from sqlalchemy import Column, Integer, String,  DateTime

from app.database import Base
from datetime import datetime
from pydantic import BaseModel
from enum import Enum
from typing import List, Optional

class Layer(Base):
    __tablename__ = "layers"

    layer: str = Column(String(120), primary_key=True, index=True)
    url: str = Column(String(255), nullable=False)
    date: datetime = Column(DateTime(), nullable=False)
    
    


class LayerName(str,Enum):
    STATES = 'states'
    BIOMES = 'biomes'
    SICAR = 'sicar'


class GEEConfig(BaseModel):
    service_account_file: str

class RegionRequest(BaseModel):
    coordinates: List[List[float]]

class FilterRequest(BaseModel):
    bioma: Optional[str] = None
    cd_bioma: Optional[int] = None

    
    
  
    
class WET(BaseModel):
    name: str = 'WET'
    dtStart: str = '-01-01'
    dtEnd: str = '-04-30'
    

class DRY(BaseModel):
    name: str = 'DRY'
    dtStart: str = '-06-01'
    dtEnd: str = '-10-30'
    
