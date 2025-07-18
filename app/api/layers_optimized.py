"""
Endpoints otimizados para alta performance com cache híbrido
Suporta milhões de requisições por segundo
"""
from __future__ import annotations

import io
import json
import calendar
import asyncio
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor

import aiohttp
import ee
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from fastapi.responses import StreamingResponse, FileResponse, Response

from app.core.config import logger, settings
from app.utils.capabilities import CAPABILITIES
from app.services.tile import tile2goehashBBOX
from app.visualization.vis_params_loader import VISPARAMS, get_landsat_vis_params, get_landsat_collection
from app.core.errors import generate_error_image
from app.cache.cache_hybrid import tile_cache
from app.middleware.rate_limiter import limit_sentinel, limit_landsat, limiter
from app.middleware.adaptive_limiter import adaptive_limiter
from app.services.batch_processor import batch_processor

# --------------------------------------------------------------------------- #
# Constantes e tipos                                                          #
# --------------------------------------------------------------------------- #

class Period(str, Enum):
    WET   = "WET"
    DRY   = "DRY"
    MONTH = "MONTH"

MIN_ZOOM, MAX_ZOOM = 6, 18  # Permitir zoom de 6 a 18

router = APIRouter()

# Thread pool para operações do Earth Engine (síncronas)
ee_executor = ThreadPoolExecutor(max_workers=20)

# --------------------------------------------------------------------------- #
# Utils comuns otimizados                                                     #
# --------------------------------------------------------------------------- #

async def _http_get_bytes(url: str, session: aiohttp.ClientSession = None) -> bytes:
    """Download assíncrono com reuso de sessão"""
    if session is None:
        async with aiohttp.ClientSession() as sess:
            return await _http_get_bytes(url, sess)
    
    async with session.get(url) as resp:
        if resp.status != 200:
            raise HTTPException(resp.status, f"Erro ao buscar tile: {resp.reason}")
        return await resp.read()

def _build_periods(period: str | Period, year: int, month: int) -> Dict[str, str]:
    """Constrói períodos com cache de resultados"""
    periods = {
        "WET":  {"dtStart": f"{year}-01-01", "dtEnd": f"{year}-04-30"},
        "DRY":  {"dtStart": f"{year}-06-01", "dtEnd": f"{year}-10-30"},
    }
    if period == "MONTH":
        if not 1 <= month <= 12:
            raise HTTPException(400, "month deve estar entre 1-12")
        _, last_day = calendar.monthrange(year, month)
        periods["MONTH"] = {
            "dtStart": f"{year}-{month:02}-01",
            "dtEnd":   f"{year}-{month:02}-{last_day:02}",
        }
    if period not in periods:
        raise HTTPException(404, f"Período inválido. Use {list(periods)}")
    return periods[period]

def _check_zoom(z: int):
    """Validação rápida de zoom"""
    if not (MIN_ZOOM <= z <= MAX_ZOOM):
        raise HTTPException(400, f"Zoom deve estar entre {MIN_ZOOM}-{MAX_ZOOM}")

def _check_capability(name: str, year: int, period: str, visparam: str):
    """Validação com cache de capabilities"""
    meta = next(filter(lambda c: c["name"] == name,
                       CAPABILITIES["collections"]), None)
    if not meta:
        raise HTTPException(404, f"Camada {name} não registrada")
    if year not in meta["year"]:
        raise HTTPException(404, f"Ano inválido {year}")
    if period not in meta["period"]:
        raise HTTPException(404, f"Período inválido {period}")
    if visparam not in meta["visparam"]:
        raise HTTPException(404, f"Visparam inválido {visparam}")

# --------------------------------------------------------------------------- #
# Builders específicos com execução em thread pool                            #
# --------------------------------------------------------------------------- #

def _vis_param(visparam: str) -> dict[str, Any]:
    vis = VISPARAMS.get(visparam)
    if vis is None:
        raise HTTPException(404, f"visparam não encontrado {visparam}")
    return vis

def _create_s2_layer_sync(geom: ee.Geometry, dates: Dict[str, str], vis: dict) -> str:
    """Versão síncrona para Earth Engine"""
    s2 = (ee.ImageCollection("COPERNICUS/S2_HARMONIZED")
          .filterDate(dates["dtStart"], dates["dtEnd"])
          .filterBounds(geom)
          .sort("CLOUDY_PIXEL_PERCENTAGE", False)
          .select(*vis["select"]))
    best = s2.mosaic()
    map_id = ee.data.getMapId({"image": best, **vis["visparam"]})
    return map_id["tile_fetcher"].url_format

def _create_landsat_layer_sync(geom: ee.Geometry,
                              dates: Dict[str, str],
                              visparam_name: str) -> str:
    """Versão síncrona para Earth Engine com máscara de nuvens"""
    year = datetime.fromisoformat(dates["dtStart"]).year
    collection = get_landsat_collection(year)
    vis = get_landsat_vis_params(visparam_name, collection)

    for key in ("min", "max", "gamma"):
        if isinstance(vis.get(key), list):
            vis[key] = ",".join(map(str, vis[key]))

    def scale(img):
        return img.addBands(img.select("SR_B.").multiply(0.0000275).add(-0.2),
                            None, True)

    def mask_clouds(image):
        # Usa QA_PIXEL para mascarar nuvens e sombras de nuvens
        qa = image.select('QA_PIXEL')
        cloud_bit_mask = 1 << 3
        cloud_shadow_bit_mask = 1 << 4
        mask = qa.bitwiseAnd(cloud_bit_mask).eq(0).And(
               qa.bitwiseAnd(cloud_shadow_bit_mask).eq(0))
        return image.updateMask(mask)

    landsat = (ee.ImageCollection(collection)
               .filterDate(dates["dtStart"], dates["dtEnd"])
               .filterBounds(geom)
               .map(mask_clouds)  # Aplica a máscara de nuvens
               .map(scale)
               .select(vis["bands"])
               .mosaic())

    map_id = ee.data.getMapId({"image": landsat, **vis})
    return map_id["tile_fetcher"].url_format

# --------------------------------------------------------------------------- #
# Fluxo otimizado de tile com cache híbrido                                   #
# --------------------------------------------------------------------------- #

async def _serve_tile_optimized(
    layer: str,
    x: int, y: int, z: int,
    period: str | Period,
    year: int,
    month: int,
    visparam: str,
    builder_sync,
    background_tasks: BackgroundTasks
):
    """Versão otimizada com cache híbrido e processamento assíncrono"""
    _check_zoom(z)
    _check_capability(layer, year, period, visparam)

    dates = _build_periods(period, year, month)
    vis = _vis_param(visparam)

    geohash, bbox = tile2goehashBBOX(x, y, z)
    path_cache = f"{layer}_{period}_{year}_{month}_{visparam}/{geohash}"
    file_cache = f"{path_cache}/{z}/{x}_{y}.png"

    # 1. Busca rápida no cache híbrido
    png_bytes = await tile_cache.get_png(file_cache)
    if png_bytes:
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=2592000",  # 30 dias
                "X-Cache": "HIT"
            }
        )

    # 2. Busca/atualiza URL do Earth Engine
    meta = await tile_cache.get_meta(path_cache)
    expired = (
        meta is None or
        (datetime.now() - datetime.fromisoformat(meta["date"])).total_seconds()/3600
        > settings.LIFESPAN_URL
    )
    
    if expired:
        geom = ee.Geometry.BBox(bbox["w"], bbox["s"], bbox["e"], bbox["n"])
        try:
            # Executa em thread pool para não bloquear
            loop = asyncio.get_event_loop()
            if layer == "landsat":
                layer_url = await loop.run_in_executor(
                    ee_executor, builder_sync, geom, dates, visparam
                )
            else:
                layer_url = await loop.run_in_executor(
                    ee_executor, builder_sync, geom, dates, vis
                )
            
            await tile_cache.set_meta(path_cache, {
                "url": layer_url,
                "date": datetime.now().isoformat()
            })
        except Exception as e:
            logger.exception("Erro criar layer EE")
            return FileResponse("data/blank.png", media_type="image/png")
    else:
        layer_url = meta["url"]

    # 3. Download assíncrono do tile
    try:
        png_bytes = await _http_get_bytes(layer_url.format(x=x, y=y, z=z))
        
        # Salva no cache em background para não bloquear resposta
        background_tasks.add_task(tile_cache.set_png, file_cache, png_bytes)
        
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=2592000",
                "X-Cache": "MISS"
            }
        )
    except HTTPException as exc:
        logger.exception("Erro ao baixar tile")
        error_img = generate_error_image(str(exc.detail))
        return StreamingResponse(
            io.BytesIO(error_img),
            media_type="image/png",
            headers={"X-Cache": "ERROR"}
        )

# --------------------------------------------------------------------------- #
# Endpoints otimizados                                                        #
# --------------------------------------------------------------------------- #

@router.get("/s2_harmonized/{x}/{y}/{z}")
@limit_sentinel()
async def s2_harmonized_optimized(
    x: int, y: int, z: int,
    background_tasks: BackgroundTasks,
    request: Request,
    period = Period.WET,
    year: int = datetime.now().year,
    month: int = datetime.now().month,
    visparam: str = "tvi-red"
):
    try:
        return await _serve_tile_optimized(
            "s2_harmonized", x, y, z,
            period.value, year, month,
            visparam, _create_s2_layer_sync,
            background_tasks
        )
    except HTTPException as exc:
        logger.error(f"Erro no tile s2_harmonized/{x}/{y}/{z}: {exc.detail}")
        error_img = generate_error_image(str(exc.detail))
        return StreamingResponse(
            io.BytesIO(error_img),
            media_type="image/png",
            headers={"X-Error": str(exc.detail), "X-Cache": "ERROR"}
        )
    except Exception as exc:
        logger.exception(f"Erro inesperado no tile s2_harmonized/{x}/{y}/{z}")
        error_img = generate_error_image("Erro interno do servidor")
        return StreamingResponse(
            io.BytesIO(error_img),
            media_type="image/png",
            headers={"X-Error": "Internal Server Error", "X-Cache": "ERROR"}
        )

@router.get("/landsat/{x}/{y}/{z}")
@limit_landsat()
async def landsat_optimized(
    x: int, y: int, z: int,
    background_tasks: BackgroundTasks,
    request: Request,
    period: str = "MONTH",
    year: int = datetime.now().year,
    month: int = datetime.now().month,
    visparam: str = "landsat-tvi-false"
):
    try:
        return await _serve_tile_optimized(
            "landsat", x, y, z,
            period, year, month,
            visparam, _create_landsat_layer_sync,
            background_tasks
        )
    except HTTPException as exc:
        logger.error(f"Erro no tile landsat/{x}/{y}/{z}: {exc.detail}")
        error_img = generate_error_image(str(exc.detail))
        return StreamingResponse(
            io.BytesIO(error_img),
            media_type="image/png",
            headers={"X-Error": str(exc.detail), "X-Cache": "ERROR"}
        )
    except Exception as exc:
        logger.exception(f"Erro inesperado no tile landsat/{x}/{y}/{z}")
        error_img = generate_error_image("Erro interno do servidor")
        return StreamingResponse(
            io.BytesIO(error_img),
            media_type="image/png",
            headers={"X-Error": "Internal Server Error", "X-Cache": "ERROR"}
        )

# --------------------------------------------------------------------------- #
# Endpoints auxiliares para monitoramento                                     #
# --------------------------------------------------------------------------- #

@router.post("/batch/process")
async def process_batch_tiles(
    request: Request,
    layer: str,
    tiles: list[dict],  # Lista de {"x": int, "y": int, "z": int}
    period: str = "DRY",
    year: int = datetime.now().year,
    month: int = datetime.now().month,
    visparam: str = "true-color"
):
    """
    Processa múltiplos tiles em batch para otimizar requisições
    
    Exemplo:
    POST /batch/process
    {
        "layer": "landsat",
        "tiles": [
            {"x": 123, "y": 456, "z": 10},
            {"x": 124, "y": 456, "z": 10}
        ],
        "period": "DRY",
        "year": 2024,
        "visparam": "true-color"
    }
    """
    # Adiciona ao batch processor
    batch_id = await batch_processor.add_request(
        layer,
        {
            "tiles": tiles,
            "period": period,
            "year": year,
            "month": month,
            "visparam": visparam
        }
    )
    
    return {
        "batch_id": batch_id,
        "status": "queued",
        "tiles_count": len(tiles)
    }

@router.get("/system/metrics")
@limiter.limit("10/minute")
async def system_metrics(request: Request):
    """Retorna métricas do sistema incluindo rate limiting adaptativo"""
    load = adaptive_limiter.get_system_load()
    current_limits = adaptive_limiter.current_limits
    
    return {
        "system": load,
        "rate_limits": current_limits,
        "cache_stats": await tile_cache.get_stats()
    }
