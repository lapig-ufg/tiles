"""set_png não pode bloquear a resposta HTTP quando o S3 está degradado.

Incidente 2026-07: SeaweedFS sem volumes graváveis fazia cada PutObject
falhar após ~230s de retries; como o upload era aguardado no caminho da
requisição, todo tile em cache-miss levava minutos. Além disso, a meta
era gravada no Redis mesmo com upload falho (s3_synced=0), apontando para
objeto inexistente — o próximo get_png apagava a meta e re-baixava do GEE.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from app.cache.cache_hybrid import HybridTileCache


class FakeRedis:
    def __init__(self):
        self.hset_calls = []
        self.expire_calls = []

    async def hset(self, key, mapping=None, **kwargs):
        self.hset_calls.append((key, mapping))

    async def expire(self, key, ttl):
        self.expire_calls.append((key, ttl))


@pytest.fixture
def cache(monkeypatch):
    c = HybridTileCache(redis_url="redis://fake:6379")
    fake_redis = FakeRedis()

    @asynccontextmanager
    async def fake_get_redis():
        yield fake_redis

    monkeypatch.setattr(c, "_get_redis", fake_get_redis)
    c._fake_redis = fake_redis
    return c


@pytest.mark.asyncio
async def test_set_png_background_nao_bloqueia_em_upload_lento(cache, monkeypatch):
    """Com background=True, set_png retorna antes do upload terminar."""
    upload_started = asyncio.Event()
    upload_release = asyncio.Event()

    async def slow_upload(s3_key, data):
        upload_started.set()
        await upload_release.wait()
        return True

    monkeypatch.setattr(cache, "_upload_to_s3", slow_upload)

    await asyncio.wait_for(
        cache.set_png("tile-key", b"png", ttl=60, background=True),
        timeout=0.5,
    )
    # set_png retornou; upload segue pendente em background
    assert len(cache._bg_tasks) == 1
    await asyncio.wait_for(upload_started.wait(), timeout=1)
    assert cache._fake_redis.hset_calls == []

    upload_release.set()
    await asyncio.gather(*cache._bg_tasks)
    assert len(cache._fake_redis.hset_calls) == 1


@pytest.mark.asyncio
async def test_meta_nao_gravada_quando_upload_falha(cache, monkeypatch):
    """Upload falho não pode deixar meta apontando para objeto inexistente."""
    async def failing_upload(s3_key, data):
        return False

    monkeypatch.setattr(cache, "_upload_to_s3", failing_upload)

    await cache.set_png("tile-key", b"png", ttl=60)

    assert cache._fake_redis.hset_calls == []
    # Cache local continua servindo o tile no mesmo processo
    assert "tile-key" in cache.local_cache


@pytest.mark.asyncio
async def test_meta_gravada_com_s3_synced_1_quando_upload_ok(cache, monkeypatch):
    async def ok_upload(s3_key, data):
        return True

    monkeypatch.setattr(cache, "_upload_to_s3", ok_upload)

    await cache.set_png("tile-key", b"png", ttl=60)

    assert len(cache._fake_redis.hset_calls) == 1
    key, mapping = cache._fake_redis.hset_calls[0]
    assert key == "tile:tile-key"
    assert mapping["s3_synced"] == "1"
    assert cache._fake_redis.expire_calls == [("tile:tile-key", 60)]


@pytest.mark.asyncio
async def test_backpressure_descarta_upload_com_fila_cheia(cache, monkeypatch):
    """Fila de background cheia descarta a persistência em vez de acumular."""
    async def never_called(s3_key, data):
        raise AssertionError("upload não deveria ser agendado")

    monkeypatch.setattr(cache, "_upload_to_s3", never_called)
    cache.MAX_BG_UPLOADS = 0

    await cache.set_png("tile-key", b"png", ttl=60, background=True)

    assert cache._bg_tasks == set()
    assert "tile-key" in cache.local_cache
