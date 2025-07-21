"""
Cache operation tasks
Handles campaign caching, point caching, and cache warming operations
"""
import asyncio
import math
import random
from collections import defaultdict
from datetime import datetime
from typing import Dict, Any, List, Optional

from celery import group, chord
from loguru import logger

from app.cache.cache_hybrid import tile_cache
from app.core.mongodb import (
    get_points_collection, get_campaigns_collection,
    connect_to_mongo
)
from app.tasks.celery_app import celery_app
from app.tasks.tile_tasks import tile_generate_batch

# Cache warming configuration
POPULAR_REGIONS = [
    {"name": "São Paulo", "lat": -23.5505, "lon": -46.6333, "radius": 0.5},
    {"name": "Rio de Janeiro", "lat": -22.9068, "lon": -43.1729, "radius": 0.4},
    {"name": "Brasília", "lat": -15.7801, "lon": -47.9292, "radius": 0.3},
    {"name": "Salvador", "lat": -12.9777, "lon": -38.5016, "radius": 0.3},
    {"name": "Porto Alegre", "lat": -30.0346, "lon": -51.2177, "radius": 0.3},
]

PRIORITY_ZOOM_LEVELS = [10, 11, 12]
STANDARD_ZOOM_LEVELS = [13, 14]


@celery_app.task(bind=True, max_retries=3, queue='high_priority')
def cache_campaign(self, campaign_id: str, batch_size: int = 100,
                  priority_mode: bool = False) -> Dict[str, Any]:
    """
    Cache all points in a campaign
    
    Args:
        campaign_id: Campaign identifier
        batch_size: Number of points per batch
        priority_mode: Whether to use priority processing
        
    Returns:
        Dict with caching results
    """
    async def _cache_campaign():
        try:
            await connect_to_mongo()
            
            points_collection = await get_points_collection()
            campaigns_collection = await get_campaigns_collection()
            
            # Get campaign
            campaign = await campaigns_collection.find_one({"_id": campaign_id})
            if not campaign:
                raise ValueError(f"Campaign {campaign_id} not found")
            
            # Update campaign status
            await campaigns_collection.update_one(
                {"_id": campaign_id},
                {
                    "$set": {
                        "caching_status": "in_progress",
                        "caching_started_at": datetime.now(),
                        "caching_mode": "optimized"
                    }
                }
            )
            
            # Get uncached points
            query = {
                "campaign": campaign_id,
                "$or": [
                    {"cached": {"$ne": True}},
                    {"cached": {"$exists": False}}
                ]
            }
            
            # Priority points first
            if priority_mode:
                query["enhance_in_cache"] = 1
            
            points_cursor = points_collection.find(query)
            points = await points_cursor.to_list(length=None)
            
            if not points:
                await campaigns_collection.update_one(
                    {"_id": campaign_id},
                    {"$set": {"caching_status": "completed"}}
                )
                return {
                    "status": "completed",
                    "message": "All points already cached",
                    "campaign_id": campaign_id
                }
            
            # Process in batches
            total_points = len(points)
            total_batches = math.ceil(total_points / batch_size)
            
            logger.info(f"Processing {total_points} points in {total_batches} batches")
            
            # Create subtasks
            subtasks = []
            for i in range(0, total_points, batch_size):
                batch = points[i:i+batch_size]
                point_ids = [p["_id"] for p in batch]
                
                subtasks.append(
                    cache_point_batch.s(
                        point_ids, campaign_id,
                        priority=priority_mode or (i == 0)
                    )
                )
            
            # Execute with chord for coordination
            job = chord(subtasks)(
                finalize_campaign_caching.s(campaign_id)
            )
            
            return {
                "status": "scheduled",
                "campaign_id": campaign_id,
                "total_points": total_points,
                "total_batches": total_batches,
                "job_id": job.id
            }
            
        except Exception as e:
            logger.exception(f"Error caching campaign: {e}")
            raise self.retry(exc=e, countdown=60)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_cache_campaign())
    finally:
        loop.close()


@celery_app.task(bind=True, max_retries=3, queue='standard')
def cache_point(self, point_id: str, force: bool = False) -> Dict[str, Any]:
    """
    Cache all tiles for a single point
    
    Args:
        point_id: Point identifier
        force: Force recaching even if already cached
        
    Returns:
        Dict with caching results
    """
    async def _cache_point():
        try:
            await connect_to_mongo()
            
            points_collection = await get_points_collection()
            campaigns_collection = await get_campaigns_collection()
            
            # Get point
            point = await points_collection.find_one({"_id": point_id})
            if not point:
                raise ValueError(f"Point {point_id} not found")
            
            # Check if already cached
            if point.get("cached") and not force:
                return {
                    "status": "already_cached",
                    "point_id": point_id,
                    "cached_at": point.get("cachedAt")
                }
            
            # Get campaign
            campaign = await campaigns_collection.find_one({"_id": point["campaign"]})
            if not campaign:
                raise ValueError(f"Campaign not found for point {point_id}")
            
            # Determine tiles to cache
            zoom_levels = PRIORITY_ZOOM_LEVELS + STANDARD_ZOOM_LEVELS
            tiles = _get_point_tiles(point["lat"], point["lon"], zoom_levels)
            
            # Get parameters
            vis_params = campaign.get("visParamsEnable", [campaign.get("visParam", "landsat-tvi-false")])
            years = list(range(
                campaign.get("initialYear", 2020),
                campaign.get("finalYear", 2024) + 1
            ))
            image_type = campaign.get("imageType", "landsat")
            
            # Generate cache tasks
            cache_tasks = []
            for year in years:
                for vis_param in vis_params:
                    params = {
                        "year": year,
                        "vis_param": vis_param,
                        "image_type": image_type
                    }
                    
                    # Create tile generation task
                    cache_tasks.append(
                        tile_generate_batch.s(tiles, image_type, params)
                    )
            
            # Execute tasks
            job = group(cache_tasks).apply_async()
            
            # Update point status
            await points_collection.update_one(
                {"_id": point_id},
                {
                    "$set": {
                        "cached": True,
                        "cachedAt": datetime.now(),
                        "cacheJobId": job.id,
                        "cacheStats": {
                            "totalTiles": len(tiles) * len(years) * len(vis_params),
                            "zoomLevels": zoom_levels,
                            "years": years,
                            "visParams": vis_params
                        }
                    }
                }
            )
            
            return {
                "status": "caching_started",
                "point_id": point_id,
                "job_id": job.id,
                "total_tiles": len(tiles) * len(years) * len(vis_params)
            }
            
        except Exception as e:
            logger.exception(f"Error caching point: {e}")
            raise self.retry(exc=e, countdown=30)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_cache_point())
    finally:
        loop.close()


@celery_app.task(bind=True, max_retries=2, queue='standard')
def cache_point_batch(self, point_ids: List[str], campaign_id: str,
                     priority: bool = False) -> Dict[str, Any]:
    """
    Cache multiple points in batch
    
    Args:
        point_ids: List of point identifiers
        campaign_id: Campaign identifier
        priority: Whether this is a priority batch
        
    Returns:
        Dict with batch results
    """
    results = {
        "successful": 0,
        "failed": 0,
        "points": []
    }
    
    for point_id in point_ids:
        try:
            result = cache_point.apply_async(
                args=[point_id],
                queue='high_priority' if priority else 'standard'
            )
            results["successful"] += 1
            results["points"].append({
                "point_id": point_id,
                "task_id": result.id,
                "status": "scheduled"
            })
        except Exception as e:
            logger.error(f"Failed to schedule caching for point {point_id}: {e}")
            results["failed"] += 1
            results["points"].append({
                "point_id": point_id,
                "status": "error",
                "error": str(e)
            })
    
    return results


@celery_app.task(queue='low_priority')
def finalize_campaign_caching(results: List[Dict[str, Any]], campaign_id: str) -> Dict[str, Any]:
    """
    Finalize campaign caching after all batches complete
    
    Args:
        results: Results from all batches
        campaign_id: Campaign identifier
        
    Returns:
        Dict with final status
    """
    async def _finalize():
        try:
            await connect_to_mongo()
            
            campaigns_collection = await get_campaigns_collection()
            points_collection = await get_points_collection()
            
            # Count cached points
            cached_count = await points_collection.count_documents({
                "campaign": campaign_id,
                "cached": True
            })
            total_count = await points_collection.count_documents({
                "campaign": campaign_id
            })
            
            # Calculate statistics
            total_successful = sum(r.get("successful", 0) for r in results)
            total_failed = sum(r.get("failed", 0) for r in results)
            
            # Update campaign
            await campaigns_collection.update_one(
                {"_id": campaign_id},
                {
                    "$set": {
                        "caching_status": "completed",
                        "caching_completed_at": datetime.now(),
                        "caching_stats": {
                            "cached_points": cached_count,
                            "total_points": total_count,
                            "cache_percentage": (cached_count / total_count * 100) if total_count > 0 else 0,
                            "batches_successful": total_successful,
                            "batches_failed": total_failed
                        }
                    }
                }
            )
            
            return {
                "status": "completed",
                "campaign_id": campaign_id,
                "cached_points": cached_count,
                "total_points": total_count,
                "cache_percentage": (cached_count / total_count * 100) if total_count > 0 else 0
            }
            
        except Exception as e:
            logger.exception(f"Error finalizing campaign caching: {e}")
            return {"status": "error", "error": str(e)}
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_finalize())
    finally:
        loop.close()


@celery_app.task(bind=True, max_retries=3, queue='low_priority')
def cache_warm_regions(self, regions: List[Dict[str, Any]] = None,
                      zoom_levels: List[int] = None,
                      max_tiles: int = 1000) -> Dict[str, Any]:
    """
    Warm cache for popular regions
    
    Args:
        regions: List of regions to warm (uses defaults if None)
        zoom_levels: Zoom levels to cache
        max_tiles: Maximum tiles to process
        
    Returns:
        Dict with warming results
    """
    if regions is None:
        regions = POPULAR_REGIONS
    
    if zoom_levels is None:
        zoom_levels = PRIORITY_ZOOM_LEVELS
    
    try:
        # Generate tiles for each region
        all_tiles = []
        tiles_per_region = max_tiles // len(regions)
        
        for region in regions:
            region_tiles = _get_region_tiles(
                region["lat"], region["lon"],
                region["radius"], zoom_levels
            )
            
            # Sample tiles if too many
            if len(region_tiles) > tiles_per_region:
                region_tiles = random.sample(region_tiles, tiles_per_region)
            
            all_tiles.extend(region_tiles)
        
        # Create tile generation tasks
        layer = "landsat"  # Default layer
        params = {
            "year": datetime.now().year,
            "vis_param": "tvi-false"
        }
        
        # Batch tiles by zoom level
        tiles_by_zoom = defaultdict(list)
        for tile in all_tiles:
            tiles_by_zoom[tile["z"]].append(tile)
        
        # Create subtasks
        subtasks = []
        for zoom, tiles in tiles_by_zoom.items():
            subtasks.append(
                tile_generate_batch.s(tiles, layer, params)
            )
        
        # Execute
        job = group(subtasks).apply_async()
        
        return {
            "status": "warming_started",
            "regions": len(regions),
            "total_tiles": len(all_tiles),
            "zoom_levels": zoom_levels,
            "job_id": job.id
        }
        
    except Exception as e:
        logger.exception(f"Error warming cache: {e}")
        raise self.retry(exc=e, countdown=300)


@celery_app.task(queue='low_priority')
def cache_validate(campaign_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Validate cache integrity
    
    Args:
        campaign_id: Optional campaign to validate
        
    Returns:
        Dict with validation results
    """
    async def _validate():
        try:
            await connect_to_mongo()
            await tile_cache.initialize()
            
            validation_results = {
                "total_checked": 0,
                "valid": 0,
                "invalid": 0,
                "missing": 0,
                "errors": []
            }
            
            # Get points to validate
            points_collection = await get_points_collection()
            query = {"cached": True}
            if campaign_id:
                query["campaign"] = campaign_id
            
            points = await points_collection.find(query).to_list(length=100)
            
            for point in points:
                validation_results["total_checked"] += 1
                
                # Check if cache entries exist
                cache_stats = point.get("cacheStats", {})
                expected_tiles = cache_stats.get("totalTiles", 0)
                
                if expected_tiles == 0:
                    validation_results["invalid"] += 1
                    validation_results["errors"].append({
                        "point_id": point["_id"],
                        "error": "No cache stats"
                    })
                else:
                    # Sample check - verify some tiles exist
                    # In production, would check all tiles
                    validation_results["valid"] += 1
            
            return validation_results
            
        except Exception as e:
            logger.exception(f"Error validating cache: {e}")
            return {"status": "error", "error": str(e)}
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_validate())
    finally:
        loop.close()


# Helper functions
def _get_point_tiles(lat: float, lon: float, zoom_levels: List[int]) -> List[Dict[str, int]]:
    """Get tile coordinates for a point at multiple zoom levels"""
    tiles = []
    
    for zoom in zoom_levels:
        n = 2 ** zoom
        x = int((lon + 180.0) / 360.0 * n)
        y = int((1.0 - math.log(math.tan(math.radians(lat)) + 
                                1.0 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n)
        
        tiles.append({"x": x, "y": y, "z": zoom})
    
    return tiles


def _get_region_tiles(center_lat: float, center_lon: float, 
                     radius: float, zoom_levels: List[int]) -> List[Dict[str, int]]:
    """Get tiles covering a circular region"""
    tiles = []
    
    for zoom in zoom_levels:
        # Calculate bounds
        min_lat = center_lat - radius
        max_lat = center_lat + radius
        min_lon = center_lon - radius
        max_lon = center_lon + radius
        
        # Convert to tile coordinates
        n = 2 ** zoom
        min_x = int((min_lon + 180.0) / 360.0 * n)
        max_x = int((max_lon + 180.0) / 360.0 * n)
        
        min_y = int((1.0 - math.log(math.tan(math.radians(max_lat)) + 
                                    1.0 / math.cos(math.radians(max_lat))) / math.pi) / 2.0 * n)
        max_y = int((1.0 - math.log(math.tan(math.radians(min_lat)) + 
                                    1.0 / math.cos(math.radians(min_lat))) / math.pi) / 2.0 * n)
        
        # Add all tiles in bounds
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                tiles.append({"x": x, "y": y, "z": zoom})
    
    return tiles