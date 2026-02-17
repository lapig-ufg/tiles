"""
Endpoints REST para o modulo Embedding Maps.
Segue o padrao de tile serving de app/api/layers.py.
"""
from __future__ import annotations

import asyncio
import io
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

import ee
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.cache.cache import (
    aget_png as get_png,
    aset_png as set_png,
    aget_meta as get_meta,
    aset_meta as set_meta,
    atile_lock as tile_lock,
)
from app.core.config import logger, settings
from app.core.errors import generate_error_image
from app.middleware.rate_limiter import limit_embedding
from app.utils.http import http_get_bytes as _http_get_bytes

from . import repository
from .schemas import (
    ExportRequest,
    JobCreateRequest,
    JobListResponse,
    JobResponse,
    JobStatus,
    ProductResult,
    ProductType,
    StatsResponse,
)
from .service import (
    build_product_image,
    build_vis_params,
    compute_stats,
    get_map_id_from_image,
)
from .utils import (
    build_cache_key,
    build_meta_cache_key,
    compute_job_id,
    roi_to_ee_geometry,
    validate_roi,
)


router = APIRouter()

MIN_ZOOM, MAX_ZOOM = 6, 18
META_TTL = 24 * 3600       # 24h para URLs EE
PNG_TTL = 30 * 24 * 3600   # 30 dias para tiles
STATS_TTL = 7 * 24 * 3600  # 7 dias para stats

ee_executor = ThreadPoolExecutor(max_workers=settings.get("MAX_WORKERS_EE", 4))


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _job_to_response(job: dict) -> JobResponse:
    """Converte documento MongoDB em JobResponse."""
    products = []
    for p in job.get("products_results", []):
        products.append(ProductResult(
            product=p["product"],
            status=p.get("status", JobStatus.PENDING),
            tile_url_template=p.get("tile_url_template"),
            metadata=p.get("metadata", {}),
        ))

    artifacts = []
    for a in job.get("artifacts", []):
        from .schemas import ArtifactInfo, ExportFormat
        artifacts.append(ArtifactInfo(
            id=a["id"],
            filename=a["filename"],
            format=ExportFormat(a["format"]),
            size_bytes=a.get("size_bytes"),
            download_url=a.get("download_url"),
            product=ProductType(a["product"]),
            status=a.get("status", "pending"),
            created_at=a.get("created_at", job.get("created_at", datetime.utcnow())),
        ))

    return JobResponse(
        id=job["_id"],
        name=job["name"],
        description=job.get("description"),
        config=job.get("config", {}),
        status=JobStatus(job.get("status", "PENDING")),
        progress=job.get("progress", 0),
        message=job.get("message"),
        products=products,
        artifacts=artifacts,
        created_at=job.get("created_at", datetime.utcnow()),
        updated_at=job.get("updated_at", datetime.utcnow()),
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
    )


# --------------------------------------------------------------------------- #
# Job CRUD endpoints                                                           #
# --------------------------------------------------------------------------- #

@router.post("/jobs", response_model=JobResponse, status_code=201)
async def create_job(req: JobCreateRequest):
    """Cria um novo job de embedding (idempotente por hash dos params)."""
    try:
        validate_roi(req.roi)
    except ValueError as e:
        raise HTTPException(400, str(e))

    config = req.model_dump(mode="json")
    job_id = compute_job_id(config)

    existing = await repository.get_job(job_id)
    if existing:
        return _job_to_response(existing)

    products_results = [
        {"product": p.product.value, "status": JobStatus.PENDING.value, "metadata": {}}
        for p in req.products
    ]

    job_doc = {
        "_id": job_id,
        "name": req.name,
        "description": req.description,
        "config": config,
        "status": JobStatus.PENDING.value,
        "progress": 0,
        "products_results": products_results,
        "artifacts": [],
    }

    await repository.create_job(job_doc)
    created = await repository.get_job(job_id)
    return _job_to_response(created)


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: Optional[str] = None,
):
    """Lista jobs com paginacao."""
    items, total = await repository.list_jobs(limit, offset, status)
    return JobListResponse(
        items=[_job_to_response(j) for j in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    """Detalhes de um job."""
    job = await repository.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job nao encontrado")
    return _job_to_response(job)


@router.post("/jobs/{job_id}/run")
async def run_job(job_id: str):
    """Dispara execucao do job via Celery."""
    job = await repository.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job nao encontrado")

    if job["status"] not in (JobStatus.PENDING.value, JobStatus.FAILED.value):
        raise HTTPException(409, f"Job em estado {job['status']}, nao pode ser executado")

    await repository.update_job_status(job_id, JobStatus.RUNNING.value, started_at=datetime.utcnow())

    from .tasks import task_run_job
    task_run_job.delay(job_id)

    return {"message": "Job iniciado", "job_id": job_id, "status": "RUNNING"}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Cancela um job em execucao."""
    job = await repository.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job nao encontrado")

    if job["status"] != JobStatus.RUNNING.value:
        raise HTTPException(409, f"Job em estado {job['status']}, nao pode ser cancelado")

    await repository.update_job_status(job_id, JobStatus.CANCELLED.value)
    return {"message": "Job cancelado", "job_id": job_id}


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    """Deleta um job."""
    deleted = await repository.delete_job(job_id)
    if not deleted:
        raise HTTPException(404, "Job nao encontrado")
    return {"message": "Job deletado", "job_id": job_id}


# --------------------------------------------------------------------------- #
# Tile serving                                                                 #
# --------------------------------------------------------------------------- #

@router.get("/tiles/{job_id}/{z}/{x}/{y}.png")
@limit_embedding()
async def serve_tile(
    job_id: str,
    z: int, x: int, y: int,
    request: Request,
    product: str = Query(..., description="Tipo de produto (rgb_embedding, pca, clusters, magnitude, change_detection)"),
):
    """Serve tile XYZ com cache hierarquico (mesmo padrao de layers.py)."""
    t0 = time.monotonic()

    if not (MIN_ZOOM <= z <= MAX_ZOOM):
        raise HTTPException(400, f"Zoom deve estar entre {MIN_ZOOM}-{MAX_ZOOM}")

    try:
        ProductType(product)
    except ValueError:
        raise HTTPException(400, f"Produto invalido: {product}")

    job = await repository.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job nao encontrado")
    if job["status"] != JobStatus.COMPLETED.value:
        raise HTTPException(409, f"Job em estado {job['status']}, tiles disponiveis apenas quando COMPLETED")

    cache_key = build_cache_key(job_id, product, z, x, y)
    meta_key = build_meta_cache_key(job_id, product)

    # 1. PNG cacheado?
    png_bytes = await get_png(cache_key)
    if png_bytes:
        elapsed = (time.monotonic() - t0) * 1000
        return StreamingResponse(
            io.BytesIO(png_bytes),
            media_type="image/png",
            headers={"X-Cache-Status": "HIT", "X-Response-Time": f"{elapsed:.0f}ms"},
        )

    # 2. Lock distribuido
    async with tile_lock(cache_key) as should_generate:
        if not should_generate:
            png_bytes = await get_png(cache_key)
            if png_bytes:
                elapsed = (time.monotonic() - t0) * 1000
                return StreamingResponse(
                    io.BytesIO(png_bytes),
                    media_type="image/png",
                    headers={"X-Cache-Status": "HIT", "X-Response-Time": f"{elapsed:.0f}ms"},
                )

        # 3. URL EE: meta cache + TTL
        meta = await get_meta(meta_key)
        expired = (
            meta is None
            or (datetime.now() - datetime.fromisoformat(meta["date"])).total_seconds() / 3600
            > settings.get("LIFESPAN_URL", 24)
        )

        if expired:
            config = job.get("config", {})
            roi_config = config.get("roi", {})
            year = config.get("year", 2023)
            processing = config.get("processing", {})
            scale = processing.get("scale", 10)

            # Encontrar config do produto especifico
            product_cfg = {}
            for p in config.get("products", []):
                if p.get("product") == product:
                    product_cfg = p
                    break

            try:
                loop = asyncio.get_event_loop()

                def _build_and_get_url():
                    from .schemas import RoiConfig
                    roi = RoiConfig(**roi_config)
                    ee_roi = roi_to_ee_geometry(roi)
                    img = build_product_image(
                        year, ee_roi, product,
                        rgb_bands=product_cfg.get("rgb_bands"),
                        pca_components=product_cfg.get("pca_components", 3),
                        kmeans_k=product_cfg.get("kmeans_k", 8),
                        scale=scale,
                        sample_size=processing.get("sample_size", 5000),
                        year_b=product_cfg.get("year_b"),
                    )
                    vis = build_vis_params(
                        product,
                        palette=product_cfg.get("palette"),
                        vis_min=product_cfg.get("vis_min", -0.3),
                        vis_max=product_cfg.get("vis_max", 0.3),
                        kmeans_k=product_cfg.get("kmeans_k", 8),
                    )
                    return get_map_id_from_image(img, vis)

                layer_url = await loop.run_in_executor(ee_executor, _build_and_get_url)
                await set_meta(meta_key, {"url": layer_url, "date": datetime.now().isoformat()}, META_TTL)
            except Exception:
                logger.exception(f"Erro ao criar layer EE para job {job_id}, produto {product}")
                error_img = generate_error_image("Erro ao gerar tile")
                return StreamingResponse(error_img, media_type="image/png")
        else:
            layer_url = meta["url"]

        # 4. Download tile remoto
        try:
            png_bytes = await _http_get_bytes(layer_url.format(x=x, y=y, z=z))
            await set_png(cache_key, png_bytes, PNG_TTL)
            elapsed = (time.monotonic() - t0) * 1000
            return StreamingResponse(
                io.BytesIO(png_bytes),
                media_type="image/png",
                headers={"X-Cache-Status": "MISS", "X-Response-Time": f"{elapsed:.0f}ms"},
            )
        except HTTPException:
            logger.exception(f"Erro ao baixar tile para job {job_id}")
            error_img = generate_error_image("Erro ao baixar tile")
            return StreamingResponse(error_img, media_type="image/png")


# --------------------------------------------------------------------------- #
# Preview (mapId + bounds)                                                     #
# --------------------------------------------------------------------------- #

@router.get("/preview/{job_id}")
async def preview_job(job_id: str, product: str = Query("rgb_embedding")):
    """Retorna tile URL template e bounds para preview no mapa."""
    job = await repository.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job nao encontrado")
    if job["status"] != JobStatus.COMPLETED.value:
        raise HTTPException(409, "Job nao completado")

    config = job.get("config", {})
    roi_config = config.get("roi", {})

    bounds = None
    if roi_config.get("roi_type") == "bbox" and roi_config.get("bbox"):
        w, s, e, n = roi_config["bbox"]
        bounds = {"west": w, "south": s, "east": e, "north": n}

    tile_url = f"/api/embedding-maps/tiles/{job_id}/{{z}}/{{x}}/{{y}}.png?product={product}"

    return {
        "job_id": job_id,
        "product": product,
        "tile_url_template": tile_url,
        "bounds": bounds,
    }


# --------------------------------------------------------------------------- #
# Stats                                                                        #
# --------------------------------------------------------------------------- #

@router.get("/jobs/{job_id}/stats", response_model=StatsResponse)
async def get_stats(job_id: str, product: str = Query("rgb_embedding")):
    """Calcula estatisticas para um produto do job."""
    job = await repository.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job nao encontrado")
    if job["status"] != JobStatus.COMPLETED.value:
        raise HTTPException(409, "Job nao completado")

    config = job.get("config", {})
    roi_config = config.get("roi", {})
    year = config.get("year", 2023)
    processing = config.get("processing", {})
    scale = processing.get("scale", 10)

    product_cfg = {}
    for p in config.get("products", []):
        if p.get("product") == product:
            product_cfg = p
            break

    stats_cache_key = f"emb:stats:{job_id}:{product}"
    cached = await get_meta(stats_cache_key)
    if cached and cached.get("stats"):
        return StatsResponse(
            job_id=job_id,
            product=ProductType(product),
            bands=cached["stats"].get("bands", []),
            total_pixels=cached["stats"].get("total_pixels", 0),
            coverage=cached["stats"].get("coverage", 0.0),
        )

    try:
        loop = asyncio.get_event_loop()

        def _compute():
            from .schemas import RoiConfig
            roi = RoiConfig(**roi_config)
            ee_roi = roi_to_ee_geometry(roi)
            img = build_product_image(
                year, ee_roi, product,
                rgb_bands=product_cfg.get("rgb_bands"),
                pca_components=product_cfg.get("pca_components", 3),
                kmeans_k=product_cfg.get("kmeans_k", 8),
                scale=scale,
                sample_size=processing.get("sample_size", 5000),
                year_b=product_cfg.get("year_b"),
            )
            stats_dict = compute_stats(img, ee_roi, scale)
            return stats_dict.getInfo()

        raw_stats = await loop.run_in_executor(ee_executor, _compute)

        bands_info = []
        for key, val in raw_stats.items():
            if val is not None:
                bands_info.append({"band": key, "value": val})

        result = {
            "bands": bands_info,
            "total_pixels": sum(1 for v in raw_stats.values() if v is not None),
            "coverage": 1.0,
        }

        await set_meta(stats_cache_key, {"stats": result, "date": datetime.now().isoformat()}, STATS_TTL)

        return StatsResponse(
            job_id=job_id,
            product=ProductType(product),
            bands=result["bands"],
            total_pixels=result["total_pixels"],
            coverage=result["coverage"],
        )
    except Exception:
        logger.exception(f"Erro ao calcular stats para job {job_id}")
        raise HTTPException(500, "Erro ao calcular estatisticas")


# --------------------------------------------------------------------------- #
# Export                                                                        #
# --------------------------------------------------------------------------- #

@router.post("/jobs/{job_id}/export")
async def export_job(job_id: str, req: ExportRequest):
    """Dispara exportacao via Celery."""
    job = await repository.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job nao encontrado")
    if job["status"] != JobStatus.COMPLETED.value:
        raise HTTPException(409, "Job nao completado")

    from .tasks import task_export_job
    export_config = req.model_dump(mode="json")
    task_export_job.delay(job_id, export_config)

    return {"message": "Exportacao iniciada", "job_id": job_id}


@router.get("/jobs/{job_id}/artifacts")
async def list_artifacts(job_id: str):
    """Lista artefatos de um job."""
    artifacts = await repository.get_artifacts(job_id)
    return {"job_id": job_id, "artifacts": artifacts}
