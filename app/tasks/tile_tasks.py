"""
Tile generation and processing tasks
Handles all tile-related operations including generation, mosaics, and batch processing
"""
import asyncio
import hashlib
import math
import json
import urllib.request
from urllib.error import HTTPError
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, Any, List

import ee
from loguru import logger

from app.core.gee_pool import gee_retry
from app.core.gee_admission import call_gee, google_request, project_from_url
from app.utils.http import http_get_bytes
from app.utils.ee_http_sync import urlopen_without_redirects
from app.tasks.celery_app import celery_app
from app.visualization.visParam import get_landsat_collection, get_landsat_vis_params, VISPARAMS

# Configuration
TILE_SIZE = 256
MAX_CONCURRENT_TILES = 50
GEE_TIMEOUT = 300  # 5 minutes
PNG_TTL = 30 * 24 * 3600  # 30 dias

def _save_tile_to_redis(cache_key: str, tile_data: bytes):
    from app.cache.sync_cache import persist_png_sync

    persist_png_sync(cache_key, tile_data, PNG_TTL)


def _annual_tile_key(layer: str, params: Dict[str, Any], x: int, y: int, z: int,
                     mosaic_bounds: Dict[str, float] | None = None) -> str:
    rendering = {
        "year": params.get("year", datetime.now().year),
        "vis_param": params.get("vis_param", "tvi-false" if layer == "landsat" else "tvi-red"),
        "bounds": mosaic_bounds,
    }
    identity = hashlib.sha256(json.dumps(rendering, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"legacy-annual/{layer}/{identity}/{z}/{x}_{y}.png"


def _download_tile_sync(url: str) -> bytes:
    with google_request(project=project_from_url(url), background=True) as lease:
        try:
            request = urllib.request.Request(url)
            with urlopen_without_redirects(request, timeout=60) as response:
                lease.response(response.status, response.headers.get("Retry-After"))
                return response.read()
        except HTTPError as error:
            try:
                lease.response(error.code, error.headers.get("Retry-After") if error.headers else None)
            finally:
                error.close()
            raise


@celery_app.task(bind=True, max_retries=3, queue='standard')
def tile_generate(self, z: int, x: int, y: int, layer: str, 
                 params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate a single tile
    
    Args:
        z: Zoom level
        x: Tile X coordinate
        y: Tile Y coordinate
        layer: Layer type (landsat, sentinel2, etc.)
        params: Generation parameters
        
    Returns:
        Dict with tile info and status
    """
    try:
        from app.core.gee_auth import initialize_earth_engine
        initialize_earth_engine()
        
        logger.info(f"Generating tile {z}/{x}/{y} for layer {layer}")
        
        # Get tile bounds
        bounds = _get_tile_bounds(x, y, z)
        
        # Generate tile based on layer type
        if layer == "landsat":
            tile_url = _generate_landsat_tile(bounds, params)
        elif layer == "sentinel2":
            tile_url = _generate_sentinel2_tile(bounds, params)
        else:
            raise ValueError(f"Unknown layer type: {layer}")
        
        # Download tile (síncrono)
        tile_download_url = tile_url.format(x=x, y=y, z=z)
        tile_data = _download_tile_sync(tile_download_url)

        cache_key = _annual_tile_key(layer, params, x, y, z)
        _save_tile_to_redis(cache_key, tile_data)

        return {
            "status": "success",
            "tile": f"{z}/{x}/{y}",
            "layer": layer,
            "size": len(tile_data),
            "cached_at": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error generating tile {z}/{x}/{y}: {e}")
        raise self.retry(exc=e, countdown=max(2 ** self.request.retries, getattr(e, "retry_after", 0)))


@celery_app.task(bind=True, max_retries=2, queue='standard')
def tile_generate_batch(self, tiles: List[Dict[str, Any]], layer: str,
                       params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate multiple tiles in batch
    
    Args:
        tiles: List of tile coordinates [{x, y, z}, ...]
        layer: Layer type
        params: Generation parameters
        
    Returns:
        Dict with batch results
    """
    try:
        from app.core.gee_auth import initialize_earth_engine
        initialize_earth_engine()
        
        results = {
            "successful": 0,
            "failed": 0,
            "tiles": []
        }
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            for start in range(0, len(tiles), 5):
                futures = [
                    (tile, executor.submit(_process_single_tile, tile['x'], tile['y'], tile['z'], layer, params))
                    for tile in tiles[start:start + 5]
                ]
                for tile, future in futures:
                    try:
                        result = future.result(timeout=GEE_TIMEOUT)
                    except Exception as e:
                        logger.error(f"Failed to process tile {tile}: {e}")
                        result = {
                            "status": "error", "tile": f"{tile['z']}/{tile['x']}/{tile['y']}",
                            "error": str(e),
                        }
                    results["tiles"].append(result)
                    results["successful" if result.get("status") == "success" else "failed"] += 1

        return results
        
    except Exception as e:
        logger.error(f"Error in batch tile generation: {e}")
        raise self.retry(exc=e, countdown=max(60, getattr(e, "retry_after", 0)))


@celery_app.task(bind=True, max_retries=3, queue='high_priority')
def tile_generate_mosaic(self, bounds: Dict[str, float], zoom: int,
                        layer: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate a mosaic of tiles for a bounding box
    
    Args:
        bounds: Dict with west, east, north, south
        zoom: Zoom level
        layer: Layer type
        params: Generation parameters
        
    Returns:
        Dict with mosaic info
    """
    try:
        from app.core.gee_auth import initialize_earth_engine
        initialize_earth_engine()
        
        # Calculate tiles in bounds
        tiles = _get_tiles_in_bounds(bounds, zoom)
        
        logger.info(f"Generating mosaic with {len(tiles)} tiles at zoom {zoom}")
        
        # Create mosaic URL
        if layer == "landsat":
            mosaic_url = _create_landsat_mosaic(bounds, params)
        elif layer == "sentinel2":
            mosaic_url = _create_sentinel2_mosaic(bounds, params)
        else:
            raise ValueError(f"Unknown layer type: {layer}")
        
        # Process tiles from mosaic
        from app.tasks._loop import run_async
        results = run_async(
            _process_mosaic_tiles(mosaic_url, tiles, layer, params, bounds)
        )
        
        successful = sum(1 for r in results if r.get("status") == "success")
        
        return {
            "status": "completed",
            "bounds": bounds,
            "zoom": zoom,
            "total_tiles": len(tiles),
            "successful": successful,
            "failed": len(tiles) - successful,
            "mosaic_url": mosaic_url
        }
        
    except Exception as e:
        logger.error(f"Error generating mosaic: {e}")
        raise self.retry(exc=e, countdown=max(2 ** self.request.retries, getattr(e, "retry_after", 0)))


# Helper functions
def _get_tile_bounds(x: int, y: int, z: int) -> Dict[str, float]:
    """Calculate geographic bounds for a tile"""
    n = 2 ** z
    west = x / n * 360 - 180
    east = (x + 1) / n * 360 - 180
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    
    return {
        "west": west,
        "east": east,
        "north": north,
        "south": south
    }


def _get_tiles_in_bounds(bounds: Dict[str, float], zoom: int) -> List[Dict[str, int]]:
    """Get all tiles within geographic bounds"""
    n = 2 ** zoom
    
    # Calculate tile coordinates
    min_x = int((bounds['west'] + 180) / 360 * n)
    max_x = int((bounds['east'] + 180) / 360 * n)
    
    min_y = int((1 - math.log(math.tan(math.radians(bounds['north'])) + 
                              1 / math.cos(math.radians(bounds['north']))) / math.pi) / 2 * n)
    max_y = int((1 - math.log(math.tan(math.radians(bounds['south'])) + 
                              1 / math.cos(math.radians(bounds['south']))) / math.pi) / 2 * n)
    
    tiles = []
    for x in range(min_x, max_x + 1):
        for y in range(min_y, max_y + 1):
            tiles.append({"x": x, "y": y, "z": zoom})
    
    return tiles


@gee_retry()
def _generate_landsat_tile(bounds: Dict[str, float], params: Dict[str, Any]) -> str:
    """Generate Landsat tile URL"""
    geom = ee.Geometry.BBox(
        bounds['west'], bounds['south'], 
        bounds['east'], bounds['north']
    )
    
    year = params.get('year', datetime.now().year)
    collection_name = get_landsat_collection(year)
    vis_params = get_landsat_vis_params(
        params.get('vis_param', 'tvi-false'),
        collection_name
    ).copy()

    # Converter listas para strings comma-separated (requisito do GEE getMapId)
    for key in ("min", "max", "gamma"):
        if isinstance(vis_params.get(key), list):
            vis_params[key] = ",".join(map(str, vis_params[key]))

    # Build image collection
    collection = (ee.ImageCollection(collection_name)
                  .filterDate(f"{year}-01-01", f"{year}-12-31")
                  .filterBounds(geom))
    
    # Apply processing
    image = collection.mosaic()
    
    # Get URL
    map_id = call_gee(ee.data.getMapId, {"image": image, **vis_params}, background=True)
    return map_id["tile_fetcher"].url_format


@gee_retry()
def _generate_sentinel2_tile(bounds: Dict[str, float], params: Dict[str, Any]) -> str:
    """Generate Sentinel-2 tile URL"""
    geom = ee.Geometry.BBox(
        bounds['west'], bounds['south'], 
        bounds['east'], bounds['north']
    )
    
    year = params.get('year', datetime.now().year)
    vis_param_name = params.get('vis_param', 'tvi-red')
    vis = VISPARAMS.get(vis_param_name, VISPARAMS["tvi-red"])
    
    # Build collection
    s2 = (ee.ImageCollection("COPERNICUS/S2_HARMONIZED")
          .filterDate(f"{year}-01-01", f"{year}-12-31")
          .filterBounds(geom)
          .sort("CLOUDY_PIXEL_PERCENTAGE", False)
          .select(*vis["select"]))
    
    image = s2.mosaic()
    
    # Get URL
    map_id = call_gee(ee.data.getMapId, {"image": image, **vis["visparam"]}, background=True)
    return map_id["tile_fetcher"].url_format


def _create_landsat_mosaic(bounds: Dict[str, float], params: Dict[str, Any]) -> str:
    """Create Landsat mosaic URL for bounds"""
    return _generate_landsat_tile(bounds, params)


def _create_sentinel2_mosaic(bounds: Dict[str, float], params: Dict[str, Any]) -> str:
    """Create Sentinel-2 mosaic URL for bounds"""
    return _generate_sentinel2_tile(bounds, params)


async def _download_tile(url: str, x: int, y: int, z: int) -> bytes:
    tile_url = url.format(x=x, y=y, z=z)
    return await http_get_bytes(tile_url, project=project_from_url(tile_url), background=True)


async def _process_mosaic_tiles(mosaic_url: str, tiles: List[Dict[str, int]],
                               layer: str, params: Dict[str, Any] | None = None,
                               bounds: Dict[str, float] | None = None) -> List[Dict[str, Any]]:
    results = []

    async def process_tile(tile):
        try:
            tile_data = await _download_tile(mosaic_url, tile['x'], tile['y'], tile['z'])
            cache_key = _annual_tile_key(layer, params or {}, tile['x'], tile['y'], tile['z'], bounds)
            await asyncio.to_thread(_save_tile_to_redis, cache_key, tile_data)
            return {
                "status": "success",
                "tile": f"{tile['z']}/{tile['x']}/{tile['y']}",
                "size": len(tile_data),
            }
        except Exception as e:
            logger.error(f"Error processing tile {tile}: {e}")
            return {
                "status": "error",
                "tile": f"{tile['z']}/{tile['x']}/{tile['y']}",
                "error": str(e),
            }

    for start in range(0, len(tiles), MAX_CONCURRENT_TILES):
        results.extend(await asyncio.gather(*(
            process_tile(tile) for tile in tiles[start:start + MAX_CONCURRENT_TILES]
        )))
    return results


def _process_single_tile(x: int, y: int, z: int, layer: str,
                        params: Dict[str, Any]) -> Dict[str, Any]:
    """Processa um tile anual e confirma sua persistência antes de retornar sucesso."""
    try:
        bounds = _get_tile_bounds(x, y, z)

        if layer == "landsat":
            tile_url_template = _generate_landsat_tile(bounds, params)
        elif layer == "sentinel2":
            tile_url_template = _generate_sentinel2_tile(bounds, params)
        else:
            raise ValueError(f"Unknown layer type: {layer}")

        # Download tile (síncrono)
        tile_url = tile_url_template.format(x=x, y=y, z=z)
        tile_data = _download_tile_sync(tile_url)

        cache_key = _annual_tile_key(layer, params, x, y, z)
        _save_tile_to_redis(cache_key, tile_data)

        return {
            "status": "success",
            "tile": f"{z}/{x}/{y}",
            "size": len(tile_data)
        }

    except Exception as e:
        return {
            "status": "error",
            "tile": f"{z}/{x}/{y}",
            "error": str(e)
        }
