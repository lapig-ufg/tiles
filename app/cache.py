"""
Cache helpers usando diskcache.FanoutCache
"""
from __future__ import annotations

import orjson
from pathlib import Path
from typing import Any, Optional

from diskcache import FanoutCache

CACHE_DIR = Path("cache")      # persistido em SSD local
CACHE_DIR.mkdir(exist_ok=True)

# 8 shards → paraleliza escrituras, timeout para evitar dead-lock
cache: FanoutCache = FanoutCache(CACHE_DIR, shards=8, timeout=1, statistics=True)

PNG_TTL  = 24 * 3600   # 24 h
META_TTL =  6 * 3600   #  6 h

# ----------------------- helpers ----------------------------- #
def get_png(key: str) -> Optional[bytes]:
    return cache.get(key)              # devolve None se expirou

def set_png(key: str, data: bytes, ttl: int = PNG_TTL) -> None:
    cache.set(key, data, expire=ttl)

def get_meta(key: str) -> Optional[dict[str, Any]]:
    raw = cache.get(key)
    return None if raw is None else orjson.loads(raw)

def set_meta(key: str, meta: dict[str, Any], ttl: int = META_TTL) -> None:
    cache.set(key, orjson.dumps(meta), expire=ttl)

def close_cache() -> None:
    cache.close()
