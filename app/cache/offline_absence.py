import asyncio
import json
import math
import re
import time
from datetime import datetime, timezone

import redis

from app.cache.errors import TileLockUnavailable


ABSENCE_TTL = 30 * 86400
_MAX_MARKER_BYTES = 2048
_WRITE = """
if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('set', KEYS[2], ARGV[2], 'PX', ARGV[3])
return 1
"""
_CLEAR = """
if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('del', KEYS[2])
return 1
"""


def _key(key):
    if (not isinstance(key, str) or not 0 < len(key) <= 512
            or not key.endswith('.png') or any(ord(char) < 32 for char in key)
            or '..' in key or key.startswith('/')):
        raise ValueError('Chave de tile inválida')
    return 'absence:' + key


def _timestamp(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError('Data de verificação sem fuso horário')
        value = value.timestamp()
    elif isinstance(value, str):
        value = datetime.fromisoformat(value)
        return _timestamp(value)
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError('Data de verificação inválida')
    return float(value)


def _validate_evidence(evidence, now, key=None):
    if (not isinstance(evidence, dict) or evidence.get('kind') != 'empty_collection'
            or type(evidence.get('count')) is not int or evidence['count'] != 0):
        raise ValueError('Ausência requer contagem comprovada de coleção vazia')
    checked_at = _timestamp(evidence.get('checked_at'))
    if not now - ABSENCE_TTL < checked_at <= now:
        raise ValueError('Evidência de ausência fora do prazo de validade')
    if key is not None:
        product = re.match(r'^(landsat|s2_harmonized)_(WET|DRY|MONTH)_(\d{4})_', key)
        collection = evidence.get('collection')
        if (not product or evidence.get('period') != product[2]
                or type(evidence.get('year')) is not int or evidence['year'] != int(product[3])
                or not isinstance(collection, str) or not 0 < len(collection.strip()) <= 256
                or any(ord(char) < 32 for char in collection)):
            raise ValueError('Evidência não identifica o produto solicitado')
    return checked_at


def _decode(raw, key):
    if not isinstance(raw, (bytes, str)) or len(raw) > _MAX_MARKER_BYTES:
        return None
    try:
        marker = json.loads(raw)
        now = time.time()
        checked_at = _validate_evidence(marker, now)
        evidence_checked_at = _validate_evidence(marker['evidence'], now, key)
        expires_at = _timestamp(marker['expires_at'])
        if (checked_at != evidence_checked_at or not now < expires_at <= checked_at + ABSENCE_TTL):
            return None
        return marker
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def get_absence(redis_client, key):
    return _decode(redis_client.get(_key(key)), key)


def _mutate(redis_client, key, lease, script, *args):
    marker_key = _key(key)
    if lease.key != key:
        raise ValueError('Concessão pertence a outra chave de cache')
    lease.ensure_owned()
    try:
        if not redis_client.eval(script, 2, 'lock:' + key, marker_key,
                                 lease.lock.local.token, *args):
            raise TileLockUnavailable('Concessão de geração perdida')
    except redis.RedisError as exc:
        raise TileLockUnavailable('Coordenador de cache indisponível') from exc


def set_absence(redis_client, key, evidence, *, lease, ttl=ABSENCE_TTL):
    if type(ttl) not in (float, int) or not math.isfinite(ttl) or not 0 < ttl <= ABSENCE_TTL:
        raise ValueError('TTL de ausência deve ser positivo e limitado a trinta dias')
    now = time.time()
    checked_at = _validate_evidence(evidence, now, key)
    stored_evidence = dict(evidence)
    stored_evidence['checked_at'] = datetime.fromtimestamp(checked_at, timezone.utc).isoformat()
    expires_at = min(now + ttl, checked_at + ABSENCE_TTL)
    marker = {'kind': 'empty_collection', 'count': 0, 'checked_at': checked_at,
              'expires_at': expires_at, 'evidence': stored_evidence}
    raw = json.dumps(marker, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > _MAX_MARKER_BYTES:
        raise ValueError('Evidência de ausência excede o tamanho permitido')
    _mutate(redis_client, key, lease, _WRITE, raw, max(1, math.ceil((expires_at - now) * 1000)))
    return marker


def clear_absence(redis_client, key, *, lease):
    _mutate(redis_client, key, lease, _CLEAR)


async def aget_absence(key):
    from app.cache.cache_hybrid import tile_cache

    try:
        async with asyncio.timeout(tile_cache.IO_TIMEOUT):
            async with tile_cache._get_redis() as client:
                return _decode(await client.get(_key(key)), key)
    except (redis.RedisError, TimeoutError) as exc:
        raise TileLockUnavailable('Coordenador de cache indisponível') from exc
