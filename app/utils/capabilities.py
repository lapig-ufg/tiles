from datetime import datetime
from typing import Dict, Any, Optional
from app.core.mongodb import get_database
from functools import lru_cache
import logging
import time
import asyncio

logger = logging.getLogger(__name__)


class CapabilitiesProvider:
    """Provider for dynamic capabilities based on MongoDB vis_params"""

    # Collection metadata
    COLLECTION_METADATA = {
        "s2_harmonized": {
            "name": "s2_harmonized",
            "display_name": "Sentinel-2 Harmonized",
            "satellite": "sentinel",
            "collections": [
                "COPERNICUS/S2_HARMONIZED",
                "COPERNICUS/S2_SR_HARMONIZED"
            ],
            "period": ["WET", "DRY", "MONTH"],
            "year_start": 2017,
            "bands": {
                "optical": ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"],
                "quality": ["QA60", "MSK_CLDPRB", "SCL"],
                "common_names": {
                    "B1": "AEROSOL",
                    "B2": "BLUE",
                    "B3": "GREEN",
                    "B4": "RED",
                    "B5": "REDEDGE1",
                    "B6": "REDEDGE2",
                    "B7": "REDEDGE3",
                    "B8": "NIR",
                    "B8A": "REDEDGE4",
                    "B9": "WATERVAPOR",
                    "B11": "SWIR1",
                    "B12": "SWIR2"
                }
            },
            "cloud_filter": {
                "property": "CLOUDY_PIXEL_PERCENTAGE",
                "max_value": 20
            }
        },
        "landsat": {
            "name": "landsat",
            "display_name": "Landsat Collection",
            "satellite": "landsat",
            "collections": {
                "LANDSAT/LT05/C02/T1_L2": {"year_range": [1985, 2011], "sensor": "TM"},
                "LANDSAT/LE07/C02/T1_L2": {"year_range": [2012, 2013], "sensor": "ETM+"},
                "LANDSAT/LC08/C02/T1_L2": {"year_range": [2014, 2024], "sensor": "OLI"},
                "LANDSAT/LC09/C02/T1_L2": {"year_range": [2025, 2030], "sensor": "OLI-2"}
            },
            "period": ["WET", "DRY", "MONTH"],
            "months": ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"],
            "year_start": 1985,
            "bands": {
                "optical": {
                    "TM": ["SR_B1", "SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B7"],
                    "ETM+": ["SR_B1", "SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B7"],
                    "OLI": ["SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7"],
                    "OLI-2": ["SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7"]
                },
                "thermal": {
                    "TM": ["ST_B6"],
                    "ETM+": ["ST_B6"],
                    "OLI": ["ST_B10"],
                    "OLI-2": ["ST_B10"]
                },
                "quality": ["QA_PIXEL", "QA_RADSAT"],
                "common_names": {
                    "SR_B1": "BLUE",
                    "SR_B2": "BLUE/GREEN",
                    "SR_B3": "GREEN/RED",
                    "SR_B4": "RED/NIR",
                    "SR_B5": "NIR/SWIR1",
                    "SR_B6": "SWIR1",
                    "SR_B7": "SWIR2"
                }
            },
            "cloud_filter": {
                "property": "CLOUD_COVER",
                "max_value": 20
            }
        }
    }

    def __init__(self):
        self._cache_ttl = 300  # 5 minutes cache
        self._cache = None
        self._cache_timestamp = 0
        self._cache_lock = asyncio.Lock()

    def clear_cache(self):
        """Clear the capabilities cache"""
        self._cache = None
        self._cache_timestamp = 0
        logger.info("Capabilities cache cleared")
    
    async def get_capabilities(self) -> Dict[str, Any]:
        """Get dynamic capabilities based on available vis_params with caching"""
        # Check cache first
        current_time = time.time()
        if self._cache and (current_time - self._cache_timestamp) < self._cache_ttl:
            return self._cache

        # Use lock to prevent multiple simultaneous MongoDB queries
        async with self._cache_lock:
            # Double-check cache inside lock
            if self._cache and (current_time - self._cache_timestamp) < self._cache_ttl:
                return self._cache

            try:
                # Get vis_params from MongoDB or fallback to hardcoded
                db = get_database()

                if db is None:
                    logger.warning("MongoDB not connected, using hardcoded values")
                    result = self._get_hardcoded_capabilities()
                    self._cache = result
                    self._cache_timestamp = current_time
                    return result

                # Fetch active vis_params from MongoDB
                vis_params_cursor = db.vis_params.find({"active": True})
                vis_params_list = await vis_params_cursor.to_list(length=None)

                # If no vis_params in MongoDB, use hardcoded ones
                if not vis_params_list:
                    logger.info("No vis_params in MongoDB, using hardcoded values")
                    result = self._get_hardcoded_capabilities()
                    self._cache = result
                    self._cache_timestamp = current_time
                    return result

                # Build dynamic capabilities
                capabilities = {
                    "collections": [],
                    "vis_params": {},
                    "metadata": {
                        "last_updated": datetime.utcnow().isoformat(),
                        "version": "2.0"
                    }
                }

                # Group vis_params by category
                sentinel_params = []
                landsat_params = []

                for vp in vis_params_list:
                    param_info = {
                        "name": vp["name"],
                        "display_name": vp.get("display_name", vp["name"]),
                        "description": vp.get("description", ""),
                        "tags": vp.get("tags", [])
                    }

                    if vp.get("category") in ["sentinel", "sentinel2"]:
                        sentinel_params.append(param_info)
                    elif vp.get("category") == "landsat":
                        landsat_params.append(param_info)

                    # Store detailed vis_params info
                    capabilities["vis_params"][vp["name"]] = {
                        "category": vp.get("category"),
                        "band_config": vp.get("band_config"),
                        "vis_params": vp.get("vis_params"),
                        "satellite_configs": vp.get("satellite_configs"),
                        "active": vp.get("active", True)
                    }

                # Build collection capabilities
                current_year = datetime.now().year

                # Sentinel-2 collection
                if sentinel_params:
                    s2_meta = self.COLLECTION_METADATA["s2_harmonized"]
                    capabilities["collections"].append({
                        "name": "s2_harmonized",
                        "display_name": s2_meta["display_name"],
                        "satellite": "sentinel",
                        "visparam": [p["name"] for p in sentinel_params],
                        "visparam_details": sentinel_params,
                        "period": s2_meta["period"],
                        "year": list(range(s2_meta["year_start"], current_year + 1)),
                        "collections": s2_meta["collections"],
                        "bands": s2_meta["bands"],
                        "cloud_filter": s2_meta["cloud_filter"]
                    })

                # Landsat collection
                if landsat_params:
                    landsat_meta = self.COLLECTION_METADATA["landsat"]
                    capabilities["collections"].append({
                        "name": "landsat",
                        "display_name": landsat_meta["display_name"],
                        "satellite": "landsat",
                        "visparam": [p["name"] for p in landsat_params],
                        "visparam_details": landsat_params,
                        "period": landsat_meta["period"],
                        "months": landsat_meta["months"],
                        "year": list(range(landsat_meta["year_start"], current_year + 1)),
                        "collections": landsat_meta["collections"],
                        "bands": landsat_meta["bands"],
                        "cloud_filter": landsat_meta["cloud_filter"]
                    })

                # Add additional metadata
                capabilities["metadata"]["total_vis_params"] = len(vis_params_list)
                capabilities["metadata"]["active_sentinel"] = len(sentinel_params)
                capabilities["metadata"]["active_landsat"] = len(landsat_params)

                # Cache the result
                self._cache = capabilities
                self._cache_timestamp = current_time

                return capabilities

            except Exception as e:
                logger.error(f"Error getting capabilities from MongoDB: {e}")
                result = self._get_hardcoded_capabilities()
                self._cache = result
                self._cache_timestamp = current_time
                return result

    def _get_hardcoded_capabilities(self) -> Dict[str, Any]:
        """Fallback to hardcoded capabilities"""
        current_year = datetime.now().year
        return {
            "collections": [
                {
                    "name": "s2_harmonized",
                    "display_name": "Sentinel-2 Harmonized",
                    "satellite": "sentinel",
                    "visparam": ["tvi-green", "tvi-red", "tvi-rgb"],
                    "visparam_details": [
                        {"name": "tvi-green", "display_name": "TVI Green", "description": "SWIR1/REDEDGE4/RED"},
                        {"name": "tvi-red", "display_name": "TVI Red", "description": "REDEDGE4/SWIR1/RED"},
                        {"name": "tvi-rgb", "display_name": "RGB", "description": "Standard RGB"}
                    ],
                    "period": ["WET", "DRY", "MONTH"],
                    "year": list(range(2017, current_year + 1)),
                    "collections": ["COPERNICUS/S2_HARMONIZED"],
                    "cloud_filter": {"property": "CLOUDY_PIXEL_PERCENTAGE", "max_value": 20}
                },
                {
                    "name": "landsat",
                    "display_name": "Landsat Collection",
                    "satellite": "landsat",
                    "visparam": ["landsat-tvi-true", "landsat-tvi-agri", "landsat-tvi-false"],
                    "visparam_details": [
                        {"name": "landsat-tvi-true", "display_name": "True Color", "description": "Natural color RGB"},
                        {"name": "landsat-tvi-agri", "display_name": "Agriculture",
                         "description": "False color for vegetation"},
                        {"name": "landsat-tvi-false", "display_name": "False Color",
                         "description": "Standard false color"}
                    ],
                    "months": ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"],
                    "year": list(range(1985, current_year + 1)),
                    "period": ["WET", "DRY", "MONTH"],
                    "cloud_filter": {"property": "CLOUD_COVER", "max_value": 20}
                }
            ],
            "metadata": {
                "last_updated": datetime.utcnow().isoformat(),
                "version": "1.0",
                "source": "hardcoded"
            }
        }

    @lru_cache(maxsize=1)
    def get_collection_info(self, collection_name: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific collection"""
        return self.COLLECTION_METADATA.get(collection_name)

    async def get_vis_param_details(self, vis_param_name: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific vis_param"""
        try:
            db = get_database()

            if db is None:
                # Try hardcoded fallback
                from app.visualization.visParam import VISPARAMS
                return VISPARAMS.get(vis_param_name)

            vis_param = await db.vis_params.find_one({"name": vis_param_name, "active": True})

            if vis_param:
                return {
                    "name": vis_param["name"],
                    "display_name": vis_param.get("display_name"),
                    "description": vis_param.get("description"),
                    "category": vis_param.get("category"),
                    "tags": vis_param.get("tags", []),
                    "band_config": vis_param.get("band_config"),
                    "vis_params": vis_param.get("vis_params"),
                    "satellite_configs": vis_param.get("satellite_configs")
                }
            else:
                # Try hardcoded fallback
                from app.visualization.visParam import VISPARAMS
                return VISPARAMS.get(vis_param_name)

        except Exception as e:
            logger.error(f"Error getting vis_param details: {e}")
            return None

    async def validate_request_params(self, collection: str, vis_param: str,
                                      year: int, period: str = None, month: str = None) -> Dict[str, Any]:
        """Validate if the requested parameters are available"""
        capabilities = await self.get_capabilities()

        # Find collection
        collection_data = None
        for coll in capabilities["collections"]:
            if coll["name"] == collection:
                collection_data = coll
                break

        if not collection_data:
            return {"valid": False, "error": f"Collection '{collection}' not found"}

        # Validate vis_param
        if vis_param not in collection_data["visparam"]:
            return {
                "valid": False,
                "error": f"vis_param '{vis_param}' not available for collection '{collection}'",
                "available": collection_data["visparam"]
            }

        # Validate year
        if year not in collection_data["year"]:
            return {
                "valid": False,
                "error": f"Year {year} not available for collection '{collection}'",
                "available_range": [collection_data["year"][0], collection_data["year"][-1]]
            }

        # Validate period
        if period and "period" in collection_data:
            if period not in collection_data["period"]:
                return {
                    "valid": False,
                    "error": f"Period '{period}' not available",
                    "available": collection_data["period"]
                }

        # Validate month (for landsat)
        if month and "months" in collection_data:
            if month not in collection_data["months"]:
                return {
                    "valid": False,
                    "error": f"Month '{month}' not available",
                    "available": collection_data["months"]
                }

        return {"valid": True, "collection_data": collection_data}


# Singleton instance
_capabilities_provider = None


def get_capabilities_provider() -> CapabilitiesProvider:
    """Get singleton instance of CapabilitiesProvider"""
    global _capabilities_provider
    if _capabilities_provider is None:
        _capabilities_provider = CapabilitiesProvider()
    return _capabilities_provider


# Legacy compatibility - maintain old CAPABILITIES structure
async def get_capabilities() -> Dict[str, Any]:
    """Get capabilities in legacy format"""
    provider = get_capabilities_provider()
    full_capabilities = await provider.get_capabilities()

    # Convert to legacy format
    legacy_collections = []
    for coll in full_capabilities["collections"]:
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


# For synchronous contexts (backward compatibility)
CAPABILITIES = {
    "collections": [
        {
            "name": "s2_harmonized",
            "visparam": ["tvi-green", "tvi-red", "tvi-rgb"],
            "period": ["WET", "DRY", "MONTH"],
            "year": list(range(2017, datetime.now().year + 1)),
        },
        {
            "name": "landsat",
            "visparam": ["landsat-tvi-true", "landsat-tvi-agri", "landsat-tvi-false"],
            "month": ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"],
            "year": list(range(1985, datetime.now().year + 1)),
            "period": ["WET", "DRY", "MONTH"]
        }
    ]
}
