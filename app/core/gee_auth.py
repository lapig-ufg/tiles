"""
Google Earth Engine authentication shared between FastAPI and Celery
"""
import ee
from google.oauth2 import service_account

from app.core.config import settings, logger


def initialize_earth_engine():
    """Initialize Google Earth Engine with service account credentials"""
    if ee.data._credentials:
        return
    
    if settings.get("SKIP_GEE_INIT", False):
        logger.warning("Skipping GEE initialization (SKIP_GEE_INIT=true)")
        return
    
    try:
        service_account_file = settings.GEE_SERVICE_ACCOUNT_FILE
        logger.debug(f"Initializing GEE with service account: {service_account_file}")
        
        credentials = service_account.Credentials.from_service_account_file(
            service_account_file,
            scopes=["https://www.googleapis.com/auth/earthengine.readonly"],
        )
        ee.Initialize(credentials)
        logger.info("GEE initialized successfully")
        
    except Exception as e:
        logger.error(f"Failed to initialize GEE: {e}")
        if settings.get("TILES_ENV") != "development":
            raise
        logger.warning("Running in development mode without GEE")


# Singleton pattern to ensure GEE is initialized only once
_gee_initialized = False


async def ensure_gee_initialized():
    """Async wrapper to ensure GEE is initialized (for use in async contexts)"""
    global _gee_initialized
    if not _gee_initialized:
        initialize_earth_engine()
        _gee_initialized = True