"""
Cache management endpoints for TVI system
Protected endpoints that require super-admin authentication
"""
from datetime import datetime
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import SuperAdminRequired
from app.mongodb import get_points_collection, get_campaigns_collection
from app.cache_tasks import cache_point_async, cache_campaign_async, get_cache_status
from app.config import logger

router = APIRouter(prefix="/api/v1/cache", tags=["cache-management"])

# Request/Response models
class CachePointRequest(BaseModel):
    point_id: str

class CacheCampaignRequest(BaseModel):
    campaign_id: str
    batch_size: Optional[int] = 5

class CacheStatusResponse(BaseModel):
    status: str
    message: str
    data: Optional[Dict[str, Any]] = None

@router.post("/point/start", dependencies=[SuperAdminRequired])
async def start_point_cache(
    request: CachePointRequest,
    background_tasks: BackgroundTasks
) -> CacheStatusResponse:
    """
    Start async cache generation for a specific point
    
    This endpoint will:
    1. Validate that the point exists
    2. Queue a Celery task to cache all tiles for the point
    3. Cache tiles for all visParamsEnable, all years (initialYear-finalYear), zoom levels 12-14
    
    Args:
        request: Point ID to cache
        
    Returns:
        Task ID and status
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
        
        # Check if point is already being cached or cached
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
                "point_id": request.point_id,
                "started_at": datetime.now().isoformat()
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error starting point cache: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error starting cache task: {str(e)}"
        )

@router.post("/campaign/start", dependencies=[SuperAdminRequired])
async def start_campaign_cache(
    request: CacheCampaignRequest,
    background_tasks: BackgroundTasks
) -> CacheStatusResponse:
    """
    Start async cache generation for all points in a campaign
    
    This endpoint will:
    1. Validate that the campaign exists
    2. Queue a Celery task to cache all tiles for all points in the campaign
    3. Process points in batches to avoid overwhelming the system
    
    Args:
        request: Campaign ID and optional batch size
        
    Returns:
        Task ID and status
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
        
        # Count points in campaign
        points_collection = await get_points_collection()
        point_count = await points_collection.count_documents({"campaign": request.campaign_id})
        
        if point_count == 0:
            return CacheStatusResponse(
                status="no_points",
                message=f"No points found for campaign {request.campaign_id}",
                data={
                    "campaign_id": request.campaign_id,
                    "point_count": 0
                }
            )
        
        # Start cache task
        task = cache_campaign_async.delay(request.campaign_id, request.batch_size)
        
        logger.info(f"Started campaign cache task {task.id} for campaign {request.campaign_id} with {point_count} points")
        
        return CacheStatusResponse(
            status="started",
            message=f"Cache task started for campaign {request.campaign_id}",
            data={
                "task_id": task.id,
                "campaign_id": request.campaign_id,
                "point_count": point_count,
                "batch_size": request.batch_size,
                "started_at": datetime.now().isoformat()
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error starting campaign cache: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error starting cache task: {str(e)}"
        )

@router.get("/point/{point_id}/status", dependencies=[SuperAdminRequired])
async def get_point_cache_status(point_id: str) -> CacheStatusResponse:
    """
    Get cache status for a specific point
    
    Args:
        point_id: Point ID to check
        
    Returns:
        Cache status information
    """
    try:
        # Get status from task
        result = get_cache_status.delay(point_id=point_id)
        status_data = result.get(timeout=10)
        
        if "error" in status_data:
            raise HTTPException(
                status_code=404,
                detail=status_data["error"]
            )
        
        return CacheStatusResponse(
            status="success",
            message=f"Cache status for point {point_id}",
            data=status_data
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error getting point cache status: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error getting cache status: {str(e)}"
        )

@router.get("/campaign/{campaign_id}/status", dependencies=[SuperAdminRequired])
async def get_campaign_cache_status(campaign_id: str) -> CacheStatusResponse:
    """
    Get cache status for all points in a campaign
    
    Args:
        campaign_id: Campaign ID to check
        
    Returns:
        Aggregated cache status for the campaign
    """
    try:
        # Get status from task
        result = get_cache_status.delay(campaign_id=campaign_id)
        status_data = result.get(timeout=10)
        
        if "error" in status_data:
            raise HTTPException(
                status_code=404,
                detail=status_data["error"]
            )
        
        return CacheStatusResponse(
            status="success",
            message=f"Cache status for campaign {campaign_id}",
            data=status_data
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error getting campaign cache status: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error getting cache status: {str(e)}"
        )

@router.delete("/point/{point_id}/clear", dependencies=[SuperAdminRequired])
async def clear_point_cache(point_id: str) -> CacheStatusResponse:
    """
    Clear cache for a specific point
    
    Args:
        point_id: Point ID to clear cache for
        
    Returns:
        Clear operation result
    """
    try:
        # Validate point exists
        points_collection = await get_points_collection()
        point = await points_collection.find_one({"_id": point_id})
        
        if not point:
            raise HTTPException(
                status_code=404,
                detail=f"Point {point_id} not found"
            )
        
        # Clear cache files (would need to implement based on your cache structure)
        # For now, just update the point status
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
            data={
                "point_id": point_id,
                "cleared_at": datetime.now().isoformat()
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error clearing point cache: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error clearing cache: {str(e)}"
        )

@router.delete("/campaign/{campaign_id}/clear", dependencies=[SuperAdminRequired])
async def clear_campaign_cache(campaign_id: str) -> CacheStatusResponse:
    """
    Clear cache for all points in a campaign
    
    Args:
        campaign_id: Campaign ID to clear cache for
        
    Returns:
        Clear operation result
    """
    try:
        # Validate campaign exists
        campaigns_collection = await get_campaigns_collection()
        campaign = await campaigns_collection.find_one({"_id": campaign_id})
        
        if not campaign:
            raise HTTPException(
                status_code=404,
                detail=f"Campaign {campaign_id} not found"
            )
        
        # Clear cache for all points in campaign
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
        
        logger.info(f"Cleared cache status for {result.modified_count} points in campaign {campaign_id}")
        
        return CacheStatusResponse(
            status="cleared",
            message=f"Cache cleared for campaign {campaign_id}",
            data={
                "campaign_id": campaign_id,
                "points_cleared": result.modified_count,
                "cleared_at": datetime.now().isoformat()
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error clearing campaign cache: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error clearing cache: {str(e)}"
        )

@router.get("/tasks/{task_id}/status", dependencies=[SuperAdminRequired])
async def get_task_status(task_id: str) -> CacheStatusResponse:
    """
    Get status of a specific Celery task
    
    Args:
        task_id: Celery task ID
        
    Returns:
        Task status and result
    """
    try:
        from celery.result import AsyncResult
        
        task_result = AsyncResult(task_id, app=cache_point_async.app)
        
        return CacheStatusResponse(
            status="success",
            message=f"Task {task_id} status",
            data={
                "task_id": task_id,
                "state": task_result.state,
                "result": task_result.result if task_result.successful() else None,
                "error": str(task_result.info) if task_result.failed() else None
            }
        )
        
    except Exception as e:
        logger.exception(f"Error getting task status: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error getting task status: {str(e)}"
        )