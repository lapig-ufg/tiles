"""
MongoDB-based visualization parameters management
Replaces the hardcoded VISPARAMS with database-driven configuration
"""
from typing import Dict, Optional, List, Any, Union
from functools import lru_cache
import asyncio
from app.core.mongodb import get_database
from app.models.vis_params import VisParamDocument, LandsatCollectionMapping
from app.core.config import logger


class VisParamsManager:
    """Manager for visualization parameters stored in MongoDB"""
    
    def __init__(self):
        self._cache: Dict[str, VisParamDocument] = {}
        self._landsat_mappings: Optional[LandsatCollectionMapping] = None
        self._initialized = False
    
    async def initialize(self):
        """Load all vis params into memory cache"""
        if self._initialized:
            return
        
        try:
            db = get_database()
            if db is None:
                logger.warning("MongoDB database not initialized, using empty vis params cache")
                self._initialized = True
                return
            
            collection = db.vis_params
            
            # Load all active vis params
            cursor = collection.find({
                "active": {"$ne": False},
                "_id": {"$nin": ["landsat_collections", "sentinel_collections"]}
            })
            async for doc in cursor:
                try:
                    # Ensure document has required fields
                    if "name" in doc and doc.get("vis_params") or doc.get("satellite_configs"):
                        self._cache[doc["name"]] = VisParamDocument(**doc)
                except Exception as e:
                    logger.warning(f"Failed to load vis param document {doc.get('_id', 'unknown')}: {e}")
            
            # Load Landsat mappings
            landsat_doc = await collection.find_one({"_id": "landsat_collections"})
            if landsat_doc:
                self._landsat_mappings = LandsatCollectionMapping(**landsat_doc)
            
            self._initialized = True
            logger.info(f"Loaded {len(self._cache)} visualization parameters from MongoDB")
            
        except Exception as e:
            logger.error(f"Failed to initialize VisParamsManager: {e}")
            # Fall back to empty cache if DB is unavailable
            self._initialized = True
    
    async def get_vis_param(self, name: str) -> Optional[VisParamDocument]:
        """Get a specific visualization parameter by name"""
        if not self._initialized:
            await self.initialize()
        
        return self._cache.get(name)
    
    async def get_all_vis_params(self) -> Dict[str, VisParamDocument]:
        """Get all visualization parameters"""
        if not self._initialized:
            await self.initialize()
        
        return self._cache.copy()
    
    async def get_by_category(self, category: str) -> List[VisParamDocument]:
        """Get all vis params for a specific category"""
        if not self._initialized:
            await self.initialize()
        
        return [doc for doc in self._cache.values() if doc.category == category]
    
    def get_landsat_collection(self, year: int) -> str:
        """Get the appropriate Landsat collection for a given year"""
        if not self._landsat_mappings:
            # Fallback to hardcoded logic if DB not available
            if 1985 <= year <= 2011:
                return 'LANDSAT/LT05/C02/T1_L2'
            elif 2012 <= year <= 2013:
                return 'LANDSAT/LE07/C02/T1_L2'
            elif 2014 <= year <= 2024:
                return 'LANDSAT/LC08/C02/T1_L2'
            elif year >= 2025:
                return 'LANDSAT/LC09/C02/T1_L2'
            else:
                raise ValueError(f"Year {year} outside supported range (1985 onwards)")
        
        # Use DB mappings
        for mapping in self._landsat_mappings.mappings:
            if mapping["start_year"] <= year <= mapping["end_year"]:
                return mapping["collection"]
        
        raise ValueError(f"No Landsat collection found for year {year}")
    
    async def get_landsat_vis_params(self, vis_type: str, year_or_collection: Union[int, str]) -> Dict[str, Any]:
        """Get Landsat visualization parameters for a specific type and year/collection"""
        if isinstance(year_or_collection, int):
            collection_name = self.get_landsat_collection(year_or_collection)
        else:
            collection_name = year_or_collection
        
        vis_doc = await self.get_vis_param(vis_type)
        if not vis_doc or not vis_doc.satellite_configs:
            raise ValueError(f"No visualization parameters found for {vis_type}")
        
        # Find the matching satellite config
        for sat_config in vis_doc.satellite_configs:
            if sat_config.collection_id == collection_name:
                landsat_params = sat_config.vis_params.model_dump()
                
                # Convert all numeric parameters to strings as expected by Google Earth Engine
                for key in ["min", "max", "gamma"]:
                    if key in landsat_params:
                        if isinstance(landsat_params[key], list):
                            landsat_params[key] = ",".join(map(str, landsat_params[key]))
                        else:
                            # Convert single values to string as well
                            landsat_params[key] = str(landsat_params[key])
                
                return landsat_params
        
        raise ValueError(f"No parameters found for {vis_type} and collection {collection_name}")
    
    async def refresh_cache(self):
        """Refresh the cache from database"""
        self._initialized = False
        self._cache.clear()
        await self.initialize()


# Global instance
vis_params_manager = VisParamsManager()


# Compatibility functions to match existing API
async def get_visparams_dict() -> Dict[str, Dict[str, Any]]:
    """
    Get VISPARAMS-style dictionary for backward compatibility
    Returns the same structure as the original VISPARAMS constant
    """
    all_params = await vis_params_manager.get_all_vis_params()
    result = {}
    
    for name, doc in all_params.items():
        if doc.vis_params:
            # Sentinel-2 style
            vis_params = doc.vis_params.model_dump()
            
            # Convert all numeric parameters to strings as expected by Google Earth Engine
            for key in ["min", "max", "gamma"]:
                if key in vis_params:
                    if isinstance(vis_params[key], list):
                        vis_params[key] = ",".join(map(str, vis_params[key]))
                    else:
                        # Convert single values to string as well
                        vis_params[key] = str(vis_params[key])
            
            entry = {"visparam": vis_params}
            if doc.band_config:
                if doc.band_config.mapped_bands:
                    entry["select"] = (doc.band_config.original_bands, doc.band_config.mapped_bands)
                else:
                    entry["select"] = doc.band_config.original_bands
            result[name] = entry
        elif doc.satellite_configs:
            # Landsat style
            visparam = {}
            for sat_config in doc.satellite_configs:
                landsat_vis_params = sat_config.vis_params.model_dump()
                
                # Convert all numeric parameters to strings as expected by Google Earth Engine
                for key in ["min", "max", "gamma"]:
                    if key in landsat_vis_params:
                        if isinstance(landsat_vis_params[key], list):
                            landsat_vis_params[key] = ",".join(map(str, landsat_vis_params[key]))
                        else:
                            # Convert single values to string as well
                            landsat_vis_params[key] = str(landsat_vis_params[key])
                
                visparam[sat_config.collection_id] = landsat_vis_params
            result[name] = {"visparam": visparam}
    
    return result


def get_landsat_collection(year: int) -> str:
    """Synchronous wrapper for get_landsat_collection"""
    return vis_params_manager.get_landsat_collection(year)


async def get_landsat_vis_params_async(vis_type: str, year_or_collection: Union[int, str]) -> Dict[str, Any]:
    """Async version of get_landsat_vis_params"""
    return await vis_params_manager.get_landsat_vis_params(vis_type, year_or_collection)


def get_landsat_vis_params(vis_type: str, year_or_collection: Union[int, str]) -> Dict[str, Any]:
    """Synchronous wrapper for backward compatibility"""
    loop = asyncio.get_event_loop()
    if loop.is_running():
        # If called from async context, create a task
        future = asyncio.ensure_future(get_landsat_vis_params_async(vis_type, year_or_collection))
        return loop.run_until_complete(future)
    else:
        # If called from sync context
        return loop.run_until_complete(get_landsat_vis_params_async(vis_type, year_or_collection))


def generate_landsat_list(start_year: int, end_year: int) -> List[tuple]:
    """Generate list of (year, collection) tuples for a date range"""
    landsat_list = []
    for year in range(start_year, end_year + 1):
        collection = get_landsat_collection(year)
        landsat_list.append((year, collection))
    return landsat_list