from pydantic import BaseModel
from enum import Enum
from typing import List, Optional

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
