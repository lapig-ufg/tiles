from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Integer, String

from app.core.database import Base


class Layer(Base):
    __tablename__ = "layers"

    layer: str = Column(String(120), primary_key=True, index=True)
    url: str = Column(String(255), nullable=False)
    date: datetime = Column(DateTime(), nullable=False)


class LayerName(str, Enum):
    STATES = "states"
    BIOMES = "biomes"
    SICAR = "sicar"


class GEEConfig(BaseModel):
    service_account_file: str


class RegionRequest(BaseModel):
    coordinates: List[List[float]]


class FilterRequest(BaseModel):
    bioma: Optional[str] = None
    cd_bioma: Optional[int] = None
