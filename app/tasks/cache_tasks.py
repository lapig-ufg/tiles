"""
Optimized Celery tasks for high-performance tile caching by campaigns
Features:
- Grid-based tile grouping for reduced GEE requests
- Multi-stage async pipeline
- Intelligent prioritization
- Dynamic rate limiting with circuit breaker
- Batch processing optimizations
"""
import asyncio
import ee
import math
import numpy as np
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Set
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
import aiohttp
from loguru import logger
from PIL import Image
import io
import time

from app.tasks.celery_app import celery_app
from app.core.mongodb import get_points_collection, get_campaigns_collection, get_tile_errors_collection
from app.services.tile import tile2goehashBBOX
from app.visualization.vis_params_loader import get_landsat_collection, get_landsat_vis_params, VISPARAMS
from app.cache.cache import aset_png as set_png, aset_meta as set_meta
from celery import chord, group, chain
import traceback

# Optimized GEE rate limiting
GEE_REQUEST_DELAY = 0.05  # 50ms between requests (faster)
MAX_CONCURRENT_GEE = 25   # Increased concurrent requests
GEE_THREAD_POOL_SIZE = 5  # Multiple threads for GEE URL creation

# Grid configuration for tile grouping
GRID_SIZE = 4  # 4x4 grid = 16 tiles per GEE request
MAX_GRID_SIZE = 8  # Maximum 8x8 grid for very dense areas

# Prioritization settings
PRIORITY_ZOOM_LEVELS = [12, 13]  # Priority levels
STANDARD_ZOOM_LEVELS = [14]      # Lower priority
RECENT_YEARS_PRIORITY = 2        # Last 2 years get priority

# Circuit breaker for GEE rate limiting
class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=30):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.is_open = False
    
    def call_succeeded(self):
        self.failure_count = 0
        self.is_open = False
    
    def call_failed(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.is_open = True
    
    def can_attempt(self):
        if not self.is_open:
            return True
        if time.time() - self.last_failure_time > self.recovery_timeout:
            self.is_open = False
            self.failure_count = 0
            return True
        return False

# Global circuit breaker for GEE
gee_circuit_breaker = CircuitBreaker()

async def log_tile_error(point_id: str = None, campaign_id: str = None, tile_info: Dict[str, Any] = None,
                        grid: Dict[str, Any] = None, year: int = None, vis_param: str = None,
                        image_type: str = None, error_type: str = None, error_message: str = None,
                        error_details: Dict[str, Any] = None, gee_url: str = None):
    """Log detailed tile generation errors to MongoDB"""
    try:
        errors_collection = await get_tile_errors_collection()
        
        error_doc = {
            "pointId": point_id,
            "campaignId": campaign_id,
            "tileInfo": tile_info,
            "year": year,
            "visParam": vis_param,
            "imageType": image_type,
            "errorType": error_type,
            "errorMessage": error_message,
            "errorDetails": error_details or {},
            "geeUrl": gee_url,
            "retryCount": 0,
            "createdAt": datetime.now(),
            "resolved": False,
            "gridKey": grid.get('grid_key') if grid else None,
            "context": {
                "circuitBreakerOpen": gee_circuit_breaker.is_open,
                "failureCount": gee_circuit_breaker.failure_count
            }
        }
        
        # Remove None values
        error_doc = {k: v for k, v in error_doc.items() if v is not None}
        
        await errors_collection.insert_one(error_doc)
        logger.info(f"Logged tile error: {error_type} for {tile_info or grid}")
        
    except Exception as e:
        logger.error(f"Failed to log tile error: {e}")

def get_tile_grid_bounds(tiles: List[Dict[str, int]], grid_size: int) -> List[Dict[str, Any]]:
    """Group tiles into grids for batch processing"""
    if not tiles:
        return []
    
    # Group tiles by zoom level
    tiles_by_zoom = defaultdict(list)
    for tile in tiles:
        tiles_by_zoom[tile['z']].append(tile)
    
    grids = []
    for zoom, zoom_tiles in tiles_by_zoom.items():
        # Sort tiles by x, y for efficient grouping
        sorted_tiles = sorted(zoom_tiles, key=lambda t: (t['x'], t['y']))
        
        # Create grids
        processed = set()
        for tile in sorted_tiles:
            if (tile['x'], tile['y']) in processed:
                continue
            
            # Find grid bounds
            min_x = tile['x']
            min_y = tile['y']
            max_x = min(tile['x'] + grid_size - 1, max(t['x'] for t in zoom_tiles))
            max_y = min(tile['y'] + grid_size - 1, max(t['y'] for t in zoom_tiles))
            
            # Get all tiles in this grid
            grid_tiles = []
            for x in range(min_x, max_x + 1):
                for y in range(min_y, max_y + 1):
                    if any(t['x'] == x and t['y'] == y and t['z'] == zoom for t in zoom_tiles):
                        grid_tiles.append({'x': x, 'y': y, 'z': zoom})
                        processed.add((x, y))
            
            if grid_tiles:
                # Calculate bounding box for the entire grid
                west = 360 * min_x / (2 ** zoom) - 180
                east = 360 * (max_x + 1) / (2 ** zoom) - 180
                
                n = 2 ** zoom
                north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * min_y / n))))
                south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (max_y + 1) / n))))
                
                grids.append({
                    'tiles': grid_tiles,
                    'bounds': {'west': west, 'east': east, 'north': north, 'south': south},
                    'zoom': zoom,
                    'grid_key': f"{zoom}_{min_x}_{min_y}_{max_x}_{max_y}"
                })
    
    return grids

def get_priority_score(year: int, zoom: int, point_data: Dict[str, Any]) -> int:
    """Calculate priority score for a tile/grid"""
    score = 0
    
    # Recent years get higher priority
    current_year = datetime.now().year
    if year >= current_year - RECENT_YEARS_PRIORITY:
        score += 50
    
    # Priority zoom levels
    if zoom in PRIORITY_ZOOM_LEVELS:
        score += 30
    
    # Enhanced points get priority
    if point_data.get('enhance_in_cache', 0) == 1:
        score += 20
    
    return score

async def create_gee_mosaic(grid_bounds: Dict[str, float], dates: Dict[str, str], 
                          vis_param: str, image_type: str, executor: ThreadPoolExecutor) -> Optional[str]:
    """Create a mosaic for a grid of tiles"""
    try:
        if not gee_circuit_breaker.can_attempt():
            logger.warning("GEE circuit breaker is open, skipping request")
            return None
        
        geom = ee.Geometry.BBox(
            grid_bounds['west'], 
            grid_bounds['south'], 
            grid_bounds['east'], 
            grid_bounds['north']
        )
        
        loop = asyncio.get_event_loop()
        
        # Add exponential backoff for retries
        for attempt in range(3):
            try:
                if image_type == "landsat":
                    url = await loop.run_in_executor(
                        executor, _create_landsat_layer_sync, geom, dates, vis_param
                    )
                else:
                    vis = VISPARAMS.get(vis_param, VISPARAMS["tvi-red"])
                    url = await loop.run_in_executor(
                        executor, _create_s2_layer_sync, geom, dates, vis
                    )
                
                gee_circuit_breaker.call_succeeded()
                return url
                
            except Exception as e:
                if "429" in str(e) or "rate limit" in str(e).lower():
                    gee_circuit_breaker.call_failed()
                    wait_time = (2 ** attempt) * GEE_REQUEST_DELAY
                    logger.warning(f"GEE rate limit hit, waiting {wait_time}s before retry")
                    await asyncio.sleep(wait_time)
                else:
                    # Log the error before re-raising
                    await log_tile_error(
                        grid=grid_bounds,
                        error_type="gee_error",
                        error_message=str(e),
                        error_details={"attempt": attempt + 1, "traceback": traceback.format_exc()},
                        year=int(dates["dtStart"].split("-")[0]),
                        vis_param=vis_param,
                        image_type=image_type
                    )
                    raise
        
        return None
        
    except Exception as e:
        logger.error(f"Error creating GEE mosaic: {e}")
        gee_circuit_breaker.call_failed()
        # Log general GEE error
        await log_tile_error(
            grid=grid_bounds,
            error_type="gee_error",
            error_message=str(e),
            error_details={
                "circuit_breaker_open": gee_circuit_breaker.is_open,
                "failure_count": gee_circuit_breaker.failure_count,
                "traceback": traceback.format_exc()
            }
        )
        return None

def _create_landsat_layer_sync(geom: ee.Geometry, dates: Dict[str, str], visparam_name: str) -> str:
    """Create Landsat layer URL (synchronous for use in thread pool)"""
    year = datetime.fromisoformat(dates["dtStart"]).year
    collection = get_landsat_collection(year)
    vis = get_landsat_vis_params(visparam_name, collection)

    for key in ("min", "max", "gamma"):
        if isinstance(vis.get(key), list):
            vis[key] = ",".join(map(str, vis[key]))

    def scale(img):
        return img.addBands(img.select("SR_B.").multiply(0.0000275).add(-0.2), None, True)

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

def _create_s2_layer_sync(geom: ee.Geometry, dates: Dict[str, str], vis: dict) -> str:
    """Create Sentinel-2 layer URL (synchronous for use in thread pool)"""
    s2 = (ee.ImageCollection("COPERNICUS/S2_HARMONIZED")
          .filterDate(dates["dtStart"], dates["dtEnd"])
          .filterBounds(geom)
          .sort("CLOUDY_PIXEL_PERCENTAGE", False)
          .select(*vis["select"]))
    best = s2.mosaic()
    map_id = ee.data.getMapId({"image": best, **vis["visparam"]})
    return map_id["tile_fetcher"].url_format

async def download_and_split_mosaic(url: str, grid: Dict[str, Any], 
                                  path_prefix: str) -> List[Dict[str, Any]]:
    """Download a mosaic and split it into individual tiles"""
    results = []
    
    try:
        # Download the entire grid as one image
        grid_url = url.format(
            x=grid['tiles'][0]['x'], 
            y=grid['tiles'][0]['y'], 
            z=grid['tiles'][0]['z']
        )
        
        async with aiohttp.ClientSession() as session:
            async with session.get(grid_url) as resp:
                if resp.status != 200:
                    logger.warning(f"Failed to download grid mosaic: {resp.status}")
                    return results
                
                mosaic_bytes = await resp.read()
        
        # If it's a single tile, just save it
        if len(grid['tiles']) == 1:
            tile = grid['tiles'][0]
            file_cache = f"{path_prefix}/{tile['z']}/{tile['x']}_{tile['y']}.png"
            await set_png(file_cache, mosaic_bytes)
            results.append({
                "status": "success",
                "tile": f"{tile['z']}/{tile['x']}/{tile['y']}",
                "size": len(mosaic_bytes)
            })
        else:
            # Split the mosaic into individual tiles
            # This is a simplified version - in production you'd need proper tile cutting
            # For now, fall back to individual tile downloads
            for tile in grid['tiles']:
                tile_url = url.format(x=tile['x'], y=tile['y'], z=tile['z'])
                async with aiohttp.ClientSession() as session:
                    async with session.get(tile_url) as resp:
                        if resp.status == 200:
                            tile_bytes = await resp.read()
                            file_cache = f"{path_prefix}/{tile['z']}/{tile['x']}_{tile['y']}.png"
                            await set_png(file_cache, tile_bytes)
                            results.append({
                                "status": "success",
                                "tile": f"{tile['z']}/{tile['x']}/{tile['y']}",
                                "size": len(tile_bytes)
                            })
                        else:
                            results.append({
                                "status": "failed",
                                "tile": f"{tile['z']}/{tile['x']}/{tile['y']}",
                                "error": f"HTTP {resp.status}"
                            })
        
    except Exception as e:
        logger.error(f"Error processing mosaic: {e}")
        for tile in grid['tiles']:
            results.append({
                "status": "error",
                "tile": f"{tile['z']}/{tile['x']}/{tile['y']}",
                "error": str(e)
            })
    
    return results

@celery_app.task(bind=True, max_retries=3, priority=5)
def cache_campaign_async(self, campaign_id: str, batch_size: int = 50):
    """
    Optimized campaign caching with grid-based processing
    """
    async def _cache_campaign():
        try:
            from app.core.mongodb import connect_to_mongo
            await connect_to_mongo()
            
            points_collection = await get_points_collection()
            campaigns_collection = await get_campaigns_collection()
            
            # Get campaign data
            campaign = await campaigns_collection.find_one({"_id": campaign_id})
            if not campaign:
                raise ValueError(f"Campaign {campaign_id} not found")
            
            # Get uncached points with priority
            points_cursor = points_collection.find({
                "campaign": campaign_id,
                "$or": [
                    {"cached": {"$ne": True}},
                    {"cached": {"$exists": False}}
                ]
            }).sort([("enhance_in_cache", -1)])  # Priority points first
            
            points = await points_cursor.to_list(length=None)
            
            if not points:
                return {
                    "status": "completed",
                    "campaign_id": campaign_id,
                    "message": "All points already cached"
                }
            
            # Update campaign status
            await campaigns_collection.update_one(
                {"_id": campaign_id},
                {
                    "$set": {
                        "caching_in_progress": True,
                        "caching_started_at": datetime.now(),
                        "optimization_mode": "grid-based"
                    }
                }
            )
            
            # Process points in batches
            total_batches = math.ceil(len(points) / batch_size)
            
            for batch_idx in range(total_batches):
                batch_start = batch_idx * batch_size
                batch_end = min((batch_idx + 1) * batch_size, len(points))
                batch_points = points[batch_start:batch_end]
                
                # Create optimized subtasks for this batch
                subtasks = []
                for point in batch_points:
                    subtasks.append(
                        cache_point_optimized.s(
                            point["_id"], 
                            campaign_id,
                            priority=(batch_idx == 0)  # First batch is priority
                        )
                    )
                
                # Use chord for better coordination
                batch_job = chord(subtasks)(
                    update_campaign_progress_optimized.s(campaign_id, batch_idx, total_batches)
                )
                
                # Add delay between batches to avoid overwhelming GEE
                if batch_idx < total_batches - 1:
                    await asyncio.sleep(1)
            
            return {
                "status": "scheduled",
                "campaign_id": campaign_id,
                "total_points": len(points),
                "total_batches": total_batches,
                "batch_size": batch_size
            }
            
        except Exception as e:
            logger.exception(f"Error in optimized campaign caching: {e}")
            raise self.retry(exc=e, countdown=2 ** self.request.retries)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_cache_campaign())
    finally:
        loop.close()

@celery_app.task(bind=True, max_retries=3)
def cache_point_async(self, point_id: str):
    """
    Cache all tiles for a specific point asynchronously
    """
    async def _cache_point():
        try:
            from app.core.mongodb import connect_to_mongo, get_points_collection, get_campaigns_collection
            from app.core.gee_auth import initialize_earth_engine
            
            await connect_to_mongo()
            initialize_earth_engine()
            
            points_collection = await get_points_collection()
            campaigns_collection = await get_campaigns_collection()
            
            # Get point data
            point = await points_collection.find_one({"_id": point_id})
            if not point:
                raise ValueError(f"Point {point_id} not found")
            
            # Get campaign data
            campaign_id = point.get("campaign")
            campaign = await campaigns_collection.find_one({"_id": campaign_id})
            if not campaign:
                raise ValueError(f"Campaign {campaign_id} not found")
            
            # Use optimized caching with single point
            return await _cache_point_optimized(point, campaign, priority=True)
            
        except Exception as e:
            logger.exception(f"Error caching point {point_id}: {e}")
            raise self.retry(exc=e, countdown=2 ** self.request.retries)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_cache_point())
    finally:
        loop.close()

async def _cache_point_optimized(point: Dict[str, Any], campaign: Dict[str, Any], priority: bool = False):
    """Shared logic for optimized point caching"""
    point_id = point["_id"]
    
    # Determine zoom levels based on priority
    zoom_levels = PRIORITY_ZOOM_LEVELS if priority else PRIORITY_ZOOM_LEVELS + STANDARD_ZOOM_LEVELS
    
    # Get tiles for point
    tiles = []
    for zoom in zoom_levels:
        lat_rad = math.radians(point['lat'])
        n = 2 ** zoom
        x = int((point['lon'] + 180.0) / 360.0 * n)
        y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
        tiles.append({"x": x, "y": y, "z": zoom})
    
    # Group tiles into grids
    grid_size = GRID_SIZE if not priority else min(GRID_SIZE, 2)
    grids = get_tile_grid_bounds(tiles, grid_size)
    
    # Process parameters
    vis_params = campaign.get("visParamsEnable", [campaign.get("visParam", "landsat-tvi-false")])
    initial_year = campaign.get("initialYear", 2020)
    final_year = campaign.get("finalYear", 2024)
    image_type = campaign.get("imageType", "landsat")
    
    # Sort years by priority
    years = list(range(initial_year, final_year + 1))
    years.sort(reverse=True)
    if not priority:
        years = years[:RECENT_YEARS_PRIORITY] + years[RECENT_YEARS_PRIORITY:]
    
    results = []
    
    # Process with thread pool for GEE operations
    with ThreadPoolExecutor(max_workers=GEE_THREAD_POOL_SIZE) as executor:
        for year in years:
            for vis_param in vis_params:
                dates = {"dtStart": f"{year}-01-01", "dtEnd": f"{year}-12-31"}
                
                # Process grids with semaphore for rate limiting
                semaphore = asyncio.Semaphore(MAX_CONCURRENT_GEE)
                
                async def process_grid(grid):
                    async with semaphore:
                        await asyncio.sleep(GEE_REQUEST_DELAY)
                        
                        url = await create_gee_mosaic(
                            grid['bounds'], dates, vis_param, 
                            image_type, executor
                        )
                        
                        if url:
                            path_prefix = f"{image_type}_MONTH_{year}_1_{vis_param}/{grid['grid_key']}"
                            
                            await set_meta(path_prefix, {
                                "url": url,
                                "date": datetime.now().isoformat(),
                                "grid_bounds": grid['bounds'],
                                "tiles_count": len(grid['tiles'])
                            })
                            
                            tile_results = await download_and_split_mosaic(
                                url, grid, path_prefix
                            )
                            
                            return tile_results
                        return []
                
                grid_tasks = [process_grid(grid) for grid in grids]
                grid_results = await asyncio.gather(*grid_tasks, return_exceptions=True)
                
                for result in grid_results:
                    if isinstance(result, list):
                        results.extend(result)
    
    # Update point status
    successful = sum(1 for r in results if r.get("status") == "success")
    points_collection = await get_points_collection()
    await points_collection.update_one(
        {"_id": point_id},
        {
            "$set": {
                "cached": True,
                "cachedAt": datetime.now(),
                "cachedBy": "celery-optimized",
                "cache_stats": {
                    "total_tiles": len(results),
                    "successful": successful,
                    "failed": len(results) - successful,
                    "grid_mode": True
                }
            }
        }
    )
    
    return {
        "status": "completed",
        "point_id": point_id,
        "total_tiles": len(results),
        "successful": successful,
        "optimization": "grid-based"
    }

@celery_app.task(bind=True, max_retries=3)
def cache_point_optimized(self, point_id: str, campaign_id: str, priority: bool = False):
    """
    Optimized point caching with grid processing
    """
    async def _cache_point():
        try:
            from app.core.mongodb import connect_to_mongo
            from app.core.gee_auth import initialize_earth_engine
            
            await connect_to_mongo()
            initialize_earth_engine()
            
            points_collection = await get_points_collection()
            campaigns_collection = await get_campaigns_collection()
            
            # Get point and campaign data
            point = await points_collection.find_one({"_id": point_id})
            campaign = await campaigns_collection.find_one({"_id": campaign_id})
            
            if not point or not campaign:
                raise ValueError(f"Point or campaign not found")
            
            # Determine zoom levels based on priority
            zoom_levels = PRIORITY_ZOOM_LEVELS if priority else PRIORITY_ZOOM_LEVELS + STANDARD_ZOOM_LEVELS
            
            # Get tiles for point
            tiles = []
            for zoom in zoom_levels:
                lat_rad = math.radians(point['lat'])
                n = 2 ** zoom
                x = int((point['lon'] + 180.0) / 360.0 * n)
                y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
                tiles.append({"x": x, "y": y, "z": zoom})
            
            # Group tiles into grids
            grid_size = GRID_SIZE if not priority else min(GRID_SIZE, 2)  # Smaller grids for priority
            grids = get_tile_grid_bounds(tiles, grid_size)
            
            # Process parameters
            vis_params = campaign.get("visParamsEnable", [campaign.get("visParam", "landsat-tvi-false")])
            initial_year = campaign.get("initialYear", 2020)
            final_year = campaign.get("finalYear", 2024)
            image_type = campaign.get("imageType", "landsat")
            
            # Sort years by priority (recent first)
            years = list(range(initial_year, final_year + 1))
            years.sort(reverse=True)
            if not priority:
                years = years[:RECENT_YEARS_PRIORITY] + years[RECENT_YEARS_PRIORITY:]
            
            results = []
            
            # Process with thread pool for GEE operations
            with ThreadPoolExecutor(max_workers=GEE_THREAD_POOL_SIZE) as executor:
                for year in years:
                    for vis_param in vis_params:
                        dates = {"dtStart": f"{year}-01-01", "dtEnd": f"{year}-12-31"}
                        
                        # Process grids with semaphore for rate limiting
                        semaphore = asyncio.Semaphore(MAX_CONCURRENT_GEE)
                        
                        async def process_grid(grid):
                            async with semaphore:
                                await asyncio.sleep(GEE_REQUEST_DELAY)
                                
                                # Create mosaic URL
                                url = await create_gee_mosaic(
                                    grid['bounds'], dates, vis_param, 
                                    image_type, executor
                                )
                                
                                if url:
                                    # Build cache path
                                    path_prefix = f"{image_type}_MONTH_{year}_1_{vis_param}/{grid['grid_key']}"
                                    
                                    # Save metadata
                                    await set_meta(path_prefix, {
                                        "url": url,
                                        "date": datetime.now().isoformat(),
                                        "grid_bounds": grid['bounds'],
                                        "tiles_count": len(grid['tiles'])
                                    })
                                    
                                    # Download and split mosaic
                                    tile_results = await download_and_split_mosaic(
                                        url, grid, path_prefix
                                    )
                                    
                                    return tile_results
                                return []
                        
                        # Process all grids for this year/vis_param
                        grid_tasks = [process_grid(grid) for grid in grids]
                        grid_results = await asyncio.gather(*grid_tasks, return_exceptions=True)
                        
                        for result in grid_results:
                            if isinstance(result, list):
                                results.extend(result)
            
            # Update point status
            successful = sum(1 for r in results if r.get("status") == "success")
            await points_collection.update_one(
                {"_id": point_id},
                {
                    "$set": {
                        "cached": True,
                        "cachedAt": datetime.now(),
                        "cachedBy": "celery-optimized",
                        "cache_stats": {
                            "total_tiles": len(results),
                            "successful": successful,
                            "failed": len(results) - successful,
                            "grid_mode": True
                        }
                    }
                }
            )
            
            return {
                "status": "completed",
                "point_id": point_id,
                "total_tiles": len(results),
                "successful": successful,
                "optimization": "grid-based"
            }
            
        except Exception as e:
            logger.exception(f"Error in optimized point caching: {e}")
            raise self.retry(exc=e, countdown=2 ** self.request.retries)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_cache_point())
    finally:
        loop.close()

@celery_app.task
def get_cache_status(point_id: str = None, campaign_id: str = None):
    """
    Get cache status for point or campaign
    """
    async def _get_status():
        try:
            from app.core.mongodb import connect_to_mongo
            await connect_to_mongo()
            
            points_collection = await get_points_collection()
            
            if point_id:
                point = await points_collection.find_one({"_id": point_id})
                if not point:
                    return {"error": f"Point {point_id} not found"}
                
                return {
                    "point_id": point_id,
                    "cached": point.get("cached", False),
                    "cached_at": point.get("cachedAt"),
                    "cached_by": point.get("cachedBy"),
                    "cache_stats": point.get("cache_stats", {})
                }
            
            elif campaign_id:
                # Count cached vs total points in campaign
                total_points = await points_collection.count_documents({"campaign": campaign_id})
                cached_points = await points_collection.count_documents({
                    "campaign": campaign_id,
                    "cached": True
                })
                
                return {
                    "campaign_id": campaign_id,
                    "total_points": total_points,
                    "cached_points": cached_points,
                    "cache_percentage": (cached_points / total_points * 100) if total_points > 0 else 0
                }
            
            else:
                return {"error": "Either point_id or campaign_id must be provided"}
                
        except Exception as e:
            logger.exception(f"Error getting cache status: {e}")
            return {"error": str(e)}
    
    # Run async function
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_get_status())
    finally:
        loop.close()

@celery_app.task
def update_campaign_progress_optimized(results: List[Dict], campaign_id: str, 
                                     batch_idx: int, total_batches: int):
    """
    Update campaign progress with batch information
    """
    async def _update_progress():
        try:
            from app.core.mongodb import connect_to_mongo
            await connect_to_mongo()
            
            campaigns_collection = await get_campaigns_collection()
            points_collection = await get_points_collection()
            
            # Count current progress
            cached_points = await points_collection.count_documents({
                "campaign": campaign_id,
                "cached": True
            })
            total_points = await points_collection.count_documents({"campaign": campaign_id})
            
            progress_data = {
                "cached_points": cached_points,
                "total_points": total_points,
                "cache_percentage": (cached_points / total_points * 100) if total_points > 0 else 0,
                "current_batch": batch_idx + 1,
                "total_batches": total_batches,
                "last_update": datetime.now()
            }
            
            # Mark complete if all done
            if cached_points == total_points:
                progress_data.update({
                    "caching_in_progress": False,
                    "caching_completed_at": datetime.now(),
                    "optimization_results": {
                        "grid_processing": True,
                        "average_tiles_per_batch": len(results) / len([r for r in results if r])
                    }
                })
            
            await campaigns_collection.update_one(
                {"_id": campaign_id},
                {"$set": progress_data}
            )
            
            return progress_data
            
        except Exception as e:
            logger.exception(f"Error updating optimized progress: {e}")
            return {"error": str(e)}
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_update_progress())
    finally:
        loop.close()