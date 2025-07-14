"""
Endpoints refatorados para usar DiskCache (FanoutCache)

✓ remove todas as chamadas a request.app.state.valkey
✓ usa helpers get_png / set_png / get_meta / set_meta definidos em app.cache
✓ mantém mesma interface pública dos endpoints
"""
from __future__ import annotations

import io, json, calendar
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional

import aiohttp
import ee
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse

from app.config import logger, settings
from app.utils.capabilities import CAPABILITIES
from app.tile import tile2goehashBBOX
from app.visParam import VISPARAMS, get_landsat_vis_params, get_landsat_collection
from app.errors import generate_error_image
from app.cache import (
    aget_png as get_png,  aset_png as set_png,          # bytes (tile)
    aget_meta as get_meta, aset_meta as set_meta          # {"url": str, "date": iso}
)

# --------------------------------------------------------------------------- #
# Constantes e tipos                                                          #
# --------------------------------------------------------------------------- #

class Period(str, Enum):
    WET   = "WET"
    DRY   = "DRY"
    MONTH = "MONTH"

MIN_ZOOM, MAX_ZOOM = 6, 18                     # Permitir zoom de 6 a 18

router = APIRouter()

# --------------------------------------------------------------------------- #
# Utils comuns                                                                #
# --------------------------------------------------------------------------- #

async def _http_get_bytes(url: str) -> bytes:
    async with aiohttp.ClientSession() as sess, sess.get(url) as resp:
        if resp.status != 200:
            raise HTTPException(resp.status, f"Erro ao buscar tile: {resp.reason}")
        return await resp.read()


def _build_periods(period: str | Period, year: int, month: int) -> Dict[str, str]:
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
    if not (MIN_ZOOM <= z <= MAX_ZOOM):
        logger.debug(f"zoom {z} fora do intervalo [{MIN_ZOOM}, {MAX_ZOOM}]")
        raise HTTPException(400, f"Zoom deve estar entre {MIN_ZOOM}-{MAX_ZOOM}")


def _check_capability(name: str, year: int, period: str, visparam: str):
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
# Builders específicos                                                        #
# --------------------------------------------------------------------------- #

def _vis_param(visparam: str) -> dict[str, Any]:
    vis = VISPARAMS.get(visparam)
    if vis is None:
        raise HTTPException(404, f"visparam não encontrado {visparam}")
    return vis


def _create_s2_layer(geom: ee.Geometry, dates: Dict[str, str], vis: dict) -> str:
    s2 = (ee.ImageCollection("COPERNICUS/S2_HARMONIZED")
          .filterDate(dates["dtStart"], dates["dtEnd"])
          .filterBounds(geom)
          .sort("CLOUDY_PIXEL_PERCENTAGE", False)
          .select(*vis["select"]))
    best = s2.mosaic()
    map_id = ee.data.getMapId({"image": best, **vis["visparam"]})
    return map_id["tile_fetcher"].url_format


def _create_landsat_layer(geom: ee.Geometry,
                          dates: Dict[str, str],
                          visparam_name: str) -> str:
    year = datetime.fromisoformat(dates["dtStart"]).year
    collection = get_landsat_collection(year)

    vis = get_landsat_vis_params(visparam_name, collection)

    for key in ("min", "max", "gamma"):
        if isinstance(vis.get(key), list):
            vis[key] = ",".join(map(str, vis[key]))

    def scale(img):
        return img.addBands(img.select("SR_B.").multiply(0.0000275).add(-0.2),
                            None, True)

    logger.info(f"vis: {vis}")

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
# Fluxo genérico de tile                                                      #
# --------------------------------------------------------------------------- #

async def _serve_tile(layer: str,
                      x: int, y: int, z: int,
                      period: str | Period,
                      year: int,
                      month: int,
                      visparam: str,
                      builder):
    _check_zoom(z)
    _check_capability(layer, year, period, visparam)

    dates   = _build_periods(period, year, month)
    vis     = _vis_param(visparam)

    geohash, bbox = tile2goehashBBOX(x, y, z)
    path_cache = f"{layer}_{period}_{year}_{month}_{visparam}/{geohash}"
    file_cache = f"{path_cache}/{z}/{x}_{y}.png"

    # 1 ▸ PNG já cacheado?
    png_bytes = await get_png(file_cache)
    if png_bytes:
        return StreamingResponse(io.BytesIO(png_bytes), media_type="image/png")

    # 2 ▸ URL EE: meta cache + TTL
    meta      = await get_meta(path_cache)
    expired   = (
        meta is None or
        (datetime.now() - datetime.fromisoformat(meta["date"])).total_seconds()/3600
        > settings.LIFESPAN_URL
    )
    if expired:
        geom = ee.Geometry.BBox(bbox["w"], bbox["s"], bbox["e"], bbox["n"])
        try:
            layer_url = builder(geom, dates, visparam if layer == "landsat" else vis)
            await set_meta(path_cache, {"url": layer_url, "date": datetime.now().isoformat()})
        except Exception as e:                           # noqa: BLE001
            logger.exception("Erro criar layer EE")
            return FileResponse("data/blank.png", media_type="image/png")
    else:
        layer_url = meta["url"]

    # 3 ▸ Faz download do tile remoto
    try:
        png_bytes = await _http_get_bytes(layer_url.format(x=x, y=y, z=z))
        await set_png(file_cache, png_bytes)
        return StreamingResponse(io.BytesIO(png_bytes), media_type="image/png")
    except HTTPException as exc:
        logger.exception("Erro ao baixar tile")
        return StreamingResponse(generate_error_image(str(exc.detail)),
                                 media_type="image/png")

# --------------------------------------------------------------------------- #
# Endpoints                                                                   #
# --------------------------------------------------------------------------- #

@router.get("/s2_harmonized/{x}/{y}/{z}")
async def s2_harmonized(x: int, y: int, z: int,
                        request: Request,
                        period: Period = Period.WET,
                        year: int = datetime.now().year,
                        month: int = datetime.now().month,
                        visparam: str = "tvi-red"):
    try:
        return await _serve_tile("s2_harmonized", x, y, z,
                                 period.value, year, month,
                                 visparam, _create_s2_layer)
    except HTTPException as exc:
        logger.error(f"Erro no tile s2_harmonized/{x}/{y}/{z}: {exc.detail}")
        error_img = generate_error_image(str(exc.detail))
        return StreamingResponse(
            error_img,
            media_type="image/png",
            headers={"X-Error": str(exc.detail)}
        )
    except Exception as exc:
        logger.exception(f"Erro inesperado no tile s2_harmonized/{x}/{y}/{z}")
        error_img = generate_error_image("Erro interno do servidor")
        return StreamingResponse(
            error_img,
            media_type="image/png",
            headers={"X-Error": "Internal Server Error"}
        )


@router.get("/landsat/{x}/{y}/{z}")
async def landsat(x: int, y: int, z: int,
                  request: Request,
                  period: str = "MONTH",
                  year: int = datetime.now().year,
                  month: int = datetime.now().month,
                  visparam: str = "landsat-tvi-false"):
    try:
        return await _serve_tile("landsat", x, y, z,
                                 period, year, month,
                                 visparam, _create_landsat_layer)
    except HTTPException as exc:
        logger.error(f"Erro no tile landsat/{x}/{y}/{z}: {exc.detail}")
        error_img = generate_error_image(str(exc.detail))
        return StreamingResponse(
            error_img,
            media_type="image/png",
            headers={"X-Error": str(exc.detail)}
        )
    except Exception as exc:
        logger.exception(f"Erro inesperado no tile landsat/{x}/{y}/{z}")
        error_img = generate_error_image("Erro interno do servidor")
        return StreamingResponse(
            error_img,
            media_type="image/png",
            headers={"X-Error": "Internal Server Error"}
        )

# --------------------------------------------------------------------------- #
# Endpoints de gerenciamento de cache                                         #
# --------------------------------------------------------------------------- #

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
    from app.cache_hybrid import tile_cache
    
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
