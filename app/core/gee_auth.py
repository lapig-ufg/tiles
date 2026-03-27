"""
Autenticação do Google Earth Engine com suporte a pool de service accounts.

Delega ao ServiceAccountPool (gee_pool.py) para coordenação de múltiplas SAs
entre workers Gunicorn/Celery via Redis. Mantém retrocompatibilidade com
chamadas existentes (sem argumentos).
"""
from __future__ import annotations

import os
import socket

from app.core.config import settings, logger


_manager = None  # WorkerGEEManager | None — lazy import para evitar circular


def initialize_earth_engine(worker_id: str | None = None) -> None:
    """Inicializa o Google Earth Engine com uma SA do pool.

    Se GEE_SA_DIRECTORY estiver configurado e contiver mais de uma SA,
    utiliza o pool com coordenação Redis. Caso contrário, usa o modo
    legado com arquivo único (GEE_SERVICE_ACCOUNT_FILE).

    Args:
        worker_id: Identificador único do worker. Se None, gera automaticamente.
    """
    global _manager

    if _manager is not None:
        return

    if settings.get("SKIP_GEE_INIT", False):
        logger.warning("Inicialização do GEE ignorada (SKIP_GEE_INIT=true)")
        return

    if not worker_id:
        worker_id = f"{socket.gethostname()}-{os.getpid()}"

    try:
        from app.core.gee_pool import ServiceAccountPool, WorkerGEEManager

        pool = ServiceAccountPool.get_instance()
        _manager = WorkerGEEManager(pool)
        _manager.initialize(worker_id)

    except Exception as exc:
        logger.error(f"Falha ao inicializar GEE via pool: {exc}")
        if settings.get("TILES_ENV") != "development":
            raise
        logger.warning("Executando em modo desenvolvimento sem GEE")


def get_gee_manager():
    """Retorna o WorkerGEEManager do worker atual (ou None)."""
    return _manager


def shutdown_earth_engine() -> None:
    """Libera a SA do worker atual e para o heartbeat."""
    global _manager
    if _manager is not None:
        try:
            _manager.shutdown()
        except Exception as exc:
            logger.warning(f"Erro ao encerrar GEE manager: {exc}")
        _manager = None


# Singleton pattern para contextos async
_gee_initialized = False


async def ensure_gee_initialized() -> None:
    """Wrapper async para garantir que o GEE está inicializado."""
    global _gee_initialized
    if not _gee_initialized:
        initialize_earth_engine()
        _gee_initialized = True
