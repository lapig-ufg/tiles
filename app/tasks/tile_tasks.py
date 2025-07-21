"""
Tile generation and processing tasks
Handles all tile-related operations including generation, mosaics, and batch processing
"""
import asyncio
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, Any, List

import aiohttp
import ee
from loguru import logger

from app.cache.cache import aset_png as set_png
from app.tasks.celery_app import celery_app
from app.visualization.vis_params_loader import get_landsat_collection, get_landsat_vis_params, VISPARAMS

# Configuration
TILE_SIZE = 256
MAX_CONCURRENT_TILES = 50
GEE_TIMEOUT = 300  # 5 minutes


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
        
        # Download and cache tile
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            tile_data = loop.run_until_complete(_download_tile(tile_url, x, y, z))
        finally:
            loop.close()
        
        # Save to cache
        cache_key = f"{layer}/{z}/{x}/{y}"
        set_png(cache_key, tile_data)
        
        return {
            "status": "success",
            "tile": f"{z}/{x}/{y}",
            "layer": layer,
            "size": len(tile_data),
            "cached_at": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error generating tile {z}/{x}/{y}: {e}")
        raise self.retry(exc=e, countdown=2 ** self.request.retries)


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
        
        # Process tiles with thread pool
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            
            for tile in tiles:
                future = executor.submit(
                    _process_single_tile,
                    tile['x'], tile['y'], tile['z'],
                    layer, params
                )
                futures.append((tile, future))
            
            # Collect results
            for tile, future in futures:
                try:
                    result = future.result(timeout=GEE_TIMEOUT)
                    results["tiles"].append(result)
                    results["successful"] += 1
                except Exception as e:
                    logger.error(f"Failed to process tile {tile}: {e}")
                    results["failed"] += 1
                    results["tiles"].append({
                        "status": "error",
                        "tile": f"{tile['z']}/{tile['x']}/{tile['y']}",
                        "error": str(e)
                    })
        
        return results
        
    except Exception as e:
        logger.error(f"Error in batch tile generation: {e}")
        raise self.retry(exc=e, countdown=60)


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
        results = []
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(
                _process_mosaic_tiles(mosaic_url, tiles, layer)
            )
        finally:
            loop.close()
        
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
        raise self.retry(exc=e, countdown=2 ** self.request.retries)


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
    )
    
    # Build image collection
    collection = (ee.ImageCollection(collection_name)
                  .filterDate(f"{year}-01-01", f"{year}-12-31")
                  .filterBounds(geom))
    
    # Apply processing
    image = collection.mosaic()
    
    # Get URL
    map_id = ee.data.getMapId({"image": image, **vis_params})
    return map_id["tile_fetcher"].url_format


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
    map_id = ee.data.getMapId({"image": image, **vis["visparam"]})
    return map_id["tile_fetcher"].url_format


def _create_landsat_mosaic(bounds: Dict[str, float], params: Dict[str, Any]) -> str:
    """Create Landsat mosaic URL for bounds"""
    return _generate_landsat_tile(bounds, params)


def _create_sentinel2_mosaic(bounds: Dict[str, float], params: Dict[str, Any]) -> str:
    """Create Sentinel-2 mosaic URL for bounds"""
    return _generate_sentinel2_tile(bounds, params)


async def _download_tile(url: str, x: int, y: int, z: int) -> bytes:
    """Download tile from URL"""
    tile_url = url.format(x=x, y=y, z=z)
    
    async with aiohttp.ClientSession() as session:
        async with session.get(tile_url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to download tile: HTTP {resp.status}")
            return await resp.read()


async def _process_mosaic_tiles(mosaic_url: str, tiles: List[Dict[str, int]], 
                               layer: str) -> List[Dict[str, Any]]:
    """Process tiles from a mosaic URL"""
    results = []
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TILES)
    
    async def process_tile(tile):
        async with semaphore:
            try:
                tile_data = await _download_tile(
                    mosaic_url, tile['x'], tile['y'], tile['z']
                )
                
                # Save to cache
                cache_key = f"{layer}/{tile['z']}/{tile['x']}/{tile['y']}"
                await set_png(cache_key, tile_data)
                
                return {
                    "status": "success",
                    "tile": f"{tile['z']}/{tile['x']}/{tile['y']}",
                    "size": len(tile_data)
                }
            except Exception as e:
                logger.error(f"Error processing tile {tile}: {e}")
                return {
                    "status": "error",
                    "tile": f"{tile['z']}/{tile['x']}/{tile['y']}",
                    "error": str(e)
                }
    
    # Process all tiles concurrently
    tasks = [process_tile(tile) for tile in tiles]
    results = await asyncio.gather(*tasks)
    
    return results


def _process_single_tile(x: int, y: int, z: int, layer: str, 
                        params: Dict[str, Any]) -> Dict[str, Any]:
    """Process a single tile synchronously (for thread pool)"""
    try:
        bounds = _get_tile_bounds(x, y, z)
        
        if layer == "landsat":
            tile_url = _generate_landsat_tile(bounds, params)
        elif layer == "sentinel2":
            tile_url = _generate_sentinel2_tile(bounds, params)
        else:
            raise ValueError(f"Unknown layer type: {layer}")
        
        # Download tile
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            tile_data = loop.run_until_complete(_download_tile(tile_url, x, y, z))
        finally:
            loop.close()
        
        # Save to cache
        cache_key = f"{layer}/{z}/{x}/{y}"
        set_png(cache_key, tile_data)
        
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