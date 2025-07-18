"""
Cache facade para compatibilidade com código existente
Redireciona para o novo cache híbrido de alta performance
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from app.cache.cache_hybrid import tile_cache

# TTLs otimizados para alta performance
PNG_TTL = 30 * 24 * 3600   # 30 dias para tiles (eram 24h)
META_TTL = 7 * 24 * 3600    # 7 dias para metadados (eram 6h)

# ----------------------- helpers síncronos (compatibilidade) ----------------------------- #
# NOTA: Estas funções são para compatibilidade com código legado
# Recomenda-se usar as versões assíncronas (aget_png, aset_png, etc) quando possível

def _run_async(coro):
    """Helper para executar código assíncrono em contexto síncrono"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    if loop.is_running():
        # Se já estamos em um loop assíncrono, cria uma task
        task = asyncio.create_task(coro)
        # Usa nest_asyncio para permitir loops aninhados
        import nest_asyncio
        nest_asyncio.apply()
        return loop.run_until_complete(task)
    else:
        # Se não há loop rodando, executa normalmente
        return loop.run_until_complete(coro)

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
