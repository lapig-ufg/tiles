"""
Core module - configuration, database connections, and common utilities
"""
from .config import logger, settings
from .database import SessionLocal, get_db
from .mongodb import get_database, close_mongo_connection, connect_to_mongo
from .errors import AppError, TileGenerationError, handle_exception
from .auth import SuperAdminRequired

__all__ = [
    'logger', 'settings', 
    'SessionLocal', 'get_db',
    'get_database', 'close_mongo_connection', 'connect_to_mongo',
    'AppError', 'TileGenerationError', 'handle_exception',
    'SuperAdminRequired'
]