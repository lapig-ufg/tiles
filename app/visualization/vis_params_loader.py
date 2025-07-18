"""
Loader for visualization parameters with fallback support
Allows switching between MongoDB and hardcoded configurations
"""
import os
from typing import Dict, Any, Union
from app.core.config import settings, logger


# Check if we should use MongoDB for vis params
USE_MONGODB_VIS_PARAMS = settings.get("USE_MONGODB_VIS_PARAMS", True)


if USE_MONGODB_VIS_PARAMS:
    try:
        # Try to import MongoDB-based implementation
        from app.visualization.vis_params_db import (
            get_visparams_dict,
            get_landsat_collection,
            get_landsat_vis_params,
            generate_landsat_list,
            vis_params_manager
        )
        
        # Initialize on import
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if not loop.is_running():
                loop.run_until_complete(vis_params_manager.initialize())
        except:
            # If loop handling fails, it will be initialized on first use
            pass
        
        # Export async function to get VISPARAMS
        async def get_VISPARAMS():
            """Get VISPARAMS dictionary from MongoDB"""
            return await get_visparams_dict()
        
        logger.info("Using MongoDB-based visualization parameters")
        
    except Exception as e:
        logger.warning(f"Failed to load MongoDB vis params, falling back to hardcoded: {e}")
        USE_MONGODB_VIS_PARAMS = False


if not USE_MONGODB_VIS_PARAMS:
    # Use hardcoded implementation
    from app.visualization.visParam import (
        VISPARAMS,
        get_landsat_collection,
        get_landsat_vis_params,
        generate_landsat_list
    )
    
    # Create async wrapper for consistency
    async def get_VISPARAMS():
        """Get hardcoded VISPARAMS dictionary"""
        return VISPARAMS
    
    logger.info("Using hardcoded visualization parameters")


# Export a synchronous VISPARAMS for backward compatibility
# This will be populated on first access
_VISPARAMS_CACHE = None


def get_VISPARAMS_sync():
    """Get VISPARAMS synchronously with caching"""
    global _VISPARAMS_CACHE
    
    if _VISPARAMS_CACHE is None:
        if USE_MONGODB_VIS_PARAMS:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Can't run sync in async context, return empty dict
                    # The async code should use get_VISPARAMS() instead
                    logger.warning("Attempted to get VISPARAMS synchronously in async context")
                    return {}
                else:
                    _VISPARAMS_CACHE = loop.run_until_complete(get_VISPARAMS())
            except:
                # Fallback to hardcoded if async fails
                from app.visualization.visParam import VISPARAMS as HARDCODED_VISPARAMS
                _VISPARAMS_CACHE = HARDCODED_VISPARAMS
        else:
            from app.visualization.visParam import VISPARAMS as HARDCODED_VISPARAMS
            _VISPARAMS_CACHE = HARDCODED_VISPARAMS
    
    return _VISPARAMS_CACHE


# For backward compatibility, create a property-like access
class VISPARAMSProxy:
    def __getitem__(self, key):
        params = get_VISPARAMS_sync()
        return params[key]
    
    def get(self, key, default=None):
        params = get_VISPARAMS_sync()
        return params.get(key, default)
    
    def __contains__(self, key):
        params = get_VISPARAMS_sync()
        return key in params
    
    def keys(self):
        params = get_VISPARAMS_sync()
        return params.keys()
    
    def values(self):
        params = get_VISPARAMS_sync()
        return params.values()
    
    def items(self):
        params = get_VISPARAMS_sync()
        return params.items()


# Export VISPARAMS as a proxy object for backward compatibility
VISPARAMS = VISPARAMSProxy()


# Export all functions
__all__ = [
    'VISPARAMS',
    'get_VISPARAMS',
    'get_VISPARAMS_sync',
    'get_landsat_collection',
    'get_landsat_vis_params',
    'generate_landsat_list',
    'USE_MONGODB_VIS_PARAMS'
]