"""
Configuração e inicialização do Celery
"""
from celery import Celery
from app.core.config import settings
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
    # Rate limiting otimizado
    task_annotations={
        "tasks.process_landsat_tile": {"rate_limit": "200/m"},
        "tasks.process_sentinel_tile": {"rate_limit": "200/m"},
        "cache_warmer.warm_tiles": {"rate_limit": "300/m"},
        # Limites para tasks de cache
        "cache_tasks.cache_campaign_async": {"rate_limit": "10/m"},
        "cache_tasks.cache_point_async": {"rate_limit": "100/m"},
        "cache_tasks.cache_point_optimized": {"rate_limit": "600/m"},
    },
    # Configurações de workers otimizadas
    worker_prefetch_multiplier=8,  # Mais tasks pre-fetched
    worker_max_tasks_per_child=2000,  # Mais tasks por worker
    # Timeouts ajustados
    task_soft_time_limit=600,  # 10 minutos
    task_time_limit=900,  # 15 minutos
    # Configurações de routing para priorização
    task_routes={
        'cache_tasks.cache_campaign_async': {'queue': 'priority'},
        'cache_tasks.cache_point_optimized': {'queue': 'priority'},
        'cache_tasks.cache_point_async': {'queue': 'standard'},
    },
    # Configurações de concorrência
    worker_concurrency=8,  # Número de processos worker
    worker_pool='prefork',  # Melhor para CPU-bound tasks
    # Inclui módulos com tasks
    imports=[
        'app.tasks.tasks',
        'app.cache.cache_warmer',
        'app.tasks.cache_tasks',
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