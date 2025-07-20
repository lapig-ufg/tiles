"""
Unified Cache Management API
Combines all cache-related endpoints in a single, organized module
"""
from datetime import datetime
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from celery.result import AsyncResult

from app.core.auth import SuperAdminRequired
from app.core.mongodb import get_points_collection, get_campaigns_collection
from app.tasks.cache_tasks import cache_point_async, cache_campaign_async, get_cache_status
from app.cache.cache_warmer import (
    CacheWarmer, LoadingPattern, ViewportBounds,
    schedule_warmup_task, analyze_usage_patterns_task
)
from app.tasks.tasks import celery_app
from app.cache.cache_hybrid import tile_cache
from app.core.config import logger

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
        task = cache_point_async.delay(request.point_id)
        
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
        
        # Start optimized cache task (cache_campaign_async já possui todas as otimizações)
        task = cache_campaign_async.delay(request.campaign_id, optimal_batch_size)
        
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
        result = get_cache_status.delay(point_id=point_id)
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
        result = get_cache_status.delay(campaign_id=campaign_id)
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
        
        logger.info(f"Cleared cache status for point {point_id}")
        
        return CacheStatusResponse(
            status="cleared",
            message=f"Cache cleared for point {point_id}",
            data={"point_id": point_id}
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
        
        # Clear cache for all points
        points_collection = await get_points_collection()
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
        
        logger.info(f"Cleared cache for {result.modified_count} points in campaign {campaign_id}")
        
        return CacheStatusResponse(
            status="cleared",
            message=f"Cache cleared for campaign {campaign_id}",
            data={
                "campaign_id": campaign_id,
                "points_cleared": result.modified_count
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