"""
MongoDB connection and models for TVI collections
"""
from datetime import datetime
from typing import List, Optional, Any, Dict
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from bson import ObjectId
from app.core.config import settings

# MongoDB connection
class MongoDB:
    client: Optional[AsyncIOMotorClient] = None
    database = None

mongodb = MongoDB()

async def connect_to_mongo():
    """Create database connection"""
    mongodb.client = AsyncIOMotorClient(
        settings.get("MONGODB_URL", "mongodb://localhost:27017")
    )
    mongodb.database = mongodb.client[settings.get("MONGODB_DB", "tvi")]

async def close_mongo_connection():
    """Close database connection"""
    if mongodb.client:
        mongodb.client.close()

def get_database():
    """Get database instance"""
    return mongodb.database

# Pydantic models for collections
class User(BaseModel):
    id: str = Field(alias="_id")
    username: Optional[str] = None
    password: str
    role: str
    type: Optional[str] = None
    created_at: Optional[datetime] = Field(alias="createdAt")

    class Config:
        populate_by_name = True

class Campaign(BaseModel):
    id: str = Field(alias="_id")
    initial_year: int = Field(alias="initialYear")
    final_year: int = Field(alias="finalYear")
    password: str
    land_use: List[str] = Field(alias="landUse")
    num_inspec: int = Field(alias="numInspec")
    show_timeseries: bool = Field(alias="showTimeseries")
    show_point_info: bool = Field(alias="showPointInfo")
    vis_param: str = Field(alias="visParam")
    vis_params_enable: List[str] = Field(alias="visParamsEnable")
    use_dynamic_maps: bool = Field(alias="useDynamicMaps")
    image_type: str = Field(alias="imageType")
    geojson_file: Optional[str] = Field(alias="geojsonFile")
    properties: List[Any]
    created_at: datetime = Field(alias="createdAt")
    completed_points: int = Field(alias="completedPoints")
    pending_points: int = Field(alias="pendingPoints")
    progress: int
    total_points: int = Field(alias="totalPoints")
    updated_at: datetime = Field(alias="updatedAt")

    class Config:
        populate_by_name = True

class Inspection(BaseModel):
    counter: int
    form: List[Dict[str, Any]]
    fill_date: datetime = Field(alias="fillDate")

    class Config:
        populate_by_name = True

class Point(BaseModel):
    id: str = Field(alias="_id")
    campaign: str
    lon: float
    lat: float
    date_import: datetime = Field(alias="dateImport")
    biome: Optional[str] = None
    uf: Optional[str] = None
    county: Optional[str] = None
    county_code: Optional[str] = Field(alias="countyCode")
    path: Optional[str] = None
    row: Optional[str] = None
    user_name: List[str] = Field(alias="userName")
    inspection: List[Inspection]
    under_inspection: int = Field(alias="underInspection")
    index: int
    cached: Optional[bool] = False
    enhance_in_cache: Optional[int] = Field(alias="enhance_in_cache")
    properties: Dict[str, Any]
    cached_at: Optional[datetime] = Field(alias="cachedAt")
    cached_by: Optional[str] = Field(alias="cachedBy")
    class_consolidated: Optional[List[str]] = Field(alias="classConsolidated")

    class Config:
        populate_by_name = True

# Collection accessors
async def get_users_collection():
    """Get users collection"""
    db = get_database()
    return db.users

async def get_campaigns_collection():
    """Get campaigns collection"""
    db = get_database()
    return db.campaigns

async def get_points_collection():
    """Get points collection"""
    db = get_database()
    return db.points