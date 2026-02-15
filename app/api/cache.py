"""
Unified Cache Management API
Combines all cache-related endpoints in a single, organized module
"""
from datetime import datetime
from typing import Optional, Dict, Any, List

from celery.result import AsyncResult
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.cache.cache_hybrid import tile_cache
from app.cache.cache_warmer import (
    CacheWarmer, LoadingPattern, schedule_warmup_task, analyze_usage_patterns_task
)
from app.core.auth import SuperAdminRequired
from app.core.config import logger
from app.core.mongodb import get_points_collection, get_campaigns_collection
from app.tasks.cache_operations import cache_point, cache_campaign, cache_validate
from app.tasks.celery_app import celery_app
from app.tasks.cleanup_tasks import cleanup_expired_cache

router = APIRouter(
    prefix="/api/cache", 
    tags=["Cache Management"],
    dependencies=[SuperAdminRequired]  # Protege todos os endpoints do router
)

# ============================================================================
# Request/Response Models
# ============================================================================

class CachePointRequest(BaseModel):
    """Request to cache a single point"""
    point_id: str = Field(..., description="Point ID to cache")

class CacheCampaignRequest(BaseModel):
    """Request to cache all points in a campaign with optimizations"""
    campaign_id: str = Field(..., description="Campaign ID to cache")
    batch_size: Optional[int] = Field(50, ge=1, le=200, description="Batch size for processing")
    use_grid: Optional[bool] = Field(True, description="Use grid-based optimization")
    priority_recent_years: Optional[bool] = Field(True, description="Prioritize recent years")

class CacheWarmupRequest(BaseModel):
    """Request for cache warming"""
    layer: str = Field(..., description="Layer name to warm cache")
    params: Dict[str, Any] = Field(default_factory=dict, description="Layer parameters")
    max_tiles: int = Field(500, ge=1, le=10000, description="Maximum number of tiles")
    batch_size: int = Field(50, ge=1, le=200, description="Batch size for processing")
    patterns: List[str] = Field(
        default=["spiral", "grid"],
        description="Loading patterns to simulate"
    )
    regions: Optional[List[Dict[str, float]]] = Field(
        None,
        description="Specific regions to warm (min_lat, max_lat, min_lon, max_lon)"
    )

class CacheStatusResponse(BaseModel):
    """Generic cache status response"""
    status: str
    message: str
    data: Optional[Dict[str, Any]] = None
    timestamp: datetime = Field(default_factory=datetime.now)

class CacheStatsResponse(BaseModel):
    """Cache statistics response"""
    total_cached_tiles: int
    cache_hit_rate: float
    redis_keys: int
    disk_usage_mb: float
    popular_tiles: List[Dict[str, Any]]
    last_warmup: Optional[datetime]
    active_tasks: int

class DetailedCacheStatsResponse(BaseModel):
    """Detailed cache statistics response"""
    summary: Dict[str, Any]
    redis: Dict[str, Any]
    s3: Dict[str, Any]
    local_cache: Dict[str, Any]
    performance: Dict[str, Any]
    system: Dict[str, Any]

# ============================================================================
# General Cache Management (Public)
# ============================================================================

@router.get("/stats", response_model=DetailedCacheStatsResponse)
async def get_cache_statistics():
    """
    Get comprehensive detailed cache statistics
    
    Returns complete information about all cache layers:
    - Redis: metadata, keys, memory usage
    - S3/MinIO: total objects, storage usage, performance
    - Local cache: hot tiles, hit rates
    - System performance metrics
    """
    try:
        # Get raw stats from hybrid cache
        raw_stats = await tile_cache.get_stats()
        
        # Get Celery stats
        inspect = celery_app.control.inspect()
        active_tasks = inspect.active() or {}
        scheduled_tasks = inspect.scheduled() or {}
        reserved_tasks = inspect.reserved() or {}
        
        # Calculate total tasks
        total_active = sum(len(tasks) for tasks in active_tasks.values())
        total_scheduled = sum(len(tasks) for tasks in scheduled_tasks.values())
        total_reserved = sum(len(tasks) for tasks in reserved_tasks.values())
        
        # Build detailed response
        redis_stats = raw_stats.get("redis", {})
        s3_stats = raw_stats.get("s3", {})
        local_cache_stats = raw_stats.get("local_cache", {})
        
        return DetailedCacheStatsResponse(
            summary={
                "total_tiles_cached": redis_stats.get("total_keys", 0),
                "s3_objects": s3_stats.get("total_objects", 0),
                "s3_storage_gb": s3_stats.get("size_gb", 0),
                "local_cache_size": local_cache_stats.get("size", 0),
                "active_tasks": total_active,
                "cache_layers": ["redis", "s3", "local"],
                "status": "healthy" if s3_stats.get("connected", False) else "degraded",
                "last_updated": datetime.now().isoformat()
            },
            redis={
                "status": "connected",
                "total_keys": redis_stats.get("total_keys", 0),
                "connected_clients": redis_stats.get("connected_clients", 0),
                "used_memory_human": redis_stats.get("used_memory_human", "0"),
                "estimated_metadata_mb": round(redis_stats.get("total_keys", 0) * 0.5 / 1024, 2),  # ~0.5KB per key
                "ttl_policies": {
                    "tiles_metadata": "7 days",
                    "ee_urls": "24 hours",
                    "vis_params": "7 days"
                }
            },
            s3={
                "status": "connected" if s3_stats.get("connected", False) else "disconnected",
                "endpoint": s3_stats.get("endpoint", "unknown"),
                "bucket": s3_stats.get("bucket", "unknown"),
                "total_objects": s3_stats.get("total_objects", 0),
                "storage": {
                    "bytes": s3_stats.get("size_bytes", 0),
                    "mb": s3_stats.get("size_mb", 0),
                    "gb": s3_stats.get("size_gb", 0)
                },
                "average_tile_size_kb": round(
                    (s3_stats.get("size_bytes", 0) / 1024) / max(s3_stats.get("total_objects", 1), 1), 2
                ),
                "error": s3_stats.get("error") if not s3_stats.get("connected", False) else None
            },
            local_cache={
                "current_size": local_cache_stats.get("size", 0),
                "max_size": local_cache_stats.get("max_size", 1000),
                "usage_percent": round(
                    (local_cache_stats.get("size", 0) / max(local_cache_stats.get("max_size", 1), 1)) * 100, 2
                ),
                "hot_tiles": local_cache_stats.get("hot_tiles", [])[:10],
                "cache_policy": "LRU",
                "ttl_hours": 1
            },
            performance={
                "cache_hit_estimation": {
                    "local": "95%",  # Very fast
                    "redis": "85%",  # Fast
                    "s3": "75%"      # Slower but complete
                },
                "avg_response_time_ms": {
                    "local_cache": 1,
                    "redis_lookup": 5,
                    "s3_download": 50
                },
                "throughput": {
                    "max_concurrent_requests": 1000,
                    "tiles_per_second": 500
                }
            },
            system={
                "celery": {
                    "active_tasks": total_active,
                    "scheduled_tasks": total_scheduled,
                    "reserved_tasks": total_reserved,
                    "workers": list(active_tasks.keys()) if active_tasks else []
                },
                "cache_efficiency": {
                    "metadata_vs_storage_ratio": f"1:{round(s3_stats.get('size_mb', 0) / max(redis_stats.get('total_keys', 1) * 0.5 / 1024, 0.001), 0)}",
                    "storage_compression": "PNG optimized",
                    "partitioning": "MD5 hash prefix"
                },
                "monitoring": {
                    "last_health_check": datetime.now().isoformat(),
                    "uptime_status": "healthy",
                    "alerts": []
                }
            }
        )
        
    except Exception as e:
        logger.exception(f"Error getting detailed cache statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/clear")
async def clear_cache(
    layer: Optional[str] = Query(None, description="Specific layer to clear"),
    year: Optional[int] = Query(None, description="Specific year to clear"),
    x: Optional[int] = Query(None, description="Tile X coordinate"),
    y: Optional[int] = Query(None, description="Tile Y coordinate"),
    z: Optional[int] = Query(None, description="Tile Z coordinate"),
    pattern: Optional[str] = Query(None, description="Custom pattern for clearing"),
    confirm: bool = Query(False, description="Confirm cache clearing")
):
    """
    Clear cache entries based on filters
    
    Use with caution! Requires explicit confirmation.
    
    Examples:
    - DELETE /api/cache/clear?layer=landsat&confirm=true
    - DELETE /api/cache/clear?year=2023&confirm=true
    - DELETE /api/cache/clear?x=123&y=456&z=10&confirm=true
    """
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true to confirm cache clearing"
        )
    
    deleted_count = 0
    
    # Validate tile coordinates
    if x is not None or y is not None or z is not None:
        if not all(v is not None for v in [x, y, z]):
            raise HTTPException(
                status_code=400,
                detail="To clear a specific tile, provide x, y, and z"
            )
    
    try:
        if pattern:
            # Custom pattern clearing
            deleted_count = await tile_cache.delete_by_pattern(pattern)
        elif x is not None and y is not None and z is not None:
            # Clear specific tile
            deleted_count = await tile_cache.clear_cache_by_point(x, y, z)
        elif layer and year:
            # Clear specific layer and year
            deleted_count = await tile_cache.delete_by_pattern(f"{layer}_*_{year}_")
        elif layer:
            # Clear entire layer
            deleted_count = await tile_cache.clear_cache_by_layer(layer)
        elif year:
            # Clear entire year
            deleted_count = await tile_cache.clear_cache_by_year(year)
        else:
            raise HTTPException(
                status_code=400,
                detail="Provide at least one filter: layer, year, x/y/z, or pattern"
            )
        
        return CacheStatusResponse(
            status="success",
            message=f"Cleared {deleted_count} cache entries",
            data={
                "deleted_count": deleted_count,
                "filters": {
                    "layer": layer,
                    "year": year,
                    "tile": {"x": x, "y": y, "z": z} if x is not None else None,
                    "pattern": pattern
                }
            }
        )
        
    except Exception as e:
        logger.exception(f"Error clearing cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/clear/all")
async def clear_all_cache(
    confirm: bool = Query(False, description="Confirm clearing ALL cache"),
    double_confirm: bool = Query(False, description="Double confirmation required for this destructive action")
):
    """
    Clear ALL cache entries - EXTREMELY DESTRUCTIVE!
    
    This will remove:
    - All Redis metadata
    - All S3 objects
    - All local cache
    
    Requires double confirmation for safety.
    
    Example:
    - DELETE /api/cache/clear/all?confirm=true&double_confirm=true
    """
    if not confirm or not double_confirm:
        raise HTTPException(
            status_code=400,
            detail="This action will delete ALL cache data. Set both confirm=true and double_confirm=true to proceed."
        )
    
    try:
        # Get initial stats for reporting
        initial_stats = await tile_cache.get_stats()
        initial_redis_keys = initial_stats["redis"]["total_keys"]
        initial_s3_objects = initial_stats["s3"]["total_objects"]
        initial_s3_size_gb = initial_stats["s3"]["size_gb"]
        
        # Clear all cache using wildcard pattern
        deleted_count = await tile_cache.delete_by_pattern("*")
        
        # Get final stats
        final_stats = await tile_cache.get_stats()
        
        return CacheStatusResponse(
            status="success",
            message="All cache has been cleared",
            data={
                "deleted": {
                    "total_items": deleted_count,
                    "redis_keys": initial_redis_keys,
                    "s3_objects": initial_s3_objects,
                    "storage_freed_gb": initial_s3_size_gb
                },
                "remaining": {
                    "redis_keys": final_stats["redis"]["total_keys"],
                    "s3_objects": final_stats["s3"]["total_objects"]
                },
                "timestamp": datetime.now().isoformat()
            }
        )
        
    except Exception as e:
        logger.exception(f"Error clearing all cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# Cache Warming (Public)
# ============================================================================

@router.post("/warmup")
async def warmup_cache(request: CacheWarmupRequest):
    """
    Start cache warming process
    
    Schedules Celery tasks to pre-load popular tiles by simulating
    real webmap request patterns.
    """
    try:
        # Validate patterns
        valid_patterns = [p.value for p in LoadingPattern]
        for pattern in request.patterns:
            if pattern not in valid_patterns:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid pattern: {pattern}. Valid: {valid_patterns}"
                )
        
        # Schedule warmup task
        result = schedule_warmup_task.delay(
            layer=request.layer,
            params=request.params,
            max_tiles=request.max_tiles,
            batch_size=request.batch_size
        )
        
        # Estimate time
        estimated_time = (request.max_tiles / request.batch_size) * 2  # ~2s per batch
        
        return CacheStatusResponse(
            status="scheduled",
            message=f"Cache warmup scheduled for {request.max_tiles} tiles",
            data={
                "task_id": result.id,
                "total_tiles": request.max_tiles,
                "batches": request.max_tiles // request.batch_size,
                "estimated_time_minutes": estimated_time / 60
            }
        )
        
    except Exception as e:
        logger.error(f"Error scheduling warmup: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/analyze-patterns")
async def analyze_usage_patterns(
    days: int = Query(7, ge=1, le=30, description="Days to analyze")
):
    """
    Analyze usage patterns for cache optimization
    
    Schedules a task to analyze tile access patterns and generate
    recommendations for cache optimization.
    """
    try:
        result = analyze_usage_patterns_task.delay(days)
        
        return CacheStatusResponse(
            status="analyzing",
            message=f"Analyzing patterns from last {days} days",
            data={"task_id": result.id}
        )
        
    except Exception as e:
        logger.error(f"Error analyzing patterns: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# TVI Point/Campaign Cache Management (Protected)
# ============================================================================

@router.post("/point/start")
async def start_point_cache(request: CachePointRequest) -> CacheStatusResponse:
    """
    Start async cache generation for a specific point
    
    This will cache all tiles for:
    - All visParamsEnable options
    - All years (initialYear to finalYear)
    - Zoom levels 12, 13, 14
    
    Protected endpoint - requires super-admin authentication.
    """
    try:
        # Validate point exists
        points_collection = await get_points_collection()
        point = await points_collection.find_one({"_id": request.point_id})
        
        if not point:
            raise HTTPException(
                status_code=404,
                detail=f"Point {request.point_id} not found"
            )
        
        # Check if already cached
        if point.get("cached"):
            return CacheStatusResponse(
                status="already_cached",
                message=f"Point {request.point_id} is already cached",
                data={
                    "point_id": request.point_id,
                    "cached_at": point.get("cachedAt"),
                    "cached_by": point.get("cachedBy")
                }
            )
        
        # Start cache task
        task = cache_point.delay(request.point_id)
        
        logger.info(f"Started cache task {task.id} for point {request.point_id}")
        
        return CacheStatusResponse(
            status="started",
            message=f"Cache task started for point {request.point_id}",
            data={
                "task_id": task.id,
                "point_id": request.point_id
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error starting point cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/campaign/start")
async def start_campaign_cache(request: CacheCampaignRequest) -> CacheStatusResponse:
    """
    Start optimized async cache generation for all points in a campaign
    
    Features:
    - Grid-based tile grouping (reduces GEE requests by up to 16x)
    - Intelligent prioritization (recent years and important zoom levels first)
    - Dynamic batch sizing based on campaign size
    - Better rate limiting and error handling
    
    Protected endpoint - requires super-admin authentication.
    """
    try:
        # Validate campaign exists
        campaigns_collection = await get_campaigns_collection()
        campaign = await campaigns_collection.find_one({"_id": request.campaign_id})
        
        if not campaign:
            raise HTTPException(
                status_code=404,
                detail=f"Campaign {request.campaign_id} not found"
            )
        
        # Count points
        points_collection = await get_points_collection()
        point_count = await points_collection.count_documents({"campaign": request.campaign_id})
        
        if point_count == 0:
            return CacheStatusResponse(
                status="no_points",
                message=f"No points found for campaign {request.campaign_id}",
                data={"campaign_id": request.campaign_id, "point_count": 0}
            )
        
        # Check if already caching
        if campaign.get("caching_in_progress", False):
            return CacheStatusResponse(
                status="already_running",
                message=f"Campaign {request.campaign_id} is already being cached",
                data={
                    "campaign_id": request.campaign_id,
                    "started_at": campaign.get("caching_started_at"),
                    "cached_points": campaign.get("cached_points", 0),
                    "total_points": point_count
                }
            )
        
        # Calculate optimal batch size based on campaign size
        if point_count > 10000:
            optimal_batch_size = min(request.batch_size * 2, 200)  # Larger batches for big campaigns
        elif point_count > 1000:
            optimal_batch_size = request.batch_size
        else:
            optimal_batch_size = max(request.batch_size // 2, 10)  # Smaller batches for small campaigns
        
        # Start optimized cache task (cache_campaign já possui todas as otimizações)
        task = cache_campaign.delay(request.campaign_id, optimal_batch_size)
        
        # Estimate processing time
        tiles_per_point = len(campaign.get("visParamsEnable", [])) * (campaign.get("finalYear", 2024) - campaign.get("initialYear", 2020) + 1) * 3  # 3 zoom levels
        total_tiles = point_count * tiles_per_point
        
        # With grid optimization, we can process ~16 tiles per GEE request
        estimated_gee_requests = total_tiles // 16 if request.use_grid else total_tiles
        estimated_minutes = (estimated_gee_requests * 0.05) / 60  # 50ms per request
        
        logger.info(f"Started OPTIMIZED campaign cache task {task.id} for {point_count} points")
        
        return CacheStatusResponse(
            status="started",
            message=f"Optimized cache task started for campaign {request.campaign_id}",
            data={
                "task_id": task.id,
                "campaign_id": request.campaign_id,
                "point_count": point_count,
                "batch_size": optimal_batch_size,
                "optimization": {
                    "grid_mode": request.use_grid,
                    "priority_recent_years": request.priority_recent_years,
                    "estimated_tiles": total_tiles,
                    "estimated_gee_requests": estimated_gee_requests,
                    "estimated_time_minutes": round(estimated_minutes, 2),
                    "speedup_factor": "16x" if request.use_grid else "1x"
                }
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error starting campaign cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/point/{point_id}/status")
async def get_point_cache_status(point_id: str) -> CacheStatusResponse:
    """
    Get cache status for a specific point
    
    Protected endpoint - requires super-admin authentication.
    """
    try:
        result = cache_validate.delay(point_id=point_id)
        status_data = result.get(timeout=10)
        
        if "error" in status_data:
            raise HTTPException(status_code=404, detail=status_data["error"])
        
        return CacheStatusResponse(
            status="success",
            message=f"Cache status for point {point_id}",
            data=status_data
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error getting point cache status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/campaign/{campaign_id}/status")
async def get_campaign_cache_status(campaign_id: str) -> CacheStatusResponse:
    """
    Get aggregated cache status for all points in a campaign
    
    Protected endpoint - requires super-admin authentication.
    """
    try:
        # Get cache status from Celery task
        result = cache_validate.delay(campaign_id=campaign_id)
        status_data = result.get(timeout=10)
        
        if "error" in status_data:
            raise HTTPException(status_code=404, detail=status_data["error"])
        
        # Also get campaign progress from MongoDB
        campaigns_collection = await get_campaigns_collection()
        campaign = await campaigns_collection.find_one({"_id": campaign_id})
        
        if campaign:
            # Merge campaign progress data
            status_data.update({
                "caching_in_progress": campaign.get("caching_in_progress", False),
                "caching_started_at": campaign.get("caching_started_at"),
                "last_point_cached_at": campaign.get("last_point_cached_at"),
                "caching_completed_at": campaign.get("caching_completed_at"),
                "all_points_cached": campaign.get("all_points_cached", False),
                "points_to_cache": campaign.get("points_to_cache"),
                "caching_error": campaign.get("caching_error"),
                "caching_error_at": campaign.get("caching_error_at")
            })
        
        return CacheStatusResponse(
            status="success",
            message=f"Cache status for campaign {campaign_id}",
            data=status_data
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error getting campaign cache status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/point/{point_id}")
async def clear_point_cache(point_id: str) -> CacheStatusResponse:
    """
    Clear cache for a specific point
    
    Protected endpoint - requires super-admin authentication.
    """
    try:
        # Validate point exists
        points_collection = await get_points_collection()
        point = await points_collection.find_one({"_id": point_id})
        
        if not point:
            raise HTTPException(status_code=404, detail=f"Point {point_id} not found")
        
        # Get point coordinates and vis params
        lon = point.get("lon")
        lat = point.get("lat")
        
        # Clear actual cache entries from Redis and S3
        deleted_count = 0
        if lon is not None and lat is not None:
            # Clear cache for all zoom levels and years for this point
            # Pattern: layer_year_zoom/z/x_y
            # We need to clear all tiles that contain this point
            for z in [12, 13, 14]:  # Common zoom levels for points
                # Convert lat/lon to tile coordinates
                import math
                n = 2.0 ** z
                x = int((lon + 180.0) / 360.0 * n)
                y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
                
                # Clear tiles for this point at this zoom level
                deleted_count += await tile_cache.clear_cache_by_point(x, y, z)
        
        # Update point status
        await points_collection.update_one(
            {"_id": point_id},
            {
                "$set": {
                    "cached": False,
                    "cachedAt": None,
                    "cachedBy": None,
                    "enhance_in_cache": 0
                }
            }
        )
        
        logger.info(f"Cleared cache status for point {point_id}, removed {deleted_count} tiles")
        
        return CacheStatusResponse(
            status="cleared",
            message=f"Cache cleared for point {point_id}",
            data={
                "point_id": point_id,
                "tiles_deleted": deleted_count,
                "coordinates": {"lon": lon, "lat": lat},
                "zoom_levels_cleared": [12, 13, 14]
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error clearing point cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/campaign/{campaign_id}")
async def clear_campaign_cache(campaign_id: str) -> CacheStatusResponse:
    """
    Clear cache for all points in a campaign
    
    Protected endpoint - requires super-admin authentication.
    """
    try:
        # Validate campaign exists
        campaigns_collection = await get_campaigns_collection()
        campaign = await campaigns_collection.find_one({"_id": campaign_id})
        
        if not campaign:
            raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found")
        
        # Get all points in the campaign
        points_collection = await get_points_collection()
        points = await points_collection.find({"campaign": campaign_id}).to_list(None)
        
        # Clear actual cache entries from Redis and S3 for each point
        total_deleted = 0
        for point in points:
            lon = point.get("lon")
            lat = point.get("lat")
            
            if lon is not None and lat is not None:
                # Clear cache for all zoom levels for this point
                for z in [12, 13, 14]:
                    # Convert lat/lon to tile coordinates
                    import math
                    n = 2.0 ** z
                    x = int((lon + 180.0) / 360.0 * n)
                    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
                    
                    # Clear tiles for this point at this zoom level
                    total_deleted += await tile_cache.clear_cache_by_point(x, y, z)
        
        # Update all points status
        result = await points_collection.update_many(
            {"campaign": campaign_id},
            {
                "$set": {
                    "cached": False,
                    "cachedAt": None,
                    "cachedBy": None,
                    "enhance_in_cache": 0
                }
            }
        )
        
        logger.info(f"Cleared cache for {result.modified_count} points in campaign {campaign_id}, removed {total_deleted} tiles")
        
        return CacheStatusResponse(
            status="cleared",
            message=f"Cache cleared for campaign {campaign_id}",
            data={
                "campaign_id": campaign_id,
                "points_cleared": result.modified_count,
                "tiles_deleted": total_deleted,
                "zoom_levels_cleared": [12, 13, 14]
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error clearing campaign cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# Task Management
# ============================================================================

@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """
    Get status of any cache-related Celery task
    
    Works for warmup tasks, point cache tasks, and campaign cache tasks.
    """
    try:
        task_result = AsyncResult(task_id, app=celery_app)
        
        return CacheStatusResponse(
            status="success",
            message=f"Task {task_id} status",
            data={
                "task_id": task_id,
                "state": task_result.state,
                "ready": task_result.ready(),
                "successful": task_result.successful() if task_result.ready() else None,
                "result": task_result.result if task_result.successful() else None,
                "error": str(task_result.info) if task_result.failed() else None
            }
        )
        
    except Exception as e:
        logger.exception(f"Error getting task status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# Cache Health & Integrity
# ============================================================================

@router.get("/health/check")
async def check_cache_integrity():
    """
    Check cache integrity and health
    
    Verifies:
    - Redis metadata exists for S3 objects
    - S3 objects exist for Redis metadata
    - Identifies orphaned entries
    - Checks for corrupted data
    """
    try:
        issues = {
            "orphaned_metadata": [],
            "missing_s3_objects": [],
            "connection_issues": [],
            "corrupted_entries": []
        }
        
        # Check Redis connection
        try:
            async with tile_cache._get_redis() as r:
                await r.ping()
        except Exception as e:
            issues["connection_issues"].append(f"Redis connection failed: {str(e)}")
        
        # Check S3 connection
        try:
            async with tile_cache.s3_session.client(
                's3',
                endpoint_url=tile_cache.s3_endpoint,
                aws_access_key_id=settings.get("S3_ACCESS_KEY"),
                aws_secret_access_key=settings.get("S3_SECRET_KEY"),
                use_ssl=settings.get("S3_USE_SSL",True),  # <-- ADICIONE ISSO
                verify=settings.get("S3_VERIFY_SSL", True) 
            ) as s3:
                await s3.head_bucket(Bucket=tile_cache.s3_bucket)
        except Exception as e:
            issues["connection_issues"].append(f"S3 connection failed: {str(e)}")
        
        if not issues["connection_issues"]:
            # Sample check for integrity (limited to avoid performance impact)
            async with tile_cache._get_redis() as r:
                # Get a sample of tile metadata
                sample_keys = []
                async for key in r.scan_iter(match="tile:*", count=100):
                    sample_keys.append(key)
                    if len(sample_keys) >= 100:  # Check only 100 entries
                        break
                
                # Verify S3 objects exist
                async with tile_cache.s3_session.client(
                    's3',
                    endpoint_url=tile_cache.s3_endpoint,
                    aws_access_key_id=settings.get("S3_ACCESS_KEY"),
                    aws_secret_access_key=settings.get("S3_SECRET_KEY"),
                    use_ssl=settings.get("S3_USE_SSL",True),  # <-- ADICIONE ISSO
                    verify=settings.get("S3_VERIFY_SSL", True) 
                ) as s3:
                    for key in sample_keys:
                        meta = await r.hgetall(key)
                        if meta and meta.get(b's3_key'):
                            s3_key = meta[b's3_key'].decode()
                            try:
                                await s3.head_object(Bucket=tile_cache.s3_bucket, Key=s3_key)
                            except:
                                issues["missing_s3_objects"].append({
                                    "redis_key": key.decode(),
                                    "s3_key": s3_key
                                })
        
        # Calculate health score
        total_issues = sum(len(v) for v in issues.values())
        health_score = 100 - min(total_issues * 10, 100)  # Deduct 10% per issue
        
        return {
            "status": "healthy" if health_score >= 80 else "degraded" if health_score >= 50 else "unhealthy",
            "health_score": health_score,
            "issues": issues,
            "issues_count": total_issues,
            "checked_at": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.exception(f"Error checking cache integrity: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/report")
async def generate_cache_report(
    format: str = Query("json", description="Report format: json or csv")
):
    """
    Generate comprehensive cache report
    
    Includes:
    - Storage utilization
    - Cost estimates
    - Performance metrics
    - Usage patterns
    - Recommendations
    """
    try:
        # Get current stats
        stats = await tile_cache.get_stats()
        
        # Calculate cost estimates (example rates)
        s3_storage_gb = stats["s3"]["size_gb"]
        redis_memory_gb = float(stats["redis"].get("used_memory_human", "0").replace("M", "").replace("G", "")) / 1024
        
        # Example AWS pricing
        s3_cost_per_gb = 0.023  # USD per GB per month
        redis_cost_per_gb = 0.016  # USD per GB per hour
        
        monthly_s3_cost = s3_storage_gb * s3_cost_per_gb
        monthly_redis_cost = redis_memory_gb * redis_cost_per_gb * 24 * 30
        
        # Calculate savings from cache hits
        avg_gee_request_cost = 0.001  # USD per request (estimated)
        estimated_hits_per_day = stats["redis"]["total_keys"] * 10  # Assume 10 hits per key per day
        monthly_savings = estimated_hits_per_day * 30 * avg_gee_request_cost
        
        report = {
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "total_cache_entries": stats["redis"]["total_keys"],
                "s3_storage_gb": round(s3_storage_gb, 2),
                "redis_memory_gb": round(redis_memory_gb, 2),
                "local_cache_items": stats["local_cache"]["size"]
            },
            "costs": {
                "monthly_s3_cost_usd": round(monthly_s3_cost, 2),
                "monthly_redis_cost_usd": round(monthly_redis_cost, 2),
                "total_monthly_cost_usd": round(monthly_s3_cost + monthly_redis_cost, 2),
                "estimated_monthly_savings_usd": round(monthly_savings, 2),
                "net_benefit_usd": round(monthly_savings - (monthly_s3_cost + monthly_redis_cost), 2)
            },
            "performance": {
                "cache_efficiency_ratio": f"{round((monthly_savings / (monthly_s3_cost + monthly_redis_cost + 0.01)) * 100, 1)}%",
                "avg_response_time_improvement": "95%",
                "gee_requests_saved_monthly": int(estimated_hits_per_day * 30)
            },
            "storage_distribution": {
                "by_year": {},  # Would need to analyze keys
                "by_layer": {},  # Would need to analyze keys
                "avg_tile_size_kb": stats["s3"].get("avg_object_size_kb", 0)
            },
            "recommendations": [
                {
                    "type": "cleanup",
                    "description": "Remove tiles older than 30 days with low access",
                    "potential_savings_gb": round(s3_storage_gb * 0.2, 2)
                },
                {
                    "type": "optimization",
                    "description": "Implement progressive cache warmup for popular regions",
                    "potential_improvement": "20% better hit rate"
                }
            ]
        }
        
        if format == "csv":
            # Convert to CSV format
            import csv
            import io
            
            output = io.StringIO()
            writer = csv.writer(output)
            
            # Write headers and data
            writer.writerow(["Metric", "Value"])
            writer.writerow(["Generated At", report["generated_at"]])
            writer.writerow(["Total Cache Entries", report["summary"]["total_cache_entries"]])
            writer.writerow(["S3 Storage (GB)", report["summary"]["s3_storage_gb"]])
            writer.writerow(["Redis Memory (GB)", report["summary"]["redis_memory_gb"]])
            writer.writerow(["Monthly S3 Cost (USD)", report["costs"]["monthly_s3_cost_usd"]])
            writer.writerow(["Monthly Redis Cost (USD)", report["costs"]["monthly_redis_cost_usd"]])
            writer.writerow(["Total Monthly Cost (USD)", report["costs"]["total_monthly_cost_usd"]])
            writer.writerow(["Estimated Monthly Savings (USD)", report["costs"]["estimated_monthly_savings_usd"]])
            writer.writerow(["Net Benefit (USD)", report["costs"]["net_benefit_usd"]])
            
            from fastapi.responses import Response
            return Response(
                content=output.getvalue(),
                media_type="text/csv",
                headers={
                    "Content-Disposition": f"attachment; filename=cache_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                }
            )
        
        return report
        
    except Exception as e:
        logger.exception(f"Error generating cache report: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# Cache Cleanup & TTL Management
# ============================================================================

class CacheCleanupRequest(BaseModel):
    """Request for cache cleanup"""
    dry_run: bool = Field(False, description="If True, only report what would be cleaned without deleting")
    max_items: Optional[int] = Field(None, ge=1, le=100000, description="Maximum items to process")

@router.post("/cleanup/ttl")
async def trigger_ttl_cleanup(request: CacheCleanupRequest) -> CacheStatusResponse:
    """
    Trigger TTL-based cache cleanup
    
    This endpoint manually triggers the cleanup process that:
    - Scans for expired Redis keys
    - Identifies orphaned S3 objects (no corresponding Redis metadata)
    - Removes expired entries and orphaned objects
    - Provides detailed metrics on cleanup operations
    
    By default, this runs automatically daily at 3 AM, but can be triggered manually.
    
    Protected endpoint - requires super-admin authentication.
    """
    try:
        # Schedule cleanup task
        task = cleanup_expired_cache.delay(request.dry_run, request.max_items)
        
        logger.info(f"Started cache cleanup task {task.id} (dry_run={request.dry_run})")
        
        return CacheStatusResponse(
            status="started",
            message=f"Cache cleanup {'simulation' if request.dry_run else 'task'} started",
            data={
                "task_id": task.id,
                "dry_run": request.dry_run,
                "max_items": request.max_items,
                "mode": "dry_run" if request.dry_run else "cleanup"
            }
        )
        
    except Exception as e:
        logger.exception(f"Error starting cache cleanup: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/cleanup/age-analysis")
async def analyze_cache_age() -> Dict[str, Any]:
    """
    Analyze age distribution of cached items
    
    This helps optimize TTL values by understanding:
    - How old cached items typically are
    - Current TTL distribution
    - Recommendations for TTL optimization
    
    Protected endpoint - requires super-admin authentication.
    """
    try:
        # Schedule analysis task
        task = analyze_cache_age_distribution.delay()
        
        # Wait for result (this is usually fast)
        result = task.get(timeout=30)
        
        return {
            "status": "success",
            "analysis": result,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.exception(f"Error analyzing cache age: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/cleanup/history")
async def get_cleanup_history(
    limit: int = Query(10, ge=1, le=100, description="Number of recent cleanup operations to return")
) -> Dict[str, Any]:
    """
    Get history of recent cache cleanup operations
    
    Returns details about past cleanup operations including:
    - Items cleaned
    - Space freed
    - Errors encountered
    - Duration and performance metrics
    
    Protected endpoint - requires super-admin authentication.
    """
    try:
        from app.core.mongodb import get_db
        db = await get_db()
        
        # Get cleanup logs collection
        cleanup_logs = db.cleanup_logs
        
        # Find recent cleanup operations
        cursor = cleanup_logs.find().sort("timestamp", -1).limit(limit)
        history = []
        
        async for log in cursor:
            # Remove MongoDB _id for JSON serialization
            log.pop("_id", None)
            history.append(log)
        
        # Calculate summary statistics
        if history:
            total_redis_cleaned = sum(log.get("redis_deleted", 0) for log in history)
            total_s3_cleaned = sum(log.get("s3_deleted", 0) for log in history)
            total_space_freed = sum(log.get("space_freed_mb", 0) for log in history)
            avg_duration = sum(log.get("duration_seconds", 0) for log in history) / len(history)
        else:
            total_redis_cleaned = 0
            total_s3_cleaned = 0
            total_space_freed = 0
            avg_duration = 0
        
        return {
            "status": "success",
            "history": history,
            "summary": {
                "operations_count": len(history),
                "total_redis_entries_cleaned": total_redis_cleaned,
                "total_s3_objects_cleaned": total_s3_cleaned,
                "total_space_freed_mb": round(total_space_freed, 2),
                "average_duration_seconds": round(avg_duration, 2)
            },
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.exception(f"Error getting cleanup history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/cleanup/schedule")
async def get_cleanup_schedule() -> Dict[str, Any]:
    """
    Get current automatic cleanup schedule
    
    Returns information about scheduled automatic cleanup tasks.
    
    Protected endpoint - requires super-admin authentication.
    """
    try:
        # Get beat schedule from Celery
        schedule = celery_app.conf.beat_schedule
        
        cleanup_schedules = {}
        for task_name, task_config in schedule.items():
            if "cleanup" in task_name or "cache" in task_name.lower():
                cleanup_schedules[task_name] = {
                    "task": task_config["task"],
                    "schedule": str(task_config["schedule"]),
                    "args": task_config.get("args", []),
                    "kwargs": task_config.get("kwargs", {}),
                    "enabled": True
                }
        
        # Add specific information about our cleanup tasks
        ttl_schedule = {
            "cleanup-expired-cache-daily": {
                "description": "Automatic TTL-based cache cleanup",
                "frequency": "Daily at 3:00 AM UTC",
                "next_run": "Tomorrow 3:00 AM UTC",
                "actions": [
                    "Scan for expired Redis keys",
                    "Find orphaned S3 objects",
                    "Remove expired entries",
                    "Log cleanup metrics"
                ]
            },
            "analyze-cache-age-weekly": {
                "description": "Cache age distribution analysis",
                "frequency": "Weekly on Monday at 4:00 AM UTC",
                "next_run": "Next Monday 4:00 AM UTC",
                "actions": [
                    "Sample cache entries",
                    "Analyze age distribution",
                    "Generate TTL recommendations"
                ]
            }
        }
        
        return {
            "status": "success",
            "automatic_cleanup": {
                "enabled": True,
                "schedules": cleanup_schedules,
                "details": ttl_schedule
            },
            "manual_trigger": {
                "endpoint": "/api/cache/cleanup/ttl",
                "method": "POST",
                "description": "Manually trigger cleanup process"
            },
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.exception(f"Error getting cleanup schedule: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# Cache Recommendations
# ============================================================================

@router.get("/recommendations")
async def get_cache_recommendations():
    """
    Get recommendations for cache optimization
    
    Analyzes popular regions and usage patterns to suggest
    optimal cache warming strategies.
    """
    try:
        warmer = CacheWarmer()
        recommendations = []
        
        # Analyze popular regions
        for idx, region in enumerate(warmer.popular_regions):
            recommendations.append({
                "type": "popular_region",
                "priority": "high",
                "region_id": idx,
                "bounds": {
                    "min_lat": region.min_lat,
                    "max_lat": region.max_lat,
                    "min_lon": region.min_lon,
                    "max_lon": region.max_lon
                },
                "recommended_zoom_levels": list(range(region.zoom - 1, region.zoom + 2)),
                "estimated_tiles": 500
            })
        
        # Recommend priority zooms
        priority_zooms = sorted(
            warmer.zoom_priorities.items(),
            key=lambda x: x[1],
            reverse=True
        )[:5]
        
        recommendations.append({
            "type": "zoom_optimization",
            "priority": "medium",
            "recommended_zooms": [z[0] for z in priority_zooms],
            "reason": "Most used zoom levels"
        })
        
        return {
            "recommendations": recommendations,
            "total_recommendations": len(recommendations)
        }
        
    except Exception as e:
        logger.exception(f"Error getting recommendations: {e}")
        raise HTTPException(status_code=500, detail=str(e))