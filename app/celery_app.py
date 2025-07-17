"""
Configuração e inicialização do Celery
"""
from celery import Celery
from app.config import settings
from celery.schedules import crontab

# Cria instância do Celery
celery_app = Celery(
    "tiles",
    broker=settings.get("CELERY_BROKER_URL", "redis://valkey:6379/1"),
    backend=settings.get("CELERY_RESULT_BACKEND", "redis://valkey:6379/2"),
)

# Configurações otimizadas para processamento de tiles
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Configuração para evitar warning de deprecação
    broker_connection_retry_on_startup=True,
    # Rate limiting
    task_annotations={
        "tasks.process_landsat_tile": {"rate_limit": "100/m"},
        "tasks.process_sentinel_tile": {"rate_limit": "100/m"},
        "cache_warmer.warm_tiles": {"rate_limit": "200/m"},
    },
    # Configurações de workers
    worker_prefetch_multiplier=4,
    worker_max_tasks_per_child=1000,
    # Timeouts
    task_soft_time_limit=300,  # 5 minutos
    task_time_limit=600,  # 10 minutos
    # Inclui módulos com tasks
    imports=[
        'app.tasks',
        'app.cache_warmer',
    ],
)

# Configuração do Celery Beat (scheduler)
celery_app.conf.beat_schedule = {
    'warm-popular-regions-daily': {
        'task': 'cache_warmer.schedule_warmup',
        'schedule': crontab(hour=2, minute=0),  # 2 AM diariamente
        'args': ('landsat', {'bands': ['B4', 'B3', 'B2']}, 1000, 100),
    },
    'analyze-usage-weekly': {
        'task': 'cache_warmer.analyze_usage_patterns',
        'schedule': crontab(day_of_week=1, hour=3, minute=0),  # Segunda 3 AM
        'args': (7,),
    },
}