"""
Celery tasks para processamento assincrono de embedding jobs.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import ee

from app.core.config import logger
from app.tasks.celery_app import celery_app


@celery_app.task(bind=True, queue="standard", name="embedding_maps.run_job")
def task_run_job(self, job_id: str):
    """
    Processa um job de embedding:
    1. Carrega config do MongoDB
    2. Para cada produto: build image -> getMapId -> cachear meta
    3. Atualiza status -> COMPLETED
    """
    import asyncio
    from app.core.mongodb import mongodb, connect_to_mongo
    from .repository import get_job, update_job_status, update_job_fields
    from .schemas import JobStatus, RoiConfig
    from .service import build_product_image, build_vis_params, get_map_id_from_image
    from .utils import build_meta_cache_key, roi_to_ee_geometry

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        # Conectar MongoDB se necessario
        if mongodb.database is None:
            loop.run_until_complete(connect_to_mongo())

        job = loop.run_until_complete(get_job(job_id))
        if not job:
            logger.error(f"Job {job_id} nao encontrado")
            return

        if job["status"] == JobStatus.CANCELLED.value:
            logger.info(f"Job {job_id} foi cancelado")
            return

        config = job.get("config", {})
        roi_config = config.get("roi", {})
        year = config.get("year", 2023)
        processing = config.get("processing", {})
        scale = processing.get("scale", 10)
        sample_size = processing.get("sample_size", 5000)
        products = config.get("products", [])

        roi = RoiConfig(**roi_config)
        ee_roi = roi_to_ee_geometry(roi)

        products_results = []
        total = len(products)

        for idx, product_cfg in enumerate(products):
            product_name = product_cfg.get("product")
            logger.info(f"Job {job_id}: processando produto {product_name} ({idx + 1}/{total})")

            # Verificar cancelamento
            current = loop.run_until_complete(get_job(job_id))
            if current and current["status"] == JobStatus.CANCELLED.value:
                logger.info(f"Job {job_id} cancelado durante processamento")
                return

            try:
                img = build_product_image(
                    year, ee_roi, product_name,
                    rgb_bands=product_cfg.get("rgb_bands"),
                    pca_components=product_cfg.get("pca_components", 3),
                    kmeans_k=product_cfg.get("kmeans_k", 8),
                    scale=scale,
                    sample_size=sample_size,
                    year_b=product_cfg.get("year_b"),
                )

                vis = build_vis_params(
                    product_name,
                    palette=product_cfg.get("palette"),
                    vis_min=product_cfg.get("vis_min", -0.3),
                    vis_max=product_cfg.get("vis_max", 0.3),
                    kmeans_k=product_cfg.get("kmeans_k", 8),
                )

                tile_url = get_map_id_from_image(img, vis)

                # Cachear meta no Redis (usa async via loop para evitar conflito de event loops)
                from app.cache.cache import aset_meta
                meta_key = build_meta_cache_key(job_id, product_name)
                loop.run_until_complete(
                    aset_meta(meta_key, {"url": tile_url, "date": datetime.now().isoformat()}, 24 * 3600)
                )

                tile_url_template = f"/api/embedding-maps/tiles/{job_id}/{{z}}/{{x}}/{{y}}.png?product={product_name}"

                products_results.append({
                    "product": product_name,
                    "status": JobStatus.COMPLETED.value,
                    "tile_url_template": tile_url_template,
                    "metadata": {"vis_params": vis},
                })

            except Exception as e:
                logger.exception(f"Erro no produto {product_name} do job {job_id}")
                products_results.append({
                    "product": product_name,
                    "status": JobStatus.FAILED.value,
                    "metadata": {"error": str(e)},
                })

            # Atualizar progresso
            progress = int(((idx + 1) / total) * 100)
            loop.run_until_complete(update_job_fields(
                job_id,
                progress=progress,
                products_results=products_results,
            ))

        # Verificar se todos os produtos foram processados com sucesso
        all_ok = all(p["status"] == JobStatus.COMPLETED.value for p in products_results)
        final_status = JobStatus.COMPLETED.value if all_ok else JobStatus.FAILED.value

        loop.run_until_complete(update_job_status(
            job_id, final_status,
            progress=100,
            completed_at=datetime.utcnow(),
            products_results=products_results,
            message="Processamento concluido" if all_ok else "Alguns produtos falharam",
        ))

        logger.info(f"Job {job_id} finalizado com status {final_status}")

    except Exception:
        logger.exception(f"Erro fatal no job {job_id}")
        try:
            loop.run_until_complete(update_job_status(
                job_id, JobStatus.FAILED.value,
                message="Erro interno no processamento",
            ))
        except Exception:
            logger.exception(f"Erro ao atualizar status de falha do job {job_id}")
    finally:
        loop.close()


@celery_app.task(bind=True, queue="low_priority", name="embedding_maps.export_job")
def task_export_job(self, job_id: str, export_config: dict):
    """
    Exporta resultados de um job:
    1. Para cada produto/formato solicitado, gera artefato
    2. Registra artifacts no MongoDB
    """
    import asyncio
    from app.core.mongodb import mongodb, connect_to_mongo
    from .repository import get_job, add_artifact
    from .schemas import RoiConfig
    from .service import build_product_image, quantize_for_export
    from .utils import roi_to_ee_geometry

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        if mongodb.database is None:
            loop.run_until_complete(connect_to_mongo())

        job = loop.run_until_complete(get_job(job_id))
        if not job:
            logger.error(f"Job {job_id} nao encontrado para export")
            return

        config = job.get("config", {})
        roi_config = config.get("roi", {})
        year = config.get("year", 2023)
        processing = config.get("processing", {})
        scale = export_config.get("scale") or processing.get("scale", 10)

        roi = RoiConfig(**roi_config)
        ee_roi = roi_to_ee_geometry(roi)

        products = export_config.get("products", [])
        formats = export_config.get("formats", [])

        for product_name in products:
            product_cfg = {}
            for p in config.get("products", []):
                if p.get("product") == product_name:
                    product_cfg = p
                    break

            for fmt in formats:
                artifact_id = str(uuid.uuid4())[:8]
                filename = f"{job_id}_{product_name}.{fmt.lower()}"

                try:
                    img = build_product_image(
                        year, ee_roi, product_name,
                        rgb_bands=product_cfg.get("rgb_bands"),
                        pca_components=product_cfg.get("pca_components", 3),
                        kmeans_k=product_cfg.get("kmeans_k", 8),
                        scale=scale,
                        sample_size=processing.get("sample_size", 5000),
                        year_b=product_cfg.get("year_b"),
                    )

                    if fmt in ("COG", "GeoTIFF"):
                        export_img = quantize_for_export(img) if fmt == "COG" else img
                        task = ee.batch.Export.image.toDrive(
                            image=export_img,
                            description=f"emb_{job_id}_{product_name}",
                            scale=scale,
                            region=ee_roi,
                            maxPixels=1_000_000_000,
                            fileFormat="GeoTIFF",
                        )
                        task.start()
                        logger.info(f"Export GEE iniciado: {task.id}")

                    artifact = {
                        "id": artifact_id,
                        "filename": filename,
                        "format": fmt,
                        "product": product_name,
                        "status": "processing",
                        "created_at": datetime.utcnow(),
                    }
                    loop.run_until_complete(add_artifact(job_id, artifact))

                except Exception as e:
                    logger.exception(f"Erro no export {product_name}/{fmt}")
                    artifact = {
                        "id": artifact_id,
                        "filename": filename,
                        "format": fmt,
                        "product": product_name,
                        "status": "failed",
                        "error": str(e),
                        "created_at": datetime.utcnow(),
                    }
                    loop.run_until_complete(add_artifact(job_id, artifact))

        logger.info(f"Export do job {job_id} finalizado")

    except Exception:
        logger.exception(f"Erro fatal no export do job {job_id}")
    finally:
        loop.close()
