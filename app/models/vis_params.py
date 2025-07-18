"""
MongoDB models for visualization parameters
"""
from datetime import datetime
from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field, field_validator


class BandConfig(BaseModel):
    """Configuration for band selection and mapping"""
    original_bands: List[str] = Field(..., description="Original band names (e.g., B4, B8A)")
    mapped_bands: Optional[List[str]] = Field(None, description="Mapped band names (e.g., RED, SWIR1)")


class VisParam(BaseModel):
    """Visualization parameters for a single configuration"""
    bands: List[str] = Field(..., description="Band names to use for visualization")
    min: Union[str, List[float]] = Field(..., description="Minimum values for each band")
    max: Union[str, List[float]] = Field(..., description="Maximum values for each band")
    gamma: Union[str, float, List[float]] = Field(..., description="Gamma correction value(s)")
    
    @field_validator('min', 'max', mode='before')
    @classmethod
    def parse_string_values(cls, v):
        """Convert comma-separated strings to lists"""
        if isinstance(v, str):
            return [float(x.strip()) for x in v.split(',')]
        return v
    
    @field_validator('gamma', mode='before')
    @classmethod
    def parse_gamma(cls, v):
        """Ensure gamma is numeric"""
        if isinstance(v, str):
            return float(v)
        return v


class SatelliteVisParam(BaseModel):
    """Visualization parameters for a specific satellite/collection"""
    collection_id: str = Field(..., description="Satellite collection ID (e.g., LANDSAT/LC08/C02/T1_L2)")
    vis_params: VisParam = Field(..., description="Visualization parameters for this collection")


class VisParamDocument(BaseModel):
    """MongoDB document for visualization parameters"""
    id: str = Field(alias="_id")
    name: str = Field(..., description="Name of the visualization (e.g., tvi-green, landsat-tvi-true)")
    display_name: str = Field(..., description="Human-readable display name")
    description: Optional[str] = Field(None, description="Description of the visualization")
    category: str = Field(..., description="Category (e.g., sentinel, landsat)")
    
    # For Sentinel-2 style (single config)
    band_config: Optional[BandConfig] = Field(None, description="Band selection configuration")
    vis_params: Optional[VisParam] = Field(None, description="Single visualization parameters")
    
    # For Landsat style (multiple configs per satellite)
    satellite_configs: Optional[List[SatelliteVisParam]] = Field(None, description="Per-satellite configurations")
    
    # Metadata
    tags: List[str] = Field(default_factory=list, description="Tags for filtering")
    active: bool = Field(True, description="Whether this visualization is active")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    
    class Config:
        populate_by_name = True
    
    @field_validator('satellite_configs')
    @classmethod
    def validate_config_type(cls, v, info):
        """Ensure either vis_params or satellite_configs is set, not both"""
        if v and info.data.get('vis_params'):
            raise ValueError("Cannot have both vis_params and satellite_configs")
        if not v and not info.data.get('vis_params'):
            raise ValueError("Must have either vis_params or satellite_configs")
        return v


class LandsatCollectionMapping(BaseModel):
    """Mapping of years to Landsat collections"""
    id: str = Field(default="landsat_collections", alias="_id")
    mappings: List[Dict[str, Any]] = Field(..., description="Year range to collection mappings")
    
    class Config:
        populate_by_name = True


class SentinelCollectionMapping(BaseModel):
    """Mapping and configuration for Sentinel-2 collections"""
    id: str = Field(default="sentinel_collections", alias="_id")
    collections: List[Dict[str, Any]] = Field(..., description="Sentinel-2 collection configurations")
    default_collection: str = Field("COPERNICUS/S2_HARMONIZED", description="Default collection to use")
    cloud_filter_params: Dict[str, Any] = Field(
        default_factory=lambda: {
            "max_cloud_coverage": 20,
            "use_cloud_score": True,
            "cloud_score_threshold": 0.5
        },
        description="Cloud filtering parameters"
    )
    
    class Config:
        populate_by_name = True