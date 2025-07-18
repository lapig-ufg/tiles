"""
Celery tasks for async tile caching by points and campaigns
"""
import asyncio
import ee
from datetime import datetime
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor
import aiohttp
import io
from loguru import logger

from app.celery_app import celery_app
from app.mongodb import get_points_collection, get_campaigns_collection
from app.tile import tile2goehashBBOX
from app.vis_params_loader import get_landsat_collection, get_landsat_vis_params, VISPARAMS
from app.cache import aset_png as set_png, aset_meta as set_meta
import motor.motor_asyncio

# Rate limiting for GEE requests
GEE_REQUEST_DELAY = 0.1  # 100ms between requests
MAX_CONCURRENT_GEE = 10  # Max concurrent GEE requests

# Tile zoom levels for caching
CACHE_ZOOM_LEVELS = [12, 13, 14]

def get_tiles_for_point(lon: float, lat: float, zoom_levels: List[int]) -> List[Dict[str, int]]:
    """Generate tile coordinates for a point at given zoom levels"""
    import math
    
    tiles = []
    for zoom in zoom_levels:
        # Convert lat/lon to tile coordinates
        lat_rad = math.radians(lat)
        n = 2 ** zoom
        x = int((lon + 180.0) / 360.0 * n)
        y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
        
        tiles.append({"x": x, "y": y, "z": zoom})
    
    return tiles

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

    landsat = (ee.ImageCollection(collection)
               .filterDate(dates["dtStart"], dates["dtEnd"])
               .filterBounds(geom)
               .map(mask_clouds)
               .map(scale)
               .select(vis["bands"])
               .mosaic())

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

async def _http_get_bytes(url: str) -> Optional[bytes]:
    """Download tile from URL"""
    try:
        async with aiohttp.ClientSession() as sess, sess.get(url) as resp:
            if resp.status == 200:
                return await resp.read()
            else:
                logger.warning(f"Failed to download tile from {url}: {resp.status}")
                return None
    except Exception as e:
        logger.error(f"Error downloading tile from {url}: {e}")
        return None

async def cache_tile_for_point(point_data: Dict[str, Any], 
                              campaign_data: Dict[str, Any],
                              tile: Dict[str, int],
                              vis_param: str,
                              year: int) -> Dict[str, Any]:
    """Cache a single tile for a point"""
    try:
        # Initialize GEE if needed
        if not ee.data._credentials:
            ee.Initialize()

        lon, lat = point_data["lon"], point_data["lat"]
        x, y, z = tile["x"], tile["y"], tile["z"]
        
        # Create geometry for the tile
        geohash, bbox = tile2goehashBBOX(x, y, z)
        geom = ee.Geometry.BBox(bbox["w"], bbox["s"], bbox["e"], bbox["n"])
        
        # Build cache paths
        image_type = campaign_data.get("imageType", "landsat")
        path_cache = f"{image_type}_MONTH_{year}_1_{vis_param}/{geohash}"
        file_cache = f"{path_cache}/{z}/{x}_{y}.png"
        
        # Build date range (using monthly period for the year)
        dates = {"dtStart": f"{year}-01-01", "dtEnd": f"{year}-12-31"}
        
        # Create layer URL
        with ThreadPoolExecutor(max_workers=1) as executor:
            loop = asyncio.get_event_loop()
            
            if image_type == "landsat":
                layer_url = await loop.run_in_executor(
                    executor, _create_landsat_layer_sync, geom, dates, vis_param
                )
            else:
                # For Sentinel-2
                vis = VISPARAMS.get(vis_param, VISPARAMS["tvi-red"])
                layer_url = await loop.run_in_executor(
                    executor, _create_s2_layer_sync, geom, dates, vis
                )
        
        # Save metadata
        await set_meta(path_cache, {
            "url": layer_url, 
            "date": datetime.now().isoformat()
        })
        
        # Download and cache tile
        tile_url = layer_url.format(x=x, y=y, z=z)
        png_bytes = await _http_get_bytes(tile_url)
        
        if png_bytes:
            await set_png(file_cache, png_bytes)
            return {
                "status": "success",
                "tile": f"{z}/{x}/{y}",
                "size": len(png_bytes),
                "vis_param": vis_param,
                "year": year
            }
        else:
            return {
                "status": "failed",
                "tile": f"{z}/{x}/{y}",
                "error": "Failed to download tile",
                "vis_param": vis_param,
                "year": year
            }
            
    except Exception as e:
        logger.exception(f"Error caching tile {z}/{x}/{y} for point: {e}")
        return {
            "status": "error",
            "tile": f"{z}/{x}/{y}",
            "error": str(e),
            "vis_param": vis_param,
            "year": year
        }

@celery_app.task(bind=True, max_retries=3)
def cache_point_async(self, point_id: str):
    """
    Cache all tiles for a specific point asynchronously
    """
    async def _cache_point():
        try:
            # Connect to MongoDB
            from app.mongodb import connect_to_mongo
            await connect_to_mongo()
            
            points_collection = await get_points_collection()
            campaigns_collection = await get_campaigns_collection()
            
            # Get point data
            point = await points_collection.find_one({"_id": point_id})
            if not point:
                raise ValueError(f"Point {point_id} not found")
            
            # Get campaign data
            campaign = await campaigns_collection.find_one({"_id": point["campaign"]})
            if not campaign:
                raise ValueError(f"Campaign {point['campaign']} not found")
            
            # Generate tiles for point
            tiles = get_tiles_for_point(point["lon"], point["lat"], CACHE_ZOOM_LEVELS)
            
            # Get parameters from campaign
            vis_params = campaign.get("visParamsEnable", [campaign.get("visParam", "landsat-tvi-false")])
            initial_year = campaign.get("initialYear", 2020)
            final_year = campaign.get("finalYear", 2024)
            
            total_tiles = 0
            successful_tiles = 0
            failed_tiles = 0
            
            # Process each year and vis_param combination
            for year in range(initial_year, final_year + 1):
                for vis_param in vis_params:
                    # Process tiles with controlled concurrency
                    semaphore = asyncio.Semaphore(MAX_CONCURRENT_GEE)
                    
                    async def cache_with_semaphore(tile):
                        async with semaphore:
                            await asyncio.sleep(GEE_REQUEST_DELAY)  # Rate limiting
                            return await cache_tile_for_point(point, campaign, tile, vis_param, year)
                    
                    # Process all tiles for this year/vis_param combination
                    tasks = [cache_with_semaphore(tile) for tile in tiles]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    
                    # Count results
                    for result in results:
                        total_tiles += 1
                        if isinstance(result, dict):
                            if result.get("status") == "success":
                                successful_tiles += 1
                            else:
                                failed_tiles += 1
                        else:
                            failed_tiles += 1
            
            # Update point cache status
            await points_collection.update_one(
                {"_id": point_id},
                {
                    "$set": {
                        "cached": True,
                        "cachedAt": datetime.now(),
                        "cachedBy": "celery-task",
                        "enhance_in_cache": 1
                    }
                }
            )
            
            return {
                "status": "completed",
                "point_id": point_id,
                "total_tiles": total_tiles,
                "successful_tiles": successful_tiles,
                "failed_tiles": failed_tiles
            }
            
        except Exception as e:
            logger.exception(f"Error caching point {point_id}: {e}")
            raise self.retry(exc=e, countdown=2 ** self.request.retries)
    
    # Run async function
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_cache_point())
    finally:
        loop.close()

@celery_app.task(bind=True, max_retries=3)
def cache_campaign_async(self, campaign_id: str, batch_size: int = 5):
    """
    Cache all tiles for all points in a campaign
    """
    async def _cache_campaign():
        try:
            # Connect to MongoDB
            from app.mongodb import connect_to_mongo
            await connect_to_mongo()
            
            points_collection = await get_points_collection()
            campaigns_collection = await get_campaigns_collection()
            
            # Get campaign data
            campaign = await campaigns_collection.find_one({"_id": campaign_id})
            if not campaign:
                raise ValueError(f"Campaign {campaign_id} not found")
            
            # Get all points for campaign
            points_cursor = points_collection.find({"campaign": campaign_id})
            points = await points_cursor.to_list(length=None)
            
            if not points:
                return {
                    "status": "completed",
                    "campaign_id": campaign_id,
                    "total_points": 0,
                    "message": "No points found for campaign"
                }
            
            # Process points in batches to avoid overwhelming the system
            total_points = len(points)
            processed_points = 0
            
            for i in range(0, total_points, batch_size):
                batch = points[i:i + batch_size]
                
                # Create tasks for batch
                tasks = []
                for point in batch:
                    task = cache_point_async.delay(point["_id"])
                    tasks.append(task)
                
                # Wait for batch to complete
                for task in tasks:
                    task.get()  # This will block until task completes
                    processed_points += 1
                
                logger.info(f"Processed {processed_points}/{total_points} points for campaign {campaign_id}")
            
            return {
                "status": "completed",
                "campaign_id": campaign_id,
                "total_points": total_points,
                "processed_points": processed_points
            }
            
        except Exception as e:
            logger.exception(f"Error caching campaign {campaign_id}: {e}")
            raise self.retry(exc=e, countdown=2 ** self.request.retries)
    
    # Run async function
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_cache_campaign())
    finally:
        loop.close()

@celery_app.task
def get_cache_status(point_id: str = None, campaign_id: str = None):
    """
    Get cache status for point or campaign
    """
    async def _get_status():
        try:
            from app.mongodb import connect_to_mongo
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
                    "cached_by": point.get("cachedBy")
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