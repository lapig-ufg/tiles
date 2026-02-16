"""
Endpoints refatorados para usar DiskCache (FanoutCache)

✓ remove todas as chamadas a request.app.state.valkey
✓ usa helpers get_png / set_png / get_meta / set_meta definidos em app.cache
✓ mantém mesma interface pública dos endpoints
"""
from __future__ import annotations

import asyncio
import calendar
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Literal

import ee
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse

from app.cache.cache import (
    aget_png as get_png, aset_png as set_png,  # bytes (tile)
    aget_meta as get_meta, aset_meta as set_meta  # {"url": str, "date": iso}
)
from app.core.config import logger, settings
from app.core.errors import generate_error_image
from app.middleware.rate_limiter import limit_sentinel, limit_landsat
from app.services.tile import tile2goehashBBOX
from app.utils.capabilities import get_capabilities_provider
from app.utils.http import http_get_bytes as _http_get_bytes
from app.visualization.vis_params_db import get_landsat_vis_params_async
from app.visualization.vis_params_loader import get_visparams, get_landsat_vis_params, get_landsat_collection


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


async def _check_capability(name: str, year: int, period: str, visparam: str):
    provider = get_capabilities_provider()
    capabilities = await provider.get_capabilities()
    
    meta = next(filter(lambda c: c["name"] == name,
                       capabilities["collections"]), None)
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

async def _vis_param(visparam: str) -> dict[str, Any]:
    visparams = await get_visparams()
    vis = visparams.get(visparam)
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


def add_cloud_score_fast(image, tile_geometry):
    """Versão otimizada para scoring de nuvens usando propriedades nativas
    
    Usa uma combinação de:
    1. CLOUD_COVER_LAND (propriedade nativa, sem cálculo)
    2. Análise simplificada de QA_PIXEL apenas na área do tile
    """
    # Usar propriedade CLOUD_COVER_LAND existente como base
    # Se não existir, usar CLOUD_COVER
    cloud_cover_land = image.get('CLOUD_COVER_LAND')
    cloud_cover = image.get('CLOUD_COVER')
    
    # Use CLOUD_COVER_LAND se disponível, senão use CLOUD_COVER
    scene_cloud_score = ee.Algorithms.If(
        ee.Number(cloud_cover_land).neq(-1),
        cloud_cover_land,
        cloud_cover
    )
    
    # Análise rápida apenas na área do tile (sem buffer)
    qa = image.select('QA_PIXEL')
    
    # Máscara simplificada: apenas nuvens de alta confiança
    # Bit 3: Cloud com alta confiança (bits 8-9 = 3)
    cloud_bit = qa.bitwiseAnd(1 << 3)
    cloud_confidence = qa.rightShift(8).bitwiseAnd(3)
    high_confidence_clouds = cloud_bit.And(cloud_confidence.eq(3))
    
    # Amostragem rápida: usar scale maior para performance
    cloud_stats = high_confidence_clouds.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=tile_geometry,
        scale=300,  # 10x maior que 30m para performance
        maxPixels=1e5  # Limite menor para cálculo rápido
    )
    
    # Porcentagem de pixels com nuvem de alta confiança no tile
    tile_cloud_fraction = ee.Number(cloud_stats.get('QA_PIXEL', 0)).multiply(100)
    
    # Score combinado: 70% peso para cena inteira, 30% para tile
    # Isso balanceia performance com precisão local
    combined_score = ee.Number(scene_cloud_score).multiply(0.7).add(
        tile_cloud_fraction.multiply(0.3)
    )
    
    return image.set({
        'cloudScore': combined_score,
        'sceneCloudCover': scene_cloud_score,
        'tileCloudFraction': tile_cloud_fraction
    })


def _create_landsat_layer_sync(geom: ee.Geometry,
                               dates: Dict[str, str],
                               visparam_name: str,
                               composite_mode: str = "BEST_IMAGE") -> str:
    year = datetime.fromisoformat(dates["dtStart"]).year
    collection = get_landsat_collection(year)
    vis = get_landsat_vis_params(visparam_name, collection)

    for key in ("min", "max", "gamma"):
        if isinstance(vis.get(key), list):
            vis[key] = ",".join(map(str, vis[key]))

    def scale(img):
        return img.addBands(img.select("SR_B.").multiply(0.0000275).add(-0.2),
                            None, True)

    logger.info(f'Landsat layer creation - composite_mode: {composite_mode}, dates: {dates}')
    # Process based on composite mode
    if composite_mode == "BEST_IMAGE":
        # Best image selection mode - use native CLOUD_COVER_LAND property for performance
        # This avoids expensive pixel-level calculations
        landsat_collection = (ee.ImageCollection(collection)
                            .filterDate(dates["dtStart"], dates["dtEnd"])
                            .filterBounds(geom)
                            .filter(ee.Filter.lt('CLOUD_COVER', 40))  # Pre-filter more aggressive
                            .sort('CLOUD_COVER_LAND')  # Use native property, no calculation needed
                            .sort('CLOUD_COVER', False))  # Secondary sort by CLOUD_COVER if CLOUD_COVER_LAND is -1
        
        # Get the best image (least clouds)
        size = landsat_collection.size()
        
        def process_best_image():
            # Get the image with least clouds
            best = landsat_collection.first()
            
            # Apply cloud masking and scaling
            processed = scale(best)
            
            # Get available bands
            band_names = processed.bandNames()
            available_bands = band_names.filter(ee.Filter.inList('item', vis["bands"]))
            
            # Select bands
            return processed.select(available_bands)
        
        landsat = ee.Algorithms.If(
            size.gt(0),
            process_best_image(),
            ee.Image.constant(0).rename(['empty'])
        )
        
    else:
        # Default MOSAIC mode - original behavior
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
                landsat_collection.map(scale).select(available_bands).mosaic()
            )
            
            return processed
        
        # Only process if collection has images
        landsat = ee.Algorithms.If(
            size.gt(0),
            process_with_bands(),
            ee.Image.constant(0).rename(['empty'])
        )

    try:
        map_id = ee.data.getMapId({"image": landsat, **vis})
        return map_id["tile_fetcher"].url_format
    except ee.EEException as e:
        # Log detailed error for debugging
        error_msg = str(e)
        logger.error(f"Earth Engine error in {composite_mode} mode: {error_msg}")
        
        # Check if it's a band-related error
        if "no band named" in error_msg.lower():
            logger.error(f"Band mismatch error. Collection: {collection}, Required bands: {vis.get('bands', [])}")
            # Raise a more informative error
            raise HTTPException(
                status_code=500,
                detail=f"Band compatibility error in {composite_mode} mode. Some images in the collection do not have the required bands: {vis.get('bands', [])}. Try using MOSAIC mode instead."
            )
        else:
            # Re-raise the original error
            raise


def _create_landsat_layer_with_params(geom: ee.Geometry, dates: Dict[str, str], vis: dict, composite_mode: str = "BEST_IMAGE") -> str:
    """Create Landsat layer with pre-processed vis params (no async calls)"""
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

    # Get collection from vis params (already determined)
    year = datetime.fromisoformat(dates["dtStart"]).year
    collection = get_landsat_collection(year)
    
    # Log the composite mode and required bands
    logger.debug(f"Landsat composite mode: {composite_mode}, collection: {collection}, required bands: {vis.get('bands', [])}")
    
    # Process based on composite mode
    if composite_mode == "BEST_IMAGE":
        # Best image selection mode - use native CLOUD_COVER_LAND property for performance
        # This avoids expensive pixel-level calculations
        landsat_collection = (ee.ImageCollection(collection)
                            .filterDate(dates["dtStart"], dates["dtEnd"])
                            .filterBounds(geom)
                            .filter(ee.Filter.lt('CLOUD_COVER', 40))  # Pre-filter more aggressive
                            .sort('CLOUD_COVER_LAND')  # Use native property, no calculation needed
                            .sort('CLOUD_COVER', False))  # Secondary sort by CLOUD_COVER if CLOUD_COVER_LAND is -1
        
        # Get the best image (least clouds)
        size = landsat_collection.size()
        
        def process_best_image():
            # Required bands from vis params
            required_bands = vis["bands"]
            
            # Function to check if an image has all required bands
            def has_required_bands(image):
                band_names = image.bandNames()
                # Check if all required bands are present
                has_all = ee.List(required_bands).map(
                    lambda band: band_names.contains(band)
                ).reduce(ee.Reducer.min())
                return image.set('has_required_bands', has_all)
            
            # Filter collection to only images with required bands
            valid_collection = landsat_collection.map(has_required_bands).filter(
                ee.Filter.eq('has_required_bands', 1)
            )
            
            valid_size = valid_collection.size()
            
            # Process valid image or fallback to mosaic
            def process_valid_best():
                best = valid_collection.first()
                processed = scale(best)
                return processed.select(required_bands)
            
            # Fallback to mosaic mode if no valid images
            def fallback_to_mosaic():
                # Use mosaic approach with band checking
                # First try to get any image with the required bands
                return landsat_collection.map(scale).map(
                    lambda img: ee.Algorithms.If(
                        ee.List(required_bands).map(
                            lambda band: img.bandNames().contains(band)
                        ).reduce(ee.Reducer.min()),
                        img.select(required_bands),
                        ee.Image.constant(0).rename(['empty'])
                    )
                ).mosaic()
            
            return ee.Algorithms.If(
                valid_size.gt(0),
                process_valid_best(),
                fallback_to_mosaic()
            )
        
        landsat = ee.Algorithms.If(
            size.gt(0),
            process_best_image(),
            ee.Image.constant(0).rename(['empty'])
        )
        
    else:
        # Default MOSAIC mode - original behavior
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

    try:
        map_id = ee.data.getMapId({"image": landsat, **vis})
        return map_id["tile_fetcher"].url_format
    except ee.EEException as e:
        # Log detailed error for debugging
        error_msg = str(e)
        logger.error(f"Earth Engine error in {composite_mode} mode: {error_msg}")
        
        # Check if it's a band-related error
        if "no band named" in error_msg.lower():
            logger.error(f"Band mismatch error. Collection: {collection}, Required bands: {vis.get('bands', [])}")
            # Raise a more informative error
            raise HTTPException(
                status_code=500,
                detail=f"Band compatibility error in {composite_mode} mode. Some images in the collection do not have the required bands: {vis.get('bands', [])}. Try using MOSAIC mode instead."
            )
        else:
            # Re-raise the original error
            raise

# --------------------------------------------------------------------------- #
# Fluxo genérico de tile                                                      #
# --------------------------------------------------------------------------- #

async def _serve_tile(layer: str,
                      x: int, y: int, z: int,
                      period: str | Period,
                      year: int,
                      month: int,
                      visparam: str,
                      builder_sync,
                      composite_mode: str = None):
    _check_zoom(z)
    await _check_capability(layer, year, period, visparam)

    dates   = _build_periods(period, year, month)
    vis     = await _vis_param(visparam)

    geohash, bbox = tile2goehashBBOX(x, y, z)
    # Include composite_mode in cache path for landsat
    if layer == "landsat" and composite_mode:
        path_cache = f"{layer}_{period}_{year}_{month}_{visparam}_{composite_mode}/{geohash}"
    else:
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
                # Get Landsat vis params before entering thread executor
                year = datetime.fromisoformat(dates["dtStart"]).year
                collection = get_landsat_collection(year)
                landsat_vis = await get_landsat_vis_params_async(visparam, collection)
                
                layer_url = await loop.run_in_executor(
                    ee_executor, _create_landsat_layer_with_params, geom, dates, landsat_vis, composite_mode or "BEST_IMAGE"
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
                        period: Literal["WET", "DRY", "MONTH"] = "WET",
                        year: int = datetime.now().year,
                        month: int = datetime.now().month,
                        visparam: str = "tvi-red"):
    try:
        return await _serve_tile("s2_harmonized", x, y, z,
                                 period, year, month,
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
                  visparam: str = "landsat-tvi-false",
                  compositeMode: Literal["MOSAIC", "BEST_IMAGE"] = "BEST_IMAGE"):
    try:
        # Create a lambda to pass composite mode to the sync function
        builder = lambda geom, dates, visparam_name: _create_landsat_layer_sync(geom, dates, visparam_name, compositeMode)
        return await _serve_tile("landsat", x, y, z,
                                 period, year, month,
                                 visparam, builder,
                                 compositeMode)
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

