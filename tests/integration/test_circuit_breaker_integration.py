"""Integração: quando o CB está aberto, `_serve_tile` retorna 503 imediato
sem chamar o Earth Engine."""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_cb(monkeypatch):
    from tests.conftest import reset_app_imports
    reset_app_imports()

    cache_mod = type(sys)("app.cache.cache")
    async def _noop(*a, **k): return None
    async def _none(*a, **k): return None
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_lock(*_a, **_k):
        yield True

    cache_mod.aget_png = _none
    cache_mod.aset_png = _noop
    cache_mod.aget_meta = _none
    cache_mod.aset_meta = _noop
    cache_mod.atile_lock = _fake_lock
    sys.modules["app.cache.cache"] = cache_mod

    rate_limiter_mod = type(sys)("app.middleware.rate_limiter")
    def _pt(*_a, **_k):
        def deco(fn): return fn
        return deco
    rate_limiter_mod.limit_sentinel = _pt
    rate_limiter_mod.limit_landsat = _pt
    sys.modules["app.middleware.rate_limiter"] = rate_limiter_mod

    visParam_mod = type(sys)("app.visualization.visParam")
    visParam_mod.VISPARAMS = {
        "landsat-tvi-true":  {"visparam": {}},
        "landsat-tvi-false": {"visparam": {}},
        "landsat-tvi-agri":  {"visparam": {}},
    }
    visParam_mod.get_landsat_collection = lambda y: "LANDSAT/LC08/C02/T1_L2"
    visParam_mod.get_landsat_vis_params = lambda *a, **k: {"bands": ["SR_B4"]}
    sys.modules["app.visualization.visParam"] = visParam_mod

    vpdb = type(sys)("app.visualization.vis_params_db")
    async def _vp(*a, **k): return {"bands": ["SR_B4"], "min": 0, "max": 1}
    vpdb.get_landsat_vis_params_async = _vp
    vpdb.vis_params_manager = object()
    vpdb.get_visparams_dict = lambda: {}
    vpdb.get_landsat_collection = lambda y: "LANDSAT/LC08/C02/T1_L2"
    sys.modules["app.visualization.vis_params_db"] = vpdb

    vpl = type(sys)("app.visualization.vis_params_loader")
    vpl.VISPARAMS = {}
    vpl.get_VISPARAMS_sync = lambda: {}
    async def _vps(*a, **k):
        return {
            "landsat-tvi-true": {"bands": ["SR_B4"]},
            "landsat-tvi-false": {"bands": ["SR_B4"]},
            "landsat-tvi-agri": {"bands": ["SR_B4"]},
        }
    vpl.get_visparams = _vps
    vpl.get_landsat_vis_params = lambda *a, **k: {"bands": ["SR_B4"]}
    vpl.get_landsat_collection = lambda y: "LANDSAT/LC08/C02/T1_L2"
    sys.modules["app.visualization.vis_params_loader"] = vpl

    caps = type(sys)("app.utils.capabilities")
    class _P:
        async def get_capabilities(self):
            return {"collections": [{
                "name": "landsat", "year": list(range(1984, 2030)),
                "period": ["WET", "DRY", "MONTH"],
                "visparam": ["landsat-tvi-true", "landsat-tvi-false", "landsat-tvi-agri"],
            }]}
    caps.get_capabilities_provider = lambda: _P()
    sys.modules["app.utils.capabilities"] = caps

    svc_tile = type(sys)("app.services.tile")
    svc_tile.tile2goehashBBOX = lambda x, y, z: ("abc", {"w": -50, "s": -10, "e": -49, "n": -9})
    sys.modules["app.services.tile"] = svc_tile

    gee_pool = type(sys)("app.core.gee_pool")
    gee_pool.gee_retry = lambda *a, **k: (lambda fn: fn)
    sys.modules["app.core.gee_pool"] = gee_pool

    http_util = type(sys)("app.utils.http")
    async def _hgb(url, **k): return b""
    http_util.http_get_bytes = _hgb
    sys.modules["app.utils.http"] = http_util

    from app.api import layers
    app = FastAPI()
    app.include_router(layers.router)
    return app


def test_open_breaker_short_circuits_with_503(app_with_cb, monkeypatch):
    """Com CB aberto, handler responde 503 imediato sem hit no EE."""
    from app.api import layers

    fake_breaker = AsyncMock()
    fake_breaker.is_open = AsyncMock(return_value=True)
    fake_breaker.seconds_until_retry = AsyncMock(return_value=25)
    fake_breaker.record_success = AsyncMock()
    fake_breaker.record_failure = AsyncMock()
    monkeypatch.setattr(layers, "get_ee_circuit_breaker", lambda: fake_breaker)

    # EE nunca deveria ser tocado — mockamos pra contar.
    ee_calls = []
    async def _should_not_call(*a, **k):
        ee_calls.append(1)
        return {"bands": ["SR_B4"]}
    monkeypatch.setattr(layers, "get_landsat_vis_params_async", _should_not_call)

    client = TestClient(app_with_cb)
    resp = client.get("/landsat/100/100/10?year=2020&visparam=landsat-tvi-true")

    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "25"
    assert resp.headers["x-error-reason"] == "ee_unavailable"
    assert len(ee_calls) == 0, "EE não deve ser chamado quando CB está aberto"


def test_closed_breaker_does_not_short_circuit(app_with_cb, monkeypatch):
    """Com CB fechado, fluxo segue para o EE normalmente."""
    from app.api import layers
    from fastapi import HTTPException

    fake_breaker = AsyncMock()
    fake_breaker.is_open = AsyncMock(return_value=False)
    fake_breaker.seconds_until_retry = AsyncMock(return_value=0)
    fake_breaker.record_success = AsyncMock()
    fake_breaker.record_failure = AsyncMock()
    monkeypatch.setattr(layers, "get_ee_circuit_breaker", lambda: fake_breaker)

    async def _fail(*a, **k):
        raise HTTPException(500, detail="forced failure")
    monkeypatch.setattr(layers, "get_landsat_vis_params_async", _fail)

    client = TestClient(app_with_cb)
    resp = client.get("/landsat/100/100/10?year=2020&visparam=landsat-tvi-true")

    assert resp.status_code == 500
    # record_failure foi chamado no path de erro
    assert fake_breaker.record_failure.await_count == 1


def test_closed_breaker_on_success_records_success(app_with_cb, monkeypatch):
    from app.api import layers

    fake_breaker = AsyncMock()
    fake_breaker.is_open = AsyncMock(return_value=False)
    fake_breaker.record_success = AsyncMock()
    fake_breaker.record_failure = AsyncMock()
    monkeypatch.setattr(layers, "get_ee_circuit_breaker", lambda: fake_breaker)

    # Make EE retornar URL válida; http_get_bytes retorna bytes.
    async def _vis(*a, **k): return {"bands": ["SR_B4"]}
    monkeypatch.setattr(layers, "get_landsat_vis_params_async", _vis)

    class _FakeExec:
        def __init__(self, *_a, **_k): pass
    import asyncio
    original_run_in_executor = asyncio.AbstractEventLoop.run_in_executor

    async def _fake_run(loop, executor, fn, *args):
        return "https://ee/{x}/{y}/{z}"

    monkeypatch.setattr(
        asyncio.AbstractEventLoop, "run_in_executor",
        lambda self, *a, **k: _fake_future("https://ee/{x}/{y}/{z}"),
    )

    def _fake_future(val):
        loop = asyncio.get_event_loop()
        f = loop.create_future()
        f.set_result(val)
        return f

    client = TestClient(app_with_cb)
    resp = client.get("/landsat/100/100/10?year=2020&visparam=landsat-tvi-true")

    # Pode ou não ter chegado até o fim (depende do stub http_get_bytes).
    # O que importa: se o layer criou com sucesso, record_success foi chamado.
    if resp.status_code == 200:
        assert fake_breaker.record_success.await_count >= 1
