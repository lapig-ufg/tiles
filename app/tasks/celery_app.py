"""
Celery configuration and initialization
Optimized for high-performance tile processing and caching
"""
from celery import Celery
from kombu import Queue, Exchange
from app.core.config import settings
from celery.schedules import crontab

# Create Celery instance
celery_app = Celery(
    "tiles",
    broker=settings.get("CELERY_BROKER_URL", "redis://valkey:6379/1"),
    backend=settings.get("CELERY_RESULT_BACKEND", "redis://valkey:6379/2"),
)

# Define exchanges and queues for better routing
default_exchange = Exchange('default', type='direct')
priority_exchange = Exchange('priority', type='direct')

celery_app.conf.task_queues = (
    # High priority queue for user-initiated tasks
    Queue('high_priority', priority_exchange, routing_key='high_priority',
          queue_arguments={'x-max-priority': 10}),
    
    # Standard queue for regular processing
    Queue('standard', default_exchange, routing_key='standard',
          queue_arguments={'x-max-priority': 5}),
    
    # Low priority queue for batch operations
    Queue('low_priority', default_exchange, routing_key='low_priority',
          queue_arguments={'x-max-priority': 1}),
    
    # Maintenance queue for cleanup and monitoring
    Queue('maintenance', default_exchange, routing_key='maintenance'),
)

# Optimized Celery configuration
celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    
    # Connection settings
    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,
    broker_connection_max_retries=10,
    
    # Task execution settings
    task_acks_late=True,  # Tasks acknowledged after completion
    task_reject_on_worker_lost=True,
    task_ignore_result=False,
    
    # Performance optimizations
    worker_prefetch_multiplier=4,  # Balanced prefetching
    worker_max_tasks_per_child=1000,  # Restart worker after N tasks
    worker_disable_rate_limits=False,
    
    # Timeouts
    task_soft_time_limit=600,  # 10 minutes
    task_time_limit=900,  # 15 minutes
    
    # Result backend settings
    result_expires=3600,  # Results expire after 1 hour
    result_persistent=True,
    result_compression='gzip',
    
    # Task routing
    task_routes={
        # Tile generation tasks
        'app.tasks.tile_tasks.tile_generate': {'queue': 'standard'},
        'app.tasks.tile_tasks.tile_generate_batch': {'queue': 'standard'},
        'app.tasks.tile_tasks.tile_generate_mosaic': {'queue': 'high_priority'},
        
        # Cache operation tasks
        'app.tasks.cache_operations.cache_campaign': {'queue': 'high_priority'},
        'app.tasks.cache_operations.cache_point': {'queue': 'standard'},
        'app.tasks.cache_operations.cache_point_batch': {'queue': 'standard'},
        'app.tasks.cache_operations.cache_warm_regions': {'queue': 'low_priority'},
        'app.tasks.cache_operations.cache_validate': {'queue': 'low_priority'},
        
        # Cleanup tasks
        'app.tasks.cleanup_tasks.cleanup_expired_cache': {'queue': 'maintenance'},
        'app.tasks.cleanup_tasks.cleanup_orphaned_objects': {'queue': 'maintenance'},
        'app.tasks.cleanup_tasks.cleanup_analyze_usage': {'queue': 'maintenance'},
        'app.tasks.cleanup_tasks.cleanup_optimize_cache': {'queue': 'maintenance'},
        
        # Monitoring tasks
        'app.tasks.monitoring_tasks.monitor_collect_metrics': {'queue': 'low_priority'},
        'app.tasks.monitoring_tasks.monitor_analyze_patterns': {'queue': 'low_priority'},
        'app.tasks.monitoring_tasks.monitor_generate_report': {'queue': 'low_priority'},
        'app.tasks.monitoring_tasks.monitor_check_health': {'queue': 'maintenance'},
    },
    
    # Rate limiting per task
    task_annotations={
        # Tile tasks - higher limits
        'app.tasks.tile_tasks.tile_generate': {'rate_limit': '1000/m'},
        'app.tasks.tile_tasks.tile_generate_batch': {'rate_limit': '100/m'},
        'app.tasks.tile_tasks.tile_generate_mosaic': {'rate_limit': '50/m'},
        
        # Cache tasks - moderate limits
        'app.tasks.cache_operations.cache_campaign': {'rate_limit': '10/m'},
        'app.tasks.cache_operations.cache_point': {'rate_limit': '500/m'},
        'app.tasks.cache_operations.cache_warm_regions': {'rate_limit': '5/m'},
        
        # Cleanup tasks - conservative limits
        'app.tasks.cleanup_tasks.cleanup_expired_cache': {'rate_limit': '1/m'},
        'app.tasks.cleanup_tasks.cleanup_orphaned_objects': {'rate_limit': '1/m'},
        
        # Monitoring tasks - no limits
        'app.tasks.monitoring_tasks.monitor_check_health': {'rate_limit': None},
    },
    
    # Worker configuration
    worker_concurrency=8,  # Number of worker processes
    worker_pool='prefork',  # Best for CPU-bound tasks
    worker_max_memory_per_child=512000,  # 512MB max memory per worker
    
    # Event configuration - enable for Flower monitoring
    worker_send_task_events=True,
    task_send_sent_event=True,
    
    # Import task modules
    imports=[
        'app.tasks.tile_tasks',
        'app.tasks.cache_operations',
        'app.tasks.cleanup_tasks',
        'app.tasks.monitoring_tasks',
    ],
)

# Beat schedule configuration is now in each task module
celery_app.conf.beat_schedule = {}