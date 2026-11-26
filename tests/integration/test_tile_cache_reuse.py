from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from datetime import datetime
from types import SimpleNamespace
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar

import pytest

from fastapi.testclient import TestClient

from tests.integration.test_circuit_breaker_integration import app_with_cb
from tests.unit.test_cache_reliability import cache_env


CANONICAL = "landsat_WET_2020_0_landsat-tvi-false_BEST_IMAGE/abc/10/12_34.png"
LEGACY = "landsat_WET_2020_7_landsat-tvi-false_BEST_IMAGE/abc/10/12_34.png"
URL = "/landsat/12/34/10?period=WET&year=2020&month=7"


def forbid_generation(monkeypatch):
    from app.api import layers
    breaker = AsyncMock()
    breaker.is_open.return_value = True
    breaker.seconds_until_retry.return_value = 10
    monkeypatch.setattr(layers, "get_ee_circuit_breaker", lambda: breaker)
    return layers


def test_tile_sazonal_atende_chave_canonica_sem_geracao(app_with_cb, monkeypatch):
    layers = forbid_generation(monkeypatch)
    requested = []

    async def cached(key):
        requested.append(key)
        return b"png-canonico" if key == CANONICAL else None

    monkeypatch.setattr(layers, "get_png", cached)
    with TestClient(app_with_cb) as client:
        response = client.get(URL)
    assert response.status_code == 200
    assert response.content == b"png-canonico"
    assert requested == [CANONICAL]


def test_tile_sazonal_reaproveita_chave_legada(app_with_cb, monkeypatch):
    layers = forbid_generation(monkeypatch)
    requested = []

    async def cached(key):
        requested.append(key)
        return b"png-legado" if key == LEGACY else None

    monkeypatch.setattr(layers, "get_png", cached)
    with TestClient(app_with_cb) as client:
        response = client.get(URL)
    assert response.status_code == 200
    assert response.content == b"png-legado"
    assert requested[:2] == [CANONICAL, LEGACY]


def test_recheca_cache_apos_adquirir_lock(app_with_cb, monkeypatch):
    layers = forbid_generation(monkeypatch)
    store = {}

    async def cached(key):
        return store.get(key)

    @asynccontextmanager
    async def lock(key):
        assert key == CANONICAL
        store[key] = b"png-concorrente"
        yield True

    monkeypatch.setattr(layers, "get_png", cached)
    monkeypatch.setattr(layers, "tile_lock", lock)
    with TestClient(app_with_cb) as client:
        response = client.get(URL)
    assert response.status_code == 200
    assert response.content == b"png-concorrente"


def test_lock_indisponivel_retorna_503_sem_gerar_imagem(app_with_cb, monkeypatch):
    from app.cache.errors import TileLockUnavailable
    layers = forbid_generation(monkeypatch)

    @asynccontextmanager
    async def unavailable(key):
        raise TileLockUnavailable("ocupado")
        yield True

    monkeypatch.setattr(layers, "tile_lock", unavailable)
    with TestClient(app_with_cb) as client:
        response = client.get(URL)
    assert response.status_code == 503
    assert response.headers["X-Error-Reason"] == "cache_busy"
    assert response.headers["Retry-After"] == "1"
    assert "no-store" in response.headers["Cache-Control"]


def generation_context(monkeypatch):
    from app.api import layers
    breaker = AsyncMock()
    breaker.is_open.return_value = False
    monkeypatch.setattr(layers, "get_ee_circuit_breaker", lambda: breaker)
    monkeypatch.setattr(layers.ee, "Geometry", SimpleNamespace(BBox=lambda *args: object()))
    generated = []

    def build(*args):
        generated.append(args)
        return "https://ee/generated/{z}/{x}/{y}"

    monkeypatch.setattr(layers, "_create_landsat_layer_with_params", build)
    monkeypatch.setattr(layers, "fetch_tile_with_rotation", AsyncMock(return_value=b"png"))
    return layers, breaker, generated


def test_url_publicada_durante_espera_nao_e_regenerada(app_with_cb, monkeypatch):
    layers, _, generated = generation_context(monkeypatch)
    store = {}
    existing_url = "https://ee/existing/{z}/{x}/{y}"

    async def get_meta(key):
        return store.get(key)

    @asynccontextmanager
    async def lock(key):
        if key.startswith("url:"):
            store[key.removeprefix("url:")] = {
                "date": datetime.now().isoformat(), "url": existing_url,
            }
        yield True

    monkeypatch.setattr(layers, "get_meta", get_meta)
    monkeypatch.setattr(layers, "tile_lock", lock)
    with TestClient(app_with_cb) as client:
        response = client.get(URL)
    assert response.status_code == 200
    assert generated == []
    assert layers.fetch_tile_with_rotation.call_args.kwargs["cached_url"] == existing_url


def test_url_sem_lock_nao_gera_mapa(app_with_cb, monkeypatch):
    layers, _, generated = generation_context(monkeypatch)

    @asynccontextmanager
    async def lock(key):
        yield not key.startswith("url:")

    monkeypatch.setattr(layers, "tile_lock", lock)
    with TestClient(app_with_cb) as client:
        response = client.get(URL)
    assert response.status_code == 503
    assert response.headers["X-Error-Reason"] == "cache_busy"
    assert generated == []
    layers.fetch_tile_with_rotation.assert_not_awaited()


@pytest.mark.parametrize("metadata", [
    {"url": "https://ee/old"},
    {"date": "invalida", "url": "https://ee/old"},
    {"date": datetime.now().isoformat(), "url": ""},
    {"date": "2099-01-01T00:00:00", "url": "https://ee/future"},
])
def test_meta_invalida_e_tratada_como_miss(app_with_cb, monkeypatch, metadata):
    layers, _, generated = generation_context(monkeypatch)
    monkeypatch.setattr(layers, "get_meta", AsyncMock(return_value=metadata))
    with TestClient(app_with_cb) as client:
        response = client.get(URL)
    assert response.status_code == 200
    assert len(generated) == 1


def test_admissao_sem_capacidade_responde_503_sem_falha_no_breaker(app_with_cb, monkeypatch):
    from app.core.gee_admission import GoogleAdmissionError
    layers, breaker, _ = generation_context(monkeypatch)

    def busy(*args):
        raise GoogleAdmissionError(retry_after=7, reason="capacity")

    monkeypatch.setattr(layers, "_create_landsat_layer_with_params", busy)
    with TestClient(app_with_cb) as client:
        response = client.get(URL)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "7"
    assert response.headers["X-Error-Reason"] == "gee_busy"
    breaker.record_failure.assert_not_awaited()


@pytest.mark.asyncio
async def test_duas_instancias_nao_repetem_download_enquanto_s3_persiste(app_with_cb, cache_env, monkeypatch):
    from app.cache.cache_hybrid import HybridTileCache
    layers, _, downloads = generation_context(monkeypatch)
    first, store, storage, _ = cache_env
    second = HybridTileCache(redis_url='redis://fake:6379')
    monkeypatch.setattr(second, '_get_redis', first._get_redis)
    second._s3_client = storage
    consumer = ContextVar('consumer')
    second_lock_requested = asyncio.Event()
    fetched = []

    async def get_png(key):
        return await consumer.get().get_png(key)

    async def set_png(key, data, **kwargs):
        await consumer.get().set_png(key, data, **kwargs)

    @asynccontextmanager
    async def lock(key):
        cache = consumer.get()
        if cache is second:
            second_lock_requested.set()
        async with cache.tile_lock(key) as owned:
            yield owned

    async def fetch(**kwargs):
        fetched.append(kwargs['cached_url'])
        return b'png'

    async def request(cache):
        consumer.set(cache)
        return await layers._serve_tile('landsat', 12, 34, 10, 'WET', 2020, 7,
                                       'landsat-tvi-false', None, 'BEST_IMAGE')

    monkeypatch.setattr(layers, 'get_png', get_png)
    monkeypatch.setattr(layers, 'set_png', set_png)
    monkeypatch.setattr(layers, 'tile_lock', lock)
    monkeypatch.setattr(layers, 'fetch_tile_with_rotation', fetch)
    monkeypatch.setattr(layers, 'get_meta', AsyncMock(return_value={
        'url': 'https://ee/existing/{z}/{x}/{y}', 'date': datetime.now().isoformat(),
    }))
    storage.release.clear()
    tasks = [asyncio.create_task(request(first))]
    try:
        await asyncio.wait_for(storage.started.wait(), timeout=1)
        tasks.append(asyncio.create_task(request(second)))
        await asyncio.wait_for(second_lock_requested.wait(), timeout=1)
        await asyncio.sleep(.01)
        assert len(fetched) == 1
        assert await store.get(f'lock:{CANONICAL}') is not None
    finally:
        storage.release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
        await first.close()
        await second.close()
    assert len(fetched) == 1


@pytest.mark.asyncio
async def test_cancelamento_aguarda_builder_sincrono_antes_de_liberar_contexto(app_with_cb):
    from app.api import layers
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def build():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(timeout=2)
        return 'url'

    task = asyncio.create_task(layers._run_ee_builder(build))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_cancelamento_remove_builder_que_ainda_esta_na_fila(app_with_cb, monkeypatch):
    from app.api import layers
    release = threading.Event()
    calls = []
    executor = ThreadPoolExecutor(max_workers=1)
    blocker = executor.submit(release.wait, 2)
    monkeypatch.setattr(layers, 'ee_executor', executor)
    task = asyncio.create_task(layers._run_ee_builder(lambda: calls.append('obsolete')))
    try:
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        assert calls == []
    finally:
        release.set()
        executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_fila_local_de_builders_tem_capacidade_limitada(app_with_cb, monkeypatch):
    from app.api import layers
    from app.core.gee_admission import GoogleAdmissionError
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(layers, 'ee_pending', threading.BoundedSemaphore(1), raising=False)

    def build():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(2)
        return 'url'

    task = asyncio.create_task(layers._run_ee_builder(build))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        with pytest.raises(GoogleAdmissionError):
            await layers._run_ee_builder(lambda: 'obsolete')
    finally:
        release.set()
        await task
    assert await layers._run_ee_builder(lambda: 'available') == 'available'
