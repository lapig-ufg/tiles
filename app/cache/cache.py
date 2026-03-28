"""
Cache facade para compatibilidade com código existente
Redireciona para o novo cache híbrido de alta performance
"""
from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, Optional

from app.cache.cache_hybrid import tile_cache

# TTLs otimizados para alta performance
PNG_TTL = 30 * 24 * 3600   # 30 dias para tiles (eram 24h)
META_TTL = 7 * 24 * 3600    # 7 dias para metadados (eram 6h)

# ----------------------- helpers síncronos (compatibilidade) ----------------------------- #
# NOTA: Estas funções são para compatibilidade com código legado (ex: Celery tasks em prefork)
# Recomenda-se usar as versões assíncronas (aget_png, aset_png, etc) quando possível

_sync_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="cache-sync"
)


def _run_async(coro):
    """Executa coroutine de forma segura tanto em contexto síncrono quanto assíncrono.

    Quando chamado de dentro de um event loop ativo (ex: FastAPI handler chamando
    código síncrono legado), delega para uma thread separada para evitar deadlock.
    Quando chamado de contexto puramente síncrono (ex: Celery prefork worker),
    cria um loop dedicado.
    """
    def _run_in_new_loop():
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            try:
                loop.close()
            except Exception:
                pass

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _run_in_new_loop()

    # Loop ativo — delega para thread para evitar deadlock
    future = _sync_executor.submit(_run_in_new_loop)
    return future.result(timeout=120)


def get_png(key: str) -> Optional[bytes]:
    """Busca PNG do cache híbrido (compatibilidade síncrona)"""
    return _run_async(tile_cache.get_png(key))

def set_png(key: str, data: bytes, ttl: int = PNG_TTL) -> None:
    """Salva PNG no cache híbrido (compatibilidade síncrona)"""
    _run_async(tile_cache.set_png(key, data, ttl))

def get_meta(key: str) -> Optional[dict[str, Any]]:
    """Busca metadados do cache híbrido (compatibilidade síncrona)"""
    return _run_async(tile_cache.get_meta(key))

def set_meta(key: str, meta: dict[str, Any], ttl: int = META_TTL) -> None:
    """Salva metadados no cache híbrido (compatibilidade síncrona)"""
    _run_async(tile_cache.set_meta(key, meta, ttl))

def close_cache() -> None:
    """Fecha conexões do cache híbrido"""
    _run_async(tile_cache.close())

# ----------------------- helpers assíncronos (recomendados) ----------------------------- #
async def aget_png(key: str) -> Optional[bytes]:
    """Busca PNG do cache híbrido (assíncrono)"""
    return await tile_cache.get_png(key)

async def aset_png(key: str, data: bytes, ttl: int = PNG_TTL) -> None:
    """Salva PNG no cache híbrido (assíncrono)"""
    await tile_cache.set_png(key, data, ttl)

async def aget_meta(key: str) -> Optional[dict[str, Any]]:
    """Busca metadados do cache híbrido (assíncrono)"""
    return await tile_cache.get_meta(key)

async def aset_meta(key: str, meta: dict[str, Any], ttl: int = META_TTL) -> None:
    """Salva metadados no cache híbrido (assíncrono)"""
    await tile_cache.set_meta(key, meta, ttl)

def atile_lock(key: str):
    """Lock distribuído para geração de tile (assíncrono context manager)"""
    return tile_cache.tile_lock(key)
