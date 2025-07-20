"""
Tasks module - Celery tasks and async processing
Organized into logical categories for better maintainability
"""
from .celery_app import celery_app

# Import all task modules to register them with Celery
from . import tile_tasks
from . import cache_operations
from . import cleanup_tasks
from . import monitoring_tasks

__all__ = [
    'celery_app',
    'tile_tasks',
    'cache_operations', 
    'cleanup_tasks',
    'monitoring_tasks'
]