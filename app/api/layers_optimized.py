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

from app.config import logger, settings
from app.utils.capabilities import CAPABILITIES
from app.tile import tile2goehashBBOX
from app.visParam import VISPARAMS, get_landsat_vis_params, get_landsat_collection
from app.errors import generate_error_image
from app.cache_hybrid import tile_cache

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
    """Versão síncrona para Earth Engine"""
    year = datetime.fromisoformat(dates["dtStart"]).year
    collection = get_landsat_collection(year)

    vis = get_landsat_vis_params(visparam_name, collection)

    for key in ("min", "max", "gamma"):
        if isinstance(vis.get(key), list):
            vis[key] = ",".join(map(str, vis[key]))

    def scale(img):
        return img.addBands(img.select("SR_B.").multiply(0.0000275).add(-0.2),
                            None, True)

    landsat = (ee.ImageCollection(collection)
               .filterDate(dates["dtStart"], dates["dtEnd"])
               .filterBounds(geom)
               .map(scale)
               .select(vis["bands"])
               .sort("CLOUD_COVER", False)
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
async def s2_harmonized_optimized(
    x: int, y: int, z: int,
    background_tasks: BackgroundTasks,
    request: Request,
    period: Period = Period.WET,
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

@router.get("/cache/stats")
async def cache_stats():
    """Retorna estatísticas do cache para monitoramento"""
    stats = await tile_cache.get_stats()
    return stats

@router.delete("/cache/clear")
async def clear_cache(
    layer: Optional[str] = None,
    year: Optional[int] = None,
    x: Optional[int] = None,
    y: Optional[int] = None,
    z: Optional[int] = None,
    pattern: Optional[str] = None
):
    """
    Remove entradas do cache do Redis e S3 baseado nos parâmetros fornecidos.
    
    Parâmetros:
    - layer: Nome da camada (ex: 'landsat', 's2_harmonized')
    - year: Ano específico para limpar
    - x, y, z: Coordenadas específicas do tile
    - pattern: Padrão customizado para busca (use com cuidado)
    
    Exemplos:
    - DELETE /cache/clear?layer=landsat - Remove todo cache da camada landsat
    - DELETE /cache/clear?year=2023 - Remove todo cache do ano 2023
    - DELETE /cache/clear?x=123&y=456&z=10 - Remove cache de um tile específico
    - DELETE /cache/clear?layer=landsat&year=2023 - Remove cache landsat de 2023
    """
    
    deleted_count = 0
    
    # Validação dos parâmetros
    if x is not None or y is not None or z is not None:
        if not all(v is not None for v in [x, y, z]):
            raise HTTPException(
                status_code=400,
                detail="Para limpar um tile específico, forneça x, y e z"
            )
    
    # Executa limpeza baseada nos parâmetros
    try:
        if pattern:
            # Uso direto de padrão (cuidado!)
            deleted_count = await tile_cache.delete_by_pattern(pattern)
        elif x is not None and y is not None and z is not None:
            # Limpa tile específico
            deleted_count = await tile_cache.clear_cache_by_point(x, y, z)
        elif layer and year:
            # Limpa camada específica de um ano
            deleted_count = await tile_cache.delete_by_pattern(f"{layer}_*_{year}_")
        elif layer:
            # Limpa toda a camada
            deleted_count = await tile_cache.clear_cache_by_layer(layer)
        elif year:
            # Limpa todo o ano
            deleted_count = await tile_cache.clear_cache_by_year(year)
        else:
            raise HTTPException(
                status_code=400,
                detail="Forneça pelo menos um parâmetro: layer, year, x/y/z ou pattern"
            )
        
        return {
            "status": "success",
            "deleted_count": deleted_count,
            "parameters": {
                "layer": layer,
                "year": year,
                "tile": {"x": x, "y": y, "z": z} if x is not None else None,
                "pattern": pattern
            }
        }
        
    except Exception as e:
        logger.exception(f"Erro ao limpar cache: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao limpar cache: {str(e)}"
        )

@router.post("/cache/prewarm/{layer}")
async def prewarm_tiles(
    layer: str,
    background_tasks: BackgroundTasks,
    zoom_levels: list[int] = [10, 11, 12],
    bounds: dict = None
):
    """Pré-aquece tiles para uma região específica"""
    if bounds is None:
        # Brasil bounds padrão
        bounds = {
            "west": -73.9, "south": -33.7,
            "east": -34.8, "north": 5.3
        }
    
    # Adiciona tarefa de pre-warming em background
    background_tasks.add_task(
        _prewarm_region,
        layer, zoom_levels, bounds
    )
    
    return {"status": "Pre-warming iniciado", "layer": layer, "zoom_levels": zoom_levels}

async def _prewarm_region(layer: str, zoom_levels: list[int], bounds: dict):
    """Executa pre-warming de tiles em background"""
    # Implementação do pre-warming seria feita aqui
    logger.info(f"Pre-warming {layer} para zooms {zoom_levels}")
    # TODO: Implementar lógica de pre-warming