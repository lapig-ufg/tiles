"""
Endpoints refatorados para usar DiskCache (FanoutCache)

✓ remove todas as chamadas a request.app.state.valkey
✓ usa helpers get_png / set_png / get_meta / set_meta definidos em app.cache
✓ mantém mesma interface pública dos endpoints
"""
from __future__ import annotations

import io, json, calendar, asyncio
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor

import aiohttp
import ee
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse

from app.core.config import logger, settings
from app.utils.capabilities import CAPABILITIES
from app.services.tile import tile2goehashBBOX
from app.visualization.vis_params_loader import VISPARAMS, get_landsat_vis_params, get_landsat_collection
from app.core.errors import generate_error_image
from app.cache.cache import (
    aget_png as get_png,  aset_png as set_png,          # bytes (tile)
    aget_meta as get_meta, aset_meta as set_meta          # {"url": str, "date": iso}
)
from app.middleware.rate_limiter import limit_sentinel, limit_landsat

# --------------------------------------------------------------------------- #
# Constantes e tipos                                                          #
# --------------------------------------------------------------------------- #

class Period(str, Enum):
    WET   = "WET"
    DRY   = "DRY"
    MONTH = "MONTH"

MIN_ZOOM, MAX_ZOOM = 6, 18                     # Permitir zoom de 6 a 18

router = APIRouter()

# Thread pool para operações do Earth Engine (síncronas)
ee_executor = ThreadPoolExecutor(max_workers=settings.MAX_WORKERS_EE)


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


def _create_s2_layer_sync(geom: ee.Geometry, dates: Dict[str, str], vis: dict) -> str:
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
        qa = image.select('QA_PIXEL')
        cloud_bit_mask = 1 << 3
        cloud_shadow_bit_mask = 1 << 4
        mask = qa.bitwiseAnd(cloud_bit_mask).eq(0).And(
               qa.bitwiseAnd(cloud_shadow_bit_mask).eq(0))
        return image.updateMask(mask)

    # First get the collection and check available bands
    landsat_collection = ee.ImageCollection(collection).filterDate(dates["dtStart"], dates["dtEnd"]).filterBounds(geom)
    
    # Check if collection has images
    size = landsat_collection.size()
    
    # Only process if there are images in the collection
    def process_with_bands():
        # Get first image to check available bands
        first = landsat_collection.first()
        band_names = first.bandNames()
        
        # Check if requested bands exist
        requested_bands = vis["bands"]
        
        # Filter to only available bands
        available_bands = band_names.filter(ee.Filter.inList('item', requested_bands))
        num_available = available_bands.size()
        
        # If no requested bands are available, use default bands based on satellite
        processed = ee.Algorithms.If(
            num_available.eq(0),
            # No bands available - return empty image
            ee.Image.constant(0).rename(['empty']),
            # Process normally with available bands
            landsat_collection.map(mask_clouds).map(scale).select(available_bands).mosaic()
        )
        
        return processed
    
    # Only process if collection has images
    landsat = ee.Algorithms.If(
        size.gt(0),
        process_with_bands(),
        ee.Image.constant(0).rename(['empty'])
    )

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
                      builder_sync):
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
            loop = asyncio.get_event_loop()
            if layer == "landsat":
                 layer_url = await loop.run_in_executor(
                    ee_executor, builder_sync, geom, dates, visparam
                )
            else:
                layer_url = await loop.run_in_executor(
                    ee_executor, builder_sync, geom, dates, vis
                )
            await set_meta(path_cache, {"url": layer_url, "date": datetime.now().isoformat()})
        except Exception as e:
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
@limit_sentinel()
async def s2_harmonized(x: int, y: int, z: int,
                        request: Request,
                        period = Period.WET,
                        year: int = datetime.now().year,
                        month: int = datetime.now().month,
                        visparam: str = "tvi-red"):
    try:
        return await _serve_tile("s2_harmonized", x, y, z,
                                 period.value, year, month,
                                 visparam, _create_s2_layer_sync)
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
@limit_landsat()
async def landsat(x: int, y: int, z: int,
                  request: Request,
                  period: str = "MONTH",
                  year: int = datetime.now().year,
                  month: int = datetime.now().month,
                  visparam: str = "landsat-tvi-false"):
    try:
        return await _serve_tile("landsat", x, y, z,
                                 period, year, month,
                                 visparam, _create_landsat_layer_sync)
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

