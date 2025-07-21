"""
API endpoints for visualization parameters management
Allows CRUD operations on vis_params stored in MongoDB
"""
from datetime import datetime
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field, field_validator

from app.core.auth import SuperAdminRequired
from app.core.config import logger
from app.core.mongodb import get_database
from app.models.vis_params import (
    VisParamDocument, BandConfig, VisParam,
    SatelliteVisParam, LandsatCollectionMapping,
    SentinelCollectionMapping
)
from app.visualization.vis_params_db import vis_params_manager

router = APIRouter(
    prefix="/api/vis-params",
    tags=["Visualization Parameters"],
    dependencies=[SuperAdminRequired]  # Protege todos os endpoints
)


# Request/Response Models
class VisParamCreateRequest(BaseModel):
    """Request to create a new visualization parameter"""
    name: str = Field(..., description="Unique name identifier")
    display_name: str = Field(..., description="Human-readable display name")
    description: Optional[str] = Field(None, description="Description")
    category: str = Field(..., description="Category (sentinel2, landsat)")
    
    # For Sentinel-2 style
    band_config: Optional[BandConfig] = None
    vis_params: Optional[VisParam] = None
    
    # For Landsat style
    satellite_configs: Optional[List[SatelliteVisParam]] = None
    
    tags: List[str] = Field(default_factory=list)
    active: bool = Field(True)
    
    @field_validator('satellite_configs')
    @classmethod
    def validate_config_type(cls, v, info):
        if v and info.data.get('vis_params'):
            raise ValueError("Cannot have both vis_params and satellite_configs")
        if not v and not info.data.get('vis_params'):
            raise ValueError("Must have either vis_params or satellite_configs")
        return v


class VisParamUpdateRequest(BaseModel):
    """Request to update visualization parameters"""
    display_name: Optional[str] = None
    description: Optional[str] = None
    band_config: Optional[BandConfig] = None
    vis_params: Optional[VisParam] = None
    satellite_configs: Optional[List[SatelliteVisParam]] = None
    tags: Optional[List[str]] = None
    active: Optional[bool] = None


class VisParamTestRequest(BaseModel):
    """Request to test visualization parameters"""
    vis_params: VisParam
    x: int = Field(..., description="Tile X coordinate")
    y: int = Field(..., description="Tile Y coordinate")  
    z: int = Field(..., description="Zoom level")
    layer_type: str = Field("sentinel2", description="Layer type (sentinel2, landsat)")


# CRUD Endpoints
@router.get("/", response_model=List[Dict[str, Any]])
async def list_vis_params(
    category: Optional[str] = Query(None, description="Filter by category"),
    active: Optional[bool] = Query(None, description="Filter by active status"),
    tag: Optional[str] = Query(None, description="Filter by tag")
):
    """
    List all visualization parameters with optional filters
    
    Query parameters:
    - category: Filter by category (sentinel2, landsat)
    - active: Filter by active status
    - tag: Filter by tag
    """
    try:
        db = get_database()
        collection = db.vis_params
        
        # Build filter
        filter_query = {}
        if category:
            filter_query["category"] = category
        if active is not None:
            filter_query["active"] = active
        if tag:
            filter_query["tags"] = tag
        
        # Exclude landsat_collections document
        filter_query["_id"] = {"$ne": "landsat_collections"}
        
        cursor = collection.find(filter_query)
        results = []
        async for doc in cursor:
            results.append(doc)
        
        return results
        
    except Exception as e:
        logger.exception(f"Error listing vis params: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{name}", response_model=Dict[str, Any])
async def get_vis_param(name: str):
    """Get a specific visualization parameter by name"""
    try:
        db = get_database()
        collection = db.vis_params
        
        doc = await collection.find_one({"_id": name})
        if not doc:
            raise HTTPException(status_code=404, detail=f"Vis param '{name}' not found")
        
        return doc
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error getting vis param: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/", response_model=Dict[str, Any])
async def create_vis_param(
    request: VisParamCreateRequest,
    background_tasks: BackgroundTasks
):
    """
    Create a new visualization parameter
    
    The name must be unique and will be used as the document ID.
    """
    try:
        db = get_database()
        collection = db.vis_params
        
        # Check if already exists
        existing = await collection.find_one({"_id": request.name})
        if existing:
            raise HTTPException(
                status_code=409, 
                detail=f"Vis param '{request.name}' already exists"
            )
        
        # Create document
        doc = VisParamDocument(
            _id=request.name,
            name=request.name,
            display_name=request.display_name,
            description=request.description,
            category=request.category,
            band_config=request.band_config,
            vis_params=request.vis_params,
            satellite_configs=request.satellite_configs,
            tags=request.tags,
            active=request.active,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        
        # Insert
        await collection.insert_one(doc.model_dump(by_alias=True))
        
        # Refresh cache in background
        background_tasks.add_task(vis_params_manager.refresh_cache)
        
        logger.info(f"Created vis param: {request.name}")
        
        return doc.model_dump(by_alias=True)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error creating vis param: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{name}", response_model=Dict[str, Any])
async def update_vis_param(
    name: str,
    request: VisParamUpdateRequest,
    background_tasks: BackgroundTasks
):
    """
    Update an existing visualization parameter
    
    Only provided fields will be updated.
    """
    try:
        db = get_database()
        collection = db.vis_params
        
        # Check if exists
        existing = await collection.find_one({"_id": name})
        if not existing:
            raise HTTPException(status_code=404, detail=f"Vis param '{name}' not found")
        
        # Build update
        update_data = {"updated_at": datetime.now()}
        
        if request.display_name is not None:
            update_data["display_name"] = request.display_name
        if request.description is not None:
            update_data["description"] = request.description
        if request.band_config is not None:
            update_data["band_config"] = request.band_config.model_dump()
        if request.vis_params is not None:
            update_data["vis_params"] = request.vis_params.model_dump()
        if request.satellite_configs is not None:
            update_data["satellite_configs"] = [
                sc.model_dump() for sc in request.satellite_configs
            ]
        if request.tags is not None:
            update_data["tags"] = request.tags
        if request.active is not None:
            update_data["active"] = request.active
        
        # Update
        result = await collection.update_one(
            {"_id": name},
            {"$set": update_data}
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=400, detail="No changes made")
        
        # Refresh cache in background
        background_tasks.add_task(vis_params_manager.refresh_cache)
        
        # Get updated document
        updated = await collection.find_one({"_id": name})
        
        logger.info(f"Updated vis param: {name}")
        
        return updated
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error updating vis param: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{name}")
async def delete_vis_param(
    name: str,
    background_tasks: BackgroundTasks
):
    """
    Delete a visualization parameter
    
    This action cannot be undone. Consider deactivating instead.
    """
    try:
        db = get_database()
        collection = db.vis_params
        
        # Check if exists
        existing = await collection.find_one({"_id": name})
        if not existing:
            raise HTTPException(status_code=404, detail=f"Vis param '{name}' not found")
        
        # Delete
        result = await collection.delete_one({"_id": name})
        
        if result.deleted_count == 0:
            raise HTTPException(status_code=500, detail="Failed to delete")
        
        # Refresh cache in background
        background_tasks.add_task(vis_params_manager.refresh_cache)
        
        logger.info(f"Deleted vis param: {name}")
        
        return {"status": "deleted", "name": name}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error deleting vis param: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{name}/toggle")
async def toggle_vis_param(
    name: str,
    background_tasks: BackgroundTasks
):
    """Toggle the active status of a visualization parameter"""
    try:
        db = get_database()
        collection = db.vis_params
        
        # Get current status
        existing = await collection.find_one({"_id": name})
        if not existing:
            raise HTTPException(status_code=404, detail=f"Vis param '{name}' not found")
        
        new_status = not existing.get("active", True)
        
        # Update
        await collection.update_one(
            {"_id": name},
            {"$set": {"active": new_status, "updated_at": datetime.now()}}
        )
        
        # Refresh cache in background
        background_tasks.add_task(vis_params_manager.refresh_cache)
        
        logger.info(f"Toggled vis param '{name}' to active={new_status}")
        
        return {"name": name, "active": new_status}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error toggling vis param: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Utility Endpoints
@router.post("/test")
async def test_vis_params(request: VisParamTestRequest):
    """
    Test visualization parameters by generating a sample tile URL
    
    This endpoint helps validate vis params before saving them.
    """
    try:
        import ee
        from app.services.tile import tile2goehashBBOX
        
        # Get tile bounds
        _, bbox = tile2goehashBBOX(request.x, request.y, request.z)
        geom = ee.Geometry.BBox(bbox["w"], bbox["s"], bbox["e"], bbox["n"])
        
        # Generate based on layer type
        if request.layer_type == "sentinel2":
            # Test with Sentinel-2
            s2 = ee.ImageCollection("COPERNICUS/S2_HARMONIZED").filterBounds(geom).first()
            
            # Apply vis params
            vis_dict = request.vis_params.model_dump()
            # Convert string values to proper format
            for key in ["min", "max"]:
                if isinstance(vis_dict.get(key), str):
                    vis_dict[key] = vis_dict[key].replace(" ", "")
            
            map_id = ee.data.getMapId({"image": s2, **vis_dict})
            tile_url = map_id["tile_fetcher"].url_format
            
        else:
            # Test with Landsat
            landsat = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2").filterBounds(geom).first()
            
            # Apply vis params
            vis_dict = request.vis_params.model_dump()
            map_id = ee.data.getMapId({"image": landsat, **vis_dict})
            tile_url = map_id["tile_fetcher"].url_format
        
        return {
            "status": "success",
            "test_url": tile_url.format(x=request.x, y=request.y, z=request.z),
            "vis_params": vis_dict
        }
        
    except Exception as e:
        logger.exception(f"Error testing vis params: {e}")
        return {
            "status": "error",
            "error": str(e),
            "message": "Failed to generate test tile. Check your parameters."
        }


@router.post("/clone/{name}")
async def clone_vis_param(
    name: str,
    background_tasks: BackgroundTasks,
    new_name: str = Query(..., description="Name for the cloned vis param")
):
    """
    Clone an existing visualization parameter with a new name
    
    Useful for creating variations of existing configurations.
    """
    try:
        db = get_database()
        collection = db.vis_params
        
        # Get original
        original = await collection.find_one({"_id": name})
        if not original:
            raise HTTPException(status_code=404, detail=f"Vis param '{name}' not found")
        
        # Check if new name exists
        existing = await collection.find_one({"_id": new_name})
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Vis param '{new_name}' already exists"
            )
        
        # Create clone
        clone = original.copy()
        clone["_id"] = new_name
        clone["name"] = new_name
        clone["display_name"] = f"{original['display_name']} (Copy)"
        clone["created_at"] = datetime.now()
        clone["updated_at"] = datetime.now()
        
        # Insert
        await collection.insert_one(clone)
        
        # Refresh cache in background
        background_tasks.add_task(vis_params_manager.refresh_cache)
        
        logger.info(f"Cloned vis param '{name}' to '{new_name}'")
        
        return clone
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error cloning vis param: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export/all")
async def export_vis_params():
    """
    Export all visualization parameters as JSON
    
    Useful for backup or migration purposes.
    """
    try:
        db = get_database()
        collection = db.vis_params
        
        # Get all except landsat_collections
        cursor = collection.find({"_id": {"$ne": "landsat_collections"}})
        params = []
        async for doc in cursor:
            params.append(doc)
        
        return {
            "export_date": datetime.now().isoformat(),
            "count": len(params),
            "vis_params": params
        }
        
    except Exception as e:
        logger.exception(f"Error exporting vis params: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/import")
async def import_vis_params(
    data: Dict[str, Any],
    background_tasks: BackgroundTasks,
    overwrite: bool = Query(False, description="Overwrite existing params")
):
    """
    Import visualization parameters from JSON
    
    Expected format:
    {
        "vis_params": [
            {...vis_param_document...},
            {...vis_param_document...}
        ]
    }
    """
    try:
        db = get_database()
        collection = db.vis_params
        
        if "vis_params" not in data:
            raise HTTPException(status_code=400, detail="Missing 'vis_params' in data")
        
        imported = 0
        skipped = 0
        errors = []
        
        for param in data["vis_params"]:
            try:
                # Check if exists
                existing = await collection.find_one({"_id": param["_id"]})
                
                if existing and not overwrite:
                    skipped += 1
                    continue
                
                # Update timestamps
                param["updated_at"] = datetime.now()
                if not existing:
                    param["created_at"] = datetime.now()
                
                # Upsert
                await collection.replace_one(
                    {"_id": param["_id"]},
                    param,
                    upsert=True
                )
                imported += 1
                
            except Exception as e:
                errors.append({
                    "name": param.get("_id", "unknown"),
                    "error": str(e)
                })
        
        # Refresh cache in background
        background_tasks.add_task(vis_params_manager.refresh_cache)
        
        return {
            "imported": imported,
            "skipped": skipped,
            "errors": errors
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error importing vis params: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Landsat Collections Management
@router.get("/landsat-collections", response_model=Dict[str, Any])
async def get_landsat_collections():
    """Get Landsat collection mappings"""
    try:
        db = get_database()
        collection = db.vis_params
        
        doc = await collection.find_one({"_id": "landsat_collections"})
        if not doc:
            raise HTTPException(status_code=404, detail="Landsat collections not found")
        
        return doc
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error getting landsat collections: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/landsat-collections")
async def update_landsat_collections(
    mappings: List[Dict[str, Any]],
    background_tasks: BackgroundTasks
):
    """
    Update Landsat collection mappings
    
    Expected format:
    [
        {
            "start_year": 1985,
            "end_year": 2011,
            "collection": "LANDSAT/LT05/C02/T1_L2",
            "satellite": "Landsat 5"
        },
        ...
    ]
    """
    try:
        db = get_database()
        collection = db.vis_params
        
        # Validate mappings
        for mapping in mappings:
            required_fields = ["start_year", "end_year", "collection", "satellite"]
            for field in required_fields:
                if field not in mapping:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Missing required field: {field}"
                    )
        
        # Update
        doc = LandsatCollectionMapping(
            _id="landsat_collections",
            mappings=mappings
        )
        
        await collection.replace_one(
            {"_id": "landsat_collections"},
            doc.model_dump(by_alias=True),
            upsert=True
        )
        
        # Refresh cache in background
        background_tasks.add_task(vis_params_manager.refresh_cache)
        
        logger.info("Updated Landsat collection mappings")
        
        return doc.model_dump(by_alias=True)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error updating landsat collections: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Sentinel-2 Collections Management
@router.get("/sentinel-collections", response_model=Dict[str, Any])
async def get_sentinel_collections():
    """Get Sentinel-2 collection configurations"""
    try:
        db = get_database()
        collection = db.vis_params
        
        doc = await collection.find_one({"_id": "sentinel_collections"})
        if not doc:
            # Return default if not found
            default = SentinelCollectionMapping()
            return default.model_dump(by_alias=True)
        
        return doc
        
    except Exception as e:
        logger.exception(f"Error getting sentinel collections: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/sentinel-collections")
async def update_sentinel_collections(
    data: Dict[str, Any],
    background_tasks: BackgroundTasks
):
    """
    Update Sentinel-2 collection configurations
    
    Expected format:
    {
        "collections": [
            {
                "name": "COPERNICUS/S2_HARMONIZED",
                "display_name": "Sentinel-2 Harmonized",
                "description": "Harmonized Sentinel-2 MSI: MultiSpectral Instrument, Level-2A",
                "start_date": "2015-06-27",
                "bands": {
                    "B1": {"name": "B1", "description": "Aerosols", "wavelength": "443nm", "resolution": "60m"},
                    "B2": {"name": "B2", "description": "Blue", "wavelength": "490nm", "resolution": "10m"},
                    "B3": {"name": "B3", "description": "Green", "wavelength": "560nm", "resolution": "10m"},
                    "B4": {"name": "B4", "description": "Red", "wavelength": "665nm", "resolution": "10m"},
                    "B5": {"name": "B5", "description": "Red Edge 1", "wavelength": "705nm", "resolution": "20m"},
                    "B6": {"name": "B6", "description": "Red Edge 2", "wavelength": "740nm", "resolution": "20m"},
                    "B7": {"name": "B7", "description": "Red Edge 3", "wavelength": "783nm", "resolution": "20m"},
                    "B8": {"name": "B8", "description": "NIR", "wavelength": "842nm", "resolution": "10m"},
                    "B8A": {"name": "B8A", "description": "Red Edge 4", "wavelength": "865nm", "resolution": "20m"},
                    "B9": {"name": "B9", "description": "Water Vapor", "wavelength": "945nm", "resolution": "60m"},
                    "B10": {"name": "B10", "description": "Cirrus", "wavelength": "1375nm", "resolution": "60m"},
                    "B11": {"name": "B11", "description": "SWIR 1", "wavelength": "1610nm", "resolution": "20m"},
                    "B12": {"name": "B12", "description": "SWIR 2", "wavelength": "2190nm", "resolution": "20m"}
                }
            }
        ],
        "default_collection": "COPERNICUS/S2_HARMONIZED",
        "cloud_filter_params": {
            "max_cloud_coverage": 20,
            "use_cloud_score": true,
            "cloud_score_threshold": 0.5
        }
    }
    """
    try:
        db = get_database()
        collection = db.vis_params
        
        # Validate required fields
        if "collections" not in data:
            raise HTTPException(status_code=400, detail="Missing 'collections' field")
        
        # Create document
        doc = SentinelCollectionMapping(
            _id="sentinel_collections",
            collections=data["collections"],
            default_collection=data.get("default_collection", "COPERNICUS/S2_HARMONIZED"),
            cloud_filter_params=data.get("cloud_filter_params", {
                "max_cloud_coverage": 20,
                "use_cloud_score": True,
                "cloud_score_threshold": 0.5
            })
        )
        
        # Upsert
        await collection.replace_one(
            {"_id": "sentinel_collections"},
            doc.model_dump(by_alias=True),
            upsert=True
        )
        
        # Refresh cache in background
        background_tasks.add_task(vis_params_manager.refresh_cache)
        
        logger.info("Updated Sentinel-2 collection configurations")
        
        return doc.model_dump(by_alias=True)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error updating sentinel collections: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sentinel-collections/initialize")
async def initialize_sentinel_collections(background_tasks: BackgroundTasks):
    """
    Initialize Sentinel-2 collections with default configuration
    
    This will create a default configuration with all standard Sentinel-2 bands
    and their properties.
    """
    try:
        db = get_database()
        collection = db.vis_params
        
        # Check if already exists
        existing = await collection.find_one({"_id": "sentinel_collections"})
        if existing:
            raise HTTPException(
                status_code=409,
                detail="Sentinel collections already initialized. Use PUT to update."
            )
        
        # Create default configuration
        default_config = {
            "_id": "sentinel_collections",
            "collections": [
                {
                    "name": "COPERNICUS/S2_HARMONIZED",
                    "display_name": "Sentinel-2 Harmonized",
                    "description": "Harmonized Sentinel-2 MSI: MultiSpectral Instrument, Level-2A",
                    "start_date": "2015-06-27",
                    "end_date": None,
                    "bands": {
                        "B1": {"name": "B1", "description": "Aerosols", "wavelength": "443nm", "resolution": "60m"},
                        "B2": {"name": "B2", "description": "Blue", "wavelength": "490nm", "resolution": "10m"},
                        "B3": {"name": "B3", "description": "Green", "wavelength": "560nm", "resolution": "10m"},
                        "B4": {"name": "B4", "description": "Red", "wavelength": "665nm", "resolution": "10m"},
                        "B5": {"name": "B5", "description": "Red Edge 1", "wavelength": "705nm", "resolution": "20m"},
                        "B6": {"name": "B6", "description": "Red Edge 2", "wavelength": "740nm", "resolution": "20m"},
                        "B7": {"name": "B7", "description": "Red Edge 3", "wavelength": "783nm", "resolution": "20m"},
                        "B8": {"name": "B8", "description": "NIR", "wavelength": "842nm", "resolution": "10m"},
                        "B8A": {"name": "B8A", "description": "Red Edge 4", "wavelength": "865nm", "resolution": "20m"},
                        "B9": {"name": "B9", "description": "Water Vapor", "wavelength": "945nm", "resolution": "60m"},
                        "B10": {"name": "B10", "description": "Cirrus", "wavelength": "1375nm", "resolution": "60m"},
                        "B11": {"name": "B11", "description": "SWIR 1", "wavelength": "1610nm", "resolution": "20m"},
                        "B12": {"name": "B12", "description": "SWIR 2", "wavelength": "2190nm", "resolution": "20m"},
                        "QA10": {"name": "QA10", "description": "Cloud mask (10m)", "resolution": "10m"},
                        "QA20": {"name": "QA20", "description": "Cloud mask (20m)", "resolution": "20m"},
                        "QA60": {"name": "QA60", "description": "Cloud mask (60m)", "resolution": "60m"}
                    },
                    "quality_bands": ["QA10", "QA20", "QA60"],
                    "metadata_properties": [
                        "CLOUDY_PIXEL_PERCENTAGE",
                        "CLOUD_COVERAGE_ASSESSMENT",
                        "DATASTRIP_ID",
                        "DATATAKE_IDENTIFIER",
                        "GENERATION_TIME",
                        "GRANULE_ID",
                        "MEAN_INCIDENCE_AZIMUTH_ANGLE",
                        "MEAN_INCIDENCE_ZENITH_ANGLE",
                        "MEAN_SOLAR_AZIMUTH_ANGLE",
                        "MEAN_SOLAR_ZENITH_ANGLE",
                        "MGRS_TILE",
                        "PROCESSING_BASELINE",
                        "PRODUCT_ID",
                        "SENSING_ORBIT_DIRECTION",
                        "SENSING_ORBIT_NUMBER",
                        "SOLAR_IRRADIANCE"
                    ]
                },
                {
                    "name": "COPERNICUS/S2_SR_HARMONIZED",
                    "display_name": "Sentinel-2 SR Harmonized",
                    "description": "Harmonized Sentinel-2 Surface Reflectance",
                    "start_date": "2017-03-28",
                    "end_date": None,
                    "bands": {
                        "B1": {"name": "B1", "description": "Aerosols", "wavelength": "443nm", "resolution": "60m"},
                        "B2": {"name": "B2", "description": "Blue", "wavelength": "490nm", "resolution": "10m"},
                        "B3": {"name": "B3", "description": "Green", "wavelength": "560nm", "resolution": "10m"},
                        "B4": {"name": "B4", "description": "Red", "wavelength": "665nm", "resolution": "10m"},
                        "B5": {"name": "B5", "description": "Red Edge 1", "wavelength": "705nm", "resolution": "20m"},
                        "B6": {"name": "B6", "description": "Red Edge 2", "wavelength": "740nm", "resolution": "20m"},
                        "B7": {"name": "B7", "description": "Red Edge 3", "wavelength": "783nm", "resolution": "20m"},
                        "B8": {"name": "B8", "description": "NIR", "wavelength": "842nm", "resolution": "10m"},
                        "B8A": {"name": "B8A", "description": "Red Edge 4", "wavelength": "865nm", "resolution": "20m"},
                        "B9": {"name": "B9", "description": "Water Vapor", "wavelength": "945nm", "resolution": "60m"},
                        "B11": {"name": "B11", "description": "SWIR 1", "wavelength": "1610nm", "resolution": "20m"},
                        "B12": {"name": "B12", "description": "SWIR 2", "wavelength": "2190nm", "resolution": "20m"},
                        "SCL": {"name": "SCL", "description": "Scene Classification Map", "resolution": "20m"},
                        "MSK_CLDPRB": {"name": "MSK_CLDPRB", "description": "Cloud Probability", "resolution": "20m"},
                        "MSK_SNWPRB": {"name": "MSK_SNWPRB", "description": "Snow Probability", "resolution": "20m"}
                    },
                    "quality_bands": ["SCL", "MSK_CLDPRB", "MSK_SNWPRB"]
                }
            ],
            "default_collection": "COPERNICUS/S2_HARMONIZED",
            "cloud_filter_params": {
                "max_cloud_coverage": 20,
                "use_cloud_score": True,
                "cloud_score_threshold": 0.5,
                "use_qa_band": True,
                "qa_band": "QA60",
                "cloud_bit": 10,
                "cirrus_bit": 11
            }
        }
        
        # Insert
        await collection.insert_one(default_config)
        
        # Refresh cache in background
        background_tasks.add_task(vis_params_manager.refresh_cache)
        
        logger.info("Initialized Sentinel-2 collections with default configuration")
        
        return {
            "status": "success",
            "message": "Sentinel-2 collections initialized with default configuration",
            "collections_count": len(default_config["collections"])
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error initializing sentinel collections: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sentinel-collections/bands/{collection_name}")
async def get_sentinel_bands(collection_name: str = "COPERNICUS/S2_HARMONIZED"):
    """
    Get band information for a specific Sentinel-2 collection
    
    Returns detailed information about all bands available in the collection.
    """
    try:
        db = get_database()
        collection = db.vis_params
        
        doc = await collection.find_one({"_id": "sentinel_collections"})
        if not doc:
            raise HTTPException(status_code=404, detail="Sentinel collections not configured")
        
        # Find the requested collection
        for coll in doc.get("collections", []):
            if coll["name"] == collection_name:
                return {
                    "collection": collection_name,
                    "bands": coll.get("bands", {}),
                    "quality_bands": coll.get("quality_bands", [])
                }
        
        raise HTTPException(status_code=404, detail=f"Collection '{collection_name}' not found")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error getting sentinel bands: {e}")
        raise HTTPException(status_code=500, detail=str(e))