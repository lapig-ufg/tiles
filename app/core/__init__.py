"""
Core module - configuration, database connections, and common utilities
"""
from .auth import SuperAdminRequired
from .config import logger, settings
from .database import SessionLocal, get_db
from .errors import AppError, TileGenerationError, handle_exception
from .mongodb import get_database, close_mongo_connection, connect_to_mongo

__all__ = [
    'logger', 'settings', 
    'SessionLocal', 'get_db',
    'get_database', 'close_mongo_connection', 'connect_to_mongo',
    'AppError', 'TileGenerationError', 'handle_exception',
    'SuperAdminRequired'
]