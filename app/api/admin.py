from fastapi import APIRouter, HTTPException
from app.utils.capabilities import get_capabilities_provider
from app.core.mongodb import get_database
from app.core.auth import SuperAdminRequired
import logging

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin",
    tags=["Admin"],
    dependencies=[SuperAdminRequired]
)

@router.post("/clear-capabilities-cache")
async def clear_capabilities_cache():
    """
    Clear the capabilities cache to force reload from MongoDB
    """
    try:
        provider = get_capabilities_provider()
        provider.clear_cache()
        return {"message": "Capabilities cache cleared successfully"}
    except Exception as e:
        logger.error(f"Error clearing capabilities cache: {e}")
        raise HTTPException(500, "Failed to clear capabilities cache")

@router.get("/vis-params-summary")
async def get_vis_params_summary():
    """
    Get a summary of vis_params in MongoDB
    """
    try:
        db = get_database()
        if db is None:
            raise HTTPException(503, "MongoDB not connected")
        
        # Count by category
        pipeline = [
            {"$match": {"active": True}},
            {"$group": {
                "_id": "$category",
                "count": {"$sum": 1},
                "names": {"$push": "$name"}
            }},
            {"$sort": {"_id": 1}}
        ]
        
        cursor = db.vis_params.aggregate(pipeline)
        results = await cursor.to_list(length=None)
        
        # Total count
        total = await db.vis_params.count_documents({})
        active = await db.vis_params.count_documents({"active": True})
        
        return {
            "total": total,
            "active": active,
            "by_category": results
        }
        
    except Exception as e:
        logger.error(f"Error getting vis_params summary: {e}")
        raise HTTPException(500, f"Failed to get vis_params summary: {str(e)}")

@router.post("/fix-categories")
async def fix_categories():
    """
    Fix category names in MongoDB (sentinel2 -> sentinel)
    """
    try:
        db = get_database()
        if db is None:
            raise HTTPException(503, "MongoDB not connected")
        
        # Update sentinel2 to sentinel
        result = await db.vis_params.update_many(
            {"category": "sentinel2"},
            {"$set": {"category": "sentinel"}}
        )
        
        # Clear cache after update
        provider = get_capabilities_provider()
        provider.clear_cache()
        
        return {
            "message": "Categories fixed successfully",
            "documents_updated": result.modified_count
        }
        
    except Exception as e:
        logger.error(f"Error fixing categories: {e}")
        raise HTTPException(500, f"Failed to fix categories: {str(e)}")