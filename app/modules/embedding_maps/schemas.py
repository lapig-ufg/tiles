"""
Pydantic DTOs para o modulo Embedding Maps.
Dataset: GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL (64 bandas A00-A63, 10m, anual 2017-2024)
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# Enums                                                                        #
# --------------------------------------------------------------------------- #

class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ProductType(str, Enum):
    RGB_EMBEDDING = "rgb_embedding"
    PCA = "pca"
    CLUSTERS = "clusters"
    MAGNITUDE = "magnitude"
    CHANGE_DETECTION = "change_detection"


class RoiType(str, Enum):
    BBOX = "bbox"
    POLYGON = "polygon"
    FEATURE_COLLECTION = "feature_collection"


class ExportFormat(str, Enum):
    COG = "COG"
    GEOTIFF = "GeoTIFF"
    CSV = "CSV"
    PARQUET = "Parquet"
    JSON = "JSON"


# --------------------------------------------------------------------------- #
# Request DTOs                                                                 #
# --------------------------------------------------------------------------- #

class RoiConfig(BaseModel):
    roi_type: RoiType
    bbox: Optional[List[float]] = None
    geojson: Optional[Dict[str, Any]] = None

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is not None:
            if len(v) != 4:
                raise ValueError("bbox deve ter 4 valores [west, south, east, north]")
            west, south, east, north = v
            if not (-180 <= west <= 180 and -180 <= east <= 180):
                raise ValueError("longitude fora do intervalo [-180, 180]")
            if not (-90 <= south <= 90 and -90 <= north <= 90):
                raise ValueError("latitude fora do intervalo [-90, 90]")
            if west >= east:
                raise ValueError("west deve ser menor que east")
            if south >= north:
                raise ValueError("south deve ser menor que north")
        return v


class ProcessingConfig(BaseModel):
    scale: int = 10
    crs: str = "EPSG:4326"
    tile_scale: int = 4
    best_effort: bool = True
    max_pixels: int = 1_000_000_000
    sample_size: int = 5000


class ProductConfig(BaseModel):
    product: ProductType
    rgb_bands: Optional[List[int]] = None
    pca_components: int = 3
    kmeans_k: int = 8
    palette: Optional[List[str]] = None
    vis_min: float = -0.3
    vis_max: float = 0.3
    year_b: Optional[int] = None  # segundo ano para change_detection

    @field_validator("rgb_bands")
    @classmethod
    def validate_rgb_bands(cls, v: Optional[List[int]]) -> Optional[List[int]]:
        if v is not None:
            if len(v) != 3:
                raise ValueError("rgb_bands deve ter exatamente 3 indices")
            for idx in v:
                if not (0 <= idx <= 63):
                    raise ValueError(f"indice de banda {idx} fora do intervalo [0, 63]")
        return v


class JobCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    year: int
    roi: RoiConfig
    processing: ProcessingConfig = ProcessingConfig()
    products: List[ProductConfig] = Field(..., min_length=1)

    @field_validator("year")
    @classmethod
    def validate_year(cls, v: int) -> int:
        if not (2017 <= v <= 2024):
            raise ValueError("year deve estar entre 2017 e 2024")
        return v


class ExportRequest(BaseModel):
    products: List[ProductType] = Field(..., min_length=1)
    formats: List[ExportFormat] = Field(..., min_length=1)
    scale: Optional[int] = None
    export_target: str = "s3"


# --------------------------------------------------------------------------- #
# Response DTOs                                                                #
# --------------------------------------------------------------------------- #

class ProductResult(BaseModel):
    product: ProductType
    status: JobStatus
    tile_url_template: Optional[str] = None
    metadata: Dict[str, Any] = {}


class ArtifactInfo(BaseModel):
    id: str
    filename: str
    format: ExportFormat
    size_bytes: Optional[int] = None
    download_url: Optional[str] = None
    product: ProductType
    status: str
    created_at: datetime


class JobResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    config: Dict[str, Any] = {}
    status: JobStatus
    progress: int = 0
    message: Optional[str] = None
    products: List[ProductResult] = []
    artifacts: List[ArtifactInfo] = []
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class JobListResponse(BaseModel):
    items: List[JobResponse]
    total: int
    limit: int
    offset: int


class StatsResponse(BaseModel):
    job_id: str
    product: ProductType
    bands: List[Dict[str, Any]] = []
    total_pixels: int = 0
    coverage: float = 0.0
