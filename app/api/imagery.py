"""
Endpoints de catálogo e preview por imagem individual.

- GET /{layer}/catalog  → lista imagens disponíveis (catálogo)
- GET /{layer}/{x}/{y}/{z} → tile XYZ de uma imagem individual (por imageId)
"""
from __future__ import annotations

import asyncio
import io
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, Literal, Optional

import ee
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app.cache.cache import (
    aget_meta as get_meta,
    aset_meta as set_meta,
    aget_png as get_png,
    aset_png as set_png,
    atile_lock as tile_lock,
)
from app.core.config import logger, settings
from app.core.errors import generate_error_image
from app.middleware.rate_limiter import limit_imagery
from app.utils.http import http_get_bytes
from app.visualization.vis_params_db import get_landsat_vis_params_async
from app.visualization.vis_params_loader import get_visparams, generate_landsat_list

# --------------------------------------------------------------------------- #
# Constantes                                                                   #
# --------------------------------------------------------------------------- #

CATALOG_TTL = 12 * 3600          # 12 horas para cache de catálogo
LAYERS = {"s2_harmonized", "landsat"}
MAX_DATE_RANGE_DAYS = 540        # 18 meses
IMAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9/_\-]+$")
MIN_ZOOM, MAX_ZOOM = 3, 18

# Propriedades por coleção
S2_CLOUD_PROP = "CLOUDY_PIXEL_PERCENTAGE"
S2_EXTRA_PROPS = ["SPACECRAFT_NAME", "MGRS_TILE"]

LANDSAT_CLOUD_PROP = "CLOUD_COVER_LAND"
LANDSAT_EXTRA_PROPS = ["SPACECRAFT_ID", "WRS_PATH", "WRS_ROW", "CLOUD_COVER", "CLOUD_COVER_LAND"]

router = APIRouter()
ee_executor = ThreadPoolExecutor(max_workers=settings.MAX_WORKERS_EE)

# --------------------------------------------------------------------------- #
# Validação / utilidades                                                       #
# --------------------------------------------------------------------------- #

def _validate_layer(layer: str) -> None:
    if layer not in LAYERS:
        raise HTTPException(400, f"Layer inválida. Use: {sorted(LAYERS)}")


def _validate_coord(lat: float, lon: float) -> None:
    if not (-90 <= lat <= 90):
        raise HTTPException(400, f"lat fora do intervalo [-90, 90]: {lat}")
    if not (-180 <= lon <= 180):
        raise HTTPException(400, f"lon fora do intervalo [-180, 180]: {lon}")


def _validate_date_range(start: str, end: str) -> tuple[datetime, datetime]:
    try:
        dt_start = datetime.strptime(start, "%Y-%m-%d")
        dt_end = datetime.strptime(end, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Formato de data inválido. Use YYYY-MM-DD")
    if dt_start >= dt_end:
        raise HTTPException(400, "start deve ser anterior a end")
    delta = (dt_end - dt_start).days
    if delta > MAX_DATE_RANGE_DAYS:
        raise HTTPException(400, f"Intervalo máximo é {MAX_DATE_RANGE_DAYS} dias ({delta} fornecido)")
    return dt_start, dt_end


def _validate_image_id(image_id: str) -> str:
    if not IMAGE_ID_PATTERN.match(image_id):
        raise HTTPException(400, f"imageId contém caracteres inválidos: {image_id}")
    return image_id


def _normalize_coord(val: float) -> float:
    return round(val, 5)


def _sanitize_image_id(image_id: str) -> str:
    """Substitui / por __ para usar como parte de chave de cache."""
    return image_id.replace("/", "__")


def _point_to_region(lat: float, lon: float, buffer_meters: int) -> ee.Geometry:
    return ee.Geometry.Point([lon, lat]).buffer(buffer_meters)


def _check_zoom(z: int) -> None:
    if not (MIN_ZOOM <= z <= MAX_ZOOM):
        raise HTTPException(400, f"Zoom deve estar entre {MIN_ZOOM}-{MAX_ZOOM}")


# --------------------------------------------------------------------------- #
# Cache keys                                                                   #
# --------------------------------------------------------------------------- #

def _catalog_cache_key(
    layer: str, lat: float, lon: float, buffer_meters: int,
    start: str, end: str, sort: str, max_cloud: int,
    limit: int, offset: int,
) -> str:
    return (
        f"catalog:{layer}:{lat:.5f}_{lon:.5f}:"
        f"buf{buffer_meters}:{start}_{end}:"
        f"sort{sort}:mc{max_cloud}:l{limit}:o{offset}"
    )


def _tile_cache_key(layer: str, image_id: str, visparam: str, x: int, y: int, z: int) -> str:
    safe_id = _sanitize_image_id(image_id)
    return f"img_tile:{layer}:{safe_id}:{visparam}/{z}/{x}_{y}.png"


def _tile_meta_key(layer: str, image_id: str, visparam: str) -> str:
    safe_id = _sanitize_image_id(image_id)
    return f"img_meta:{layer}:{safe_id}:{visparam}"


# --------------------------------------------------------------------------- #
# EE builders — catálogo (sync, rodam no executor)                             #
# --------------------------------------------------------------------------- #

def _list_s2_catalog_sync(
    region: ee.Geometry, start: str, end: str,
    sort: str, max_cloud: int, limit: int, offset: int,
) -> dict:
    col = (
        ee.ImageCollection("COPERNICUS/S2_HARMONIZED")
        .filterDate(start, end)
        .filterBounds(region)
        .filter(ee.Filter.lte(S2_CLOUD_PROP, max_cloud))
    )
    total = col.size()

    if sort == "cloud_asc":
        col = col.sort(S2_CLOUD_PROP)
    else:
        col = col.sort("system:time_start", False)

    page = col.toList(limit, offset)

    def extract(img):
        img = ee.Image(img)
        props = {
            "id": img.get("system:id"),
            "time_start": img.get("system:time_start"),
            "cloud": img.get(S2_CLOUD_PROP),
        }
        for p in S2_EXTRA_PROPS:
            props[p] = img.get(p)
        return ee.Feature(None, props)

    features = page.map(extract)
    result = ee.Dictionary({"total": total, "features": ee.FeatureCollection(features)}).getInfo()
    return result


def _list_landsat_catalog_sync(
    region: ee.Geometry, start: str, end: str,
    sort: str, max_cloud: int, limit: int, offset: int,
) -> dict:
    dt_start = datetime.strptime(start, "%Y-%m-%d")
    dt_end = datetime.strptime(end, "%Y-%m-%d")
    collections = generate_landsat_list(dt_start.year, dt_end.year)
    unique_cols = sorted(set(c for _, c in collections))

    merged = None
    for col_id in unique_cols:
        c = (
            ee.ImageCollection(col_id)
            .filterDate(start, end)
            .filterBounds(region)
        )
        merged = c if merged is None else merged.merge(c)

    if merged is None:
        return {"total": 0, "features": {"type": "FeatureCollection", "features": []}}

    merged = merged.filter(ee.Filter.lte(LANDSAT_CLOUD_PROP, max_cloud))
    total = merged.size()

    if sort == "cloud_asc":
        merged = merged.sort(LANDSAT_CLOUD_PROP)
    else:
        merged = merged.sort("system:time_start", False)

    page = merged.toList(limit, offset)

    def extract(img):
        img = ee.Image(img)
        props = {
            "id": img.get("system:id"),
            "time_start": img.get("system:time_start"),
            "cloud": img.get(LANDSAT_CLOUD_PROP),
        }
        for p in LANDSAT_EXTRA_PROPS:
            props[p] = img.get(p)
        return ee.Feature(None, props)

    features = page.map(extract)
    result = ee.Dictionary({"total": total, "features": ee.FeatureCollection(features)}).getInfo()
    return result


# --------------------------------------------------------------------------- #
# EE builders — tile por imagem individual (sync, rodam no executor)           #
# --------------------------------------------------------------------------- #

def _create_s2_image_layer_sync(image_id: str, vis: dict) -> str:
    """Gera URL de tiles para uma imagem S2 individual."""
    image = ee.Image(image_id)
    if "select" in vis:
        image = image.select(*vis["select"])
    map_id = ee.data.getMapId({"image": image, **vis["visparam"]})
    return map_id["tile_fetcher"].url_format


def _create_landsat_image_layer_sync(image_id: str, vis: dict) -> str:
    """Gera URL de tiles para uma imagem Landsat individual."""
    image = ee.Image(image_id)
    # Scaling das bandas SR
    image = image.addBands(
        image.select("SR_B.").multiply(0.0000275).add(-0.2), None, True
    )
    if "bands" in vis:
        band_names = image.bandNames()
        available = band_names.filter(ee.Filter.inList("item", vis["bands"]))
        image = image.select(available)
    map_id = ee.data.getMapId({"image": image, **vis})
    return map_id["tile_fetcher"].url_format


# --------------------------------------------------------------------------- #
# Formatação de resposta do catálogo                                           #
# --------------------------------------------------------------------------- #

def _format_catalog_response(
    layer: str, raw: dict, query: dict, limit: int, offset: int,
) -> dict:
    total = raw.get("total", 0)
    fc = raw.get("features", {})
    features_list = fc.get("features", []) if isinstance(fc, dict) else []

    items = []
    for feat in features_list:
        props = feat.get("properties", {})
        image_id = props.get("id", "")
        time_start = props.get("time_start")
        cloud_val = props.get("cloud")

        dt_str = None
        if time_start:
            dt_str = datetime.utcfromtimestamp(time_start / 1000).strftime("%Y-%m-%dT%H:%M:%SZ")

        item: Dict[str, Any] = {
            "id": image_id,
            "datetime": dt_str,
            "cloud": round(cloud_val, 2) if cloud_val is not None else None,
        }

        if layer == "s2_harmonized":
            item["cloudSource"] = S2_CLOUD_PROP
            item["platform"] = props.get("SPACECRAFT_NAME")
            item["additional"] = {"mgrs_tile": props.get("MGRS_TILE")}
        else:
            cloud_land = props.get("CLOUD_COVER_LAND")
            cloud_cover = props.get("CLOUD_COVER")
            item["cloudSource"] = "CLOUD_COVER_LAND" if cloud_land is not None else "CLOUD_COVER"
            item["platform"] = props.get("SPACECRAFT_ID")
            item["additional"] = {
                "wrs_path": props.get("WRS_PATH"),
                "wrs_row": props.get("WRS_ROW"),
                "spacecraft_id": props.get("SPACECRAFT_ID"),
            }

        items.append(item)

    next_offset = offset + limit if (offset + limit) < total else None
    return {
        "layer": layer,
        "query": query,
        "total": total,
        "limit": limit,
        "offset": offset,
        "nextOffset": next_offset,
        "items": items,
    }


# --------------------------------------------------------------------------- #
# Endpoint: Catálogo                                                           #
# --------------------------------------------------------------------------- #

@router.get("/{layer}/catalog")
@limit_imagery()
async def catalog(
    layer: str,
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    start: str = Query(..., description="YYYY-MM-DD"),
    end: str = Query(..., description="YYYY-MM-DD"),
    visparam: Optional[str] = Query(None),
    bufferMeters: int = Query(1000, ge=100, le=50000),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: Literal["date_desc", "cloud_asc"] = Query("date_desc"),
    maxCloud: int = Query(100, ge=0, le=100),
):
    _validate_layer(layer)
    _validate_date_range(start, end)

    lat = _normalize_coord(lat)
    lon = _normalize_coord(lon)

    # Default visparam por layer
    if visparam is None:
        visparam = "tvi-red" if layer == "s2_harmonized" else "landsat-tvi-false"

    # Validar visparam
    visparams = await get_visparams()
    if visparam not in visparams:
        raise HTTPException(400, f"visparam inválido: {visparam}. Disponíveis: {sorted(visparams.keys())}")

    logger.info(
        f"Catalog request: layer={layer} lat={lat} lon={lon} "
        f"start={start} end={end} buffer={bufferMeters} "
        f"sort={sort} maxCloud={maxCloud} limit={limit} offset={offset}"
    )

    # Cache
    cache_key = _catalog_cache_key(layer, lat, lon, bufferMeters, start, end, sort, maxCloud, limit, offset)
    cached = await get_meta(cache_key)
    if cached:
        logger.info(f"Catalog cache HIT: {cache_key}")
        return JSONResponse(cached, headers={"X-Cache": "HIT"})

    # EE query
    region = _point_to_region(lat, lon, bufferMeters)
    loop = asyncio.get_event_loop()

    try:
        if layer == "s2_harmonized":
            raw = await loop.run_in_executor(
                ee_executor, _list_s2_catalog_sync,
                region, start, end, sort, maxCloud, limit, offset,
            )
        else:
            raw = await loop.run_in_executor(
                ee_executor, _list_landsat_catalog_sync,
                region, start, end, sort, maxCloud, limit, offset,
            )
    except Exception:
        logger.exception("Erro ao consultar catálogo EE")
        raise HTTPException(502, "Erro ao consultar Earth Engine")

    query_info = {
        "lat": lat, "lon": lon, "start": start, "end": end,
        "bufferMeters": bufferMeters, "sort": sort, "maxCloud": maxCloud,
    }
    response_data = _format_catalog_response(layer, raw, query_info, limit, offset)

    # Cache resultado
    await set_meta(cache_key, response_data, ttl=CATALOG_TTL)
    logger.info(f"Catalog cache MISS (stored): {cache_key}")

    return JSONResponse(response_data, headers={"X-Cache": "MISS"})


# --------------------------------------------------------------------------- #
# Endpoint: Tile XYZ por imagem individual                                     #
# --------------------------------------------------------------------------- #

@router.get("/{layer}/{x}/{y}/{z}")
@limit_imagery()
async def image_tile(
    layer: str,
    x: int, y: int, z: int,
    request: Request,
    imageId: str = Query(..., description="ID completo da imagem (ex: COPERNICUS/S2_HARMONIZED/...)"),
    visparam: Optional[str] = Query(None),
):
    _validate_layer(layer)
    _check_zoom(z)
    _validate_image_id(imageId)

    # Default visparam por layer
    if visparam is None:
        visparam = "tvi-red" if layer == "s2_harmonized" else "landsat-tvi-false"

    logger.info(f"Image tile request: layer={layer} imageId={imageId} visparam={visparam} x={x} y={y} z={z}")

    # 1 ▸ PNG já cacheado?
    tile_key = _tile_cache_key(layer, imageId, visparam, x, y, z)
    png_bytes = await get_png(tile_key)
    if png_bytes:
        return StreamingResponse(
            io.BytesIO(png_bytes),
            media_type="image/png",
            headers={"X-Cache": "HIT", "X-Image-Id": imageId},
        )

    # 2 ▸ Lock distribuído: evita que dois workers gerem o mesmo tile
    async with tile_lock(tile_key) as should_generate:
        if not should_generate:
            png_bytes = await get_png(tile_key)
            if png_bytes:
                return StreamingResponse(
                    io.BytesIO(png_bytes),
                    media_type="image/png",
                    headers={"X-Cache": "HIT", "X-Image-Id": imageId},
                )

        # 3 ▸ URL EE: meta cache + TTL
        meta_key = _tile_meta_key(layer, imageId, visparam)
        meta = await get_meta(meta_key)
        expired = (
            meta is None
            or (datetime.now() - datetime.fromisoformat(meta["date"])).total_seconds() / 3600
            > settings.LIFESPAN_URL
        )

        if expired:
            try:
                loop = asyncio.get_event_loop()
                if layer == "s2_harmonized":
                    vis = await _vis_param_for_s2(visparam)
                    layer_url = await loop.run_in_executor(
                        ee_executor, _create_s2_image_layer_sync, imageId, vis,
                    )
                else:
                    vis = await _vis_param_for_landsat(visparam, imageId)
                    layer_url = await loop.run_in_executor(
                        ee_executor, _create_landsat_image_layer_sync, imageId, vis,
                    )
                await set_meta(meta_key, {"url": layer_url, "date": datetime.now().isoformat()})
            except Exception as e:
                logger.exception(f"Erro ao criar layer EE para imagem {imageId}")
                return FileResponse("data/blank.png", media_type="image/png",
                                    headers={"X-Error": str(e), "X-Image-Id": imageId})
        else:
            layer_url = meta["url"]

        # 4 ▸ Download do tile remoto
        try:
            png_bytes = await http_get_bytes(layer_url.format(x=x, y=y, z=z))
            await set_png(tile_key, png_bytes)
            return StreamingResponse(
                io.BytesIO(png_bytes),
                media_type="image/png",
                headers={"X-Cache": "MISS", "X-Image-Id": imageId},
            )
        except HTTPException as exc:
            logger.exception(f"Erro ao baixar tile da imagem {imageId}")
            return StreamingResponse(
                generate_error_image(str(exc.detail)),
                media_type="image/png",
                headers={"X-Error": str(exc.detail), "X-Image-Id": imageId},
            )


# --------------------------------------------------------------------------- #
# Helpers de vis params para tiles de imagem individual                        #
# --------------------------------------------------------------------------- #

async def _vis_param_for_s2(visparam: str) -> dict:
    """Carrega vis params para Sentinel-2."""
    visparams = await get_visparams()
    vis = visparams.get(visparam)
    if vis is None:
        raise HTTPException(404, f"visparam não encontrado: {visparam}")
    return vis


async def _vis_param_for_landsat(visparam: str, image_id: str) -> dict:
    """Carrega vis params para Landsat, determinando a coleção a partir do imageId."""
    # Extrair coleção do imageId (ex: LANDSAT/LC08/C02/T1_L2/LC08_223071_20240703)
    # A coleção é tudo antes do último segmento
    parts = image_id.split("/")
    if len(parts) >= 5:
        collection = "/".join(parts[:-1])
    else:
        # Fallback: determinar pelo ano da imagem
        collection = None

    if collection:
        try:
            vis = await get_landsat_vis_params_async(visparam, collection)
        except ValueError:
            # Fallback se a coleção não for encontrada nos vis params
            logger.warning(f"Vis params não encontrado para coleção {collection}, tentando fallback")
            vis = await get_landsat_vis_params_async(visparam, "LANDSAT/LC08/C02/T1_L2")
    else:
        vis = await get_landsat_vis_params_async(visparam, "LANDSAT/LC08/C02/T1_L2")

    # Converter listas para string (formato EE)
    for key in ("min", "max", "gamma"):
        if isinstance(vis.get(key), list):
            vis[key] = ",".join(map(str, vis[key]))

    return vis
