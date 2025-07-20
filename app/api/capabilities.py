from fastapi import APIRouter, HTTPException, Query
from typing import Optional, Dict, Any
from app.utils.capabilities import get_capabilities_provider
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/capabilities", tags=["Capabilities"])

@router.get("/")
async def get_capabilities():
    """
    Get all available capabilities.
    
    Returns dynamic capabilities based on vis_params stored in MongoDB.
    Includes collection metadata, available visualizations, and system limits.
    """
    provider = get_capabilities_provider()
    return await provider.get_capabilities()

@router.get("/legacy")
async def get_legacy_capabilities():
    """
    Get capabilities in legacy format for backward compatibility.
    
    Returns simplified structure matching the original /api/capabilities endpoint.
    """
    provider = get_capabilities_provider()
    capabilities = await provider.get_capabilities()
    
    # Convert to legacy format
    legacy_collections = []
    for coll in capabilities["collections"]:
        legacy_coll = {
            "name": coll["name"],
            "visparam": coll["visparam"],
            "period": coll.get("period", []),
            "year": coll.get("year", [])
        }
        if "months" in coll:
            legacy_coll["month"] = coll["months"]
        legacy_collections.append(legacy_coll)
    
    return {"collections": legacy_collections}

@router.get("/collections")
async def get_collections():
    """
    Get detailed information about available collections.
    
    Returns metadata about Sentinel-2 and Landsat collections including:
    - Available bands
    - Date ranges
    - Cloud filtering options
    - Sensor information
    """
    provider = get_capabilities_provider()
    return provider.COLLECTION_METADATA

@router.get("/collections/{collection_name}")
async def get_collection_details(collection_name: str):
    """
    Get detailed information about a specific collection.
    
    Args:
        collection_name: Name of the collection (e.g., 's2_harmonized', 'landsat')
    
    Returns:
        Detailed collection metadata including bands, sensors, and configurations
    """
    provider = get_capabilities_provider()
    collection_info = provider.get_collection_info(collection_name)
    
    if not collection_info:
        raise HTTPException(
            status_code=404,
            detail=f"Collection '{collection_name}' not found"
        )
    
    return collection_info

@router.get("/vis-params")
async def get_all_vis_params(
    category: Optional[str] = Query(None, description="Filter by category (sentinel/landsat)"),
    active_only: bool = Query(True, description="Return only active vis_params")
):
    """
    Get all available visualization parameters.
    
    Args:
        category: Optional filter by satellite category
        active_only: Whether to return only active vis_params
    
    Returns:
        List of available visualization parameters with details
    """
    provider = get_capabilities_provider()
    capabilities = await provider.get_capabilities()
    
    vis_params = []
    for name, details in capabilities.get("vis_params", {}).items():
        if category and details.get("category") != category:
            continue
        if active_only and not details.get("active", True):
            continue
        
        vis_params.append({
            "name": name,
            **details
        })
    
    return {"vis_params": vis_params, "total": len(vis_params)}

@router.get("/vis-params/{vis_param_name}")
async def get_vis_param_details(vis_param_name: str):
    """
    Get detailed information about a specific visualization parameter.
    
    Args:
        vis_param_name: Name of the vis_param (e.g., 'tvi-green', 'landsat-tvi-true')
    
    Returns:
        Detailed vis_param configuration including bands and visualization settings
    """
    provider = get_capabilities_provider()
    details = await provider.get_vis_param_details(vis_param_name)
    
    if not details:
        raise HTTPException(
            status_code=404,
            detail=f"Visualization parameter '{vis_param_name}' not found"
        )
    
    return details

@router.post("/validate")
async def validate_request(
    collection: str,
    vis_param: str,
    year: int,
    period: Optional[str] = None,
    month: Optional[str] = None
):
    """
    Validate if a tile request would be valid.
    
    Args:
        collection: Collection name (e.g., 's2_harmonized', 'landsat')
        vis_param: Visualization parameter name
        year: Year for the data
        period: Optional period (WET/DRY/MONTH)
        month: Optional month (01-12, for landsat)
    
    Returns:
        Validation result with errors if invalid
    """
    provider = get_capabilities_provider()
    result = await provider.validate_request_params(
        collection=collection,
        vis_param=vis_param,
        year=year,
        period=period,
        month=month
    )
    
    if not result["valid"]:
        raise HTTPException(
            status_code=400,
            detail=result
        )
    
    return {
        "valid": True,
        "message": "Request parameters are valid",
        "collection_info": result.get("collection_data")
    }

@router.get("/years/{collection_name}")
async def get_available_years(collection_name: str):
    """
    Get available years for a specific collection.
    
    Args:
        collection_name: Name of the collection
    
    Returns:
        List of available years and year ranges
    """
    provider = get_capabilities_provider()
    capabilities = await provider.get_capabilities()
    
    for coll in capabilities["collections"]:
        if coll["name"] == collection_name:
            years = coll.get("year", [])
            return {
                "collection": collection_name,
                "years": years,
                "range": {
                    "start": years[0] if years else None,
                    "end": years[-1] if years else None
                },
                "total": len(years)
            }
    
    raise HTTPException(
        status_code=404,
        detail=f"Collection '{collection_name}' not found"
    )

@router.get("/admin/refresh")
async def refresh_capabilities():
    """
    Refresh capabilities cache.
    
    Forces a reload of vis_params from MongoDB.
    """
    # Clear any cache if implemented
    provider = get_capabilities_provider()
    
    # Get fresh capabilities
    capabilities = await provider.get_capabilities()
    
    return {
        "message": "Capabilities refreshed successfully",
        "metadata": capabilities.get("metadata", {})
    }