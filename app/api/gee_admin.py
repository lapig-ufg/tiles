"""
Endpoints administrativos para o pool de Service Accounts do GEE.

Permite monitorar uso por SA, visualizar assignments de workers,
recarregar SAs sem restart e controlar cooldowns manualmente.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.config import logger

router = APIRouter(prefix="/admin/gee")


def _get_pool():
    """Obtém a instância do pool (lazy, evita import circular)."""
    from app.core.gee_pool import ServiceAccountPool
    try:
        return ServiceAccountPool.get_instance()
    except Exception as exc:
        raise HTTPException(503, f"Pool GEE não disponível: {exc}")


@router.get("/pool", summary="Status do pool de SAs")
async def pool_status():
    """Retorna métricas completas do pool: uso por SA, cooldowns, contadores 429."""
    pool = _get_pool()
    return pool.get_metrics()


@router.get("/workers", summary="Assignments ativos")
async def worker_assignments():
    """Retorna todas as atribuições ativas de workers a SAs."""
    pool = _get_pool()
    return pool.get_assignments()


@router.post("/reload", summary="Recarregar SAs do diretório")
async def reload_accounts():
    """Re-escaneia o diretório de SAs e atualiza o pool (hot-reload)."""
    pool = _get_pool()
    result = pool.refresh_registry()
    logger.info(f"Pool GEE recarregado: {result}")
    return result


@router.post("/cooldown/{sa_name}", summary="Controle manual de cooldown")
async def set_cooldown(sa_name: str, seconds: int = 0):
    """Define ou remove o cooldown de uma SA manualmente.

    Args:
        sa_name: Nome (client_email) da SA.
        seconds: Duração do cooldown em segundos (0 = remover cooldown).
    """
    import time

    pool = _get_pool()
    metrics_key = pool._KEY_METRICS.format(sa_name)

    if seconds > 0:
        pool._redis.hset(metrics_key, "cooldown_until", str(time.time() + seconds))
        return {"status": "cooldown_set", "sa": sa_name, "seconds": seconds}
    else:
        pool._redis.hset(metrics_key, "cooldown_until", "")
        return {"status": "cooldown_removed", "sa": sa_name}
