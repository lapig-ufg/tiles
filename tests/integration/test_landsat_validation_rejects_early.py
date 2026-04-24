"""Verifica que o handler `landsat` rejeita combinações inválidas com 422
ANTES de chamar `_serve_tile` (e portanto antes de bater no Earth Engine).
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_layers():
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
    async def _vp(*a, **k): return {"bands": ["SR_B4"]}
    vpdb.get_landsat_vis_params_async = _vp
    vpdb.vis_params_manager = object()
    vpdb.get_visparams_dict = lambda: {}
    vpdb.get_landsat_collection = lambda y: "LANDSAT/LC08/C02/T1_L2"
    sys.modules["app.visualization.vis_params_db"] = vpdb

    vpl = type(sys)("app.visualization.vis_params_loader")
    vpl.VISPARAMS = {}
    vpl.get_VISPARAMS_sync = lambda: {}
    async def _vps(*a, **k): return {}
    vpl.get_visparams = _vps
    vpl.get_landsat_vis_params = lambda *a, **k: {"bands": ["SR_B4"]}
    vpl.get_landsat_collection = lambda year: "LANDSAT/LC08/C02/T1_L2"
    sys.modules["app.visualization.vis_params_loader"] = vpl

    caps = type(sys)("app.utils.capabilities")
    caps.get_capabilities_provider = lambda: type("P", (), {
        "get_capabilities": staticmethod(lambda *a, **k: {"collections": {"landsat": [{"year": 2020}]}}),
    })()
    sys.modules["app.utils.capabilities"] = caps

    svc_tile = type(sys)("app.services.tile")
    svc_tile.tile2goehashBBOX = lambda x, y, z: ({"w": -50, "s": -10, "e": -49, "n": -9}, "abc")
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


def test_invalid_year_returns_422_without_hitting_serve_tile(app_with_layers, monkeypatch):
    from app.api import layers

    calls = []

    async def _serve(*a, **k):
        calls.append(1)
        return None

    monkeypatch.setattr(layers, "_serve_tile", _serve)

    client = TestClient(app_with_layers)
    resp = client.get("/landsat/100/100/10?year=1900&visparam=landsat-tvi-true")

    assert resp.status_code == 422
    assert resp.headers["x-error-reason"] == "invalid_year"
    assert resp.headers["cache-control"] == "no-store, must-revalidate"
    assert len(calls) == 0, "validação deve rejeitar antes de chamar _serve_tile"


def test_invalid_visparam_returns_422_without_hitting_serve_tile(app_with_layers, monkeypatch):
    from app.api import layers

    calls = []

    async def _serve(*a, **k):
        calls.append(1)
        return None

    monkeypatch.setattr(layers, "_serve_tile", _serve)

    client = TestClient(app_with_layers)
    resp = client.get("/landsat/100/100/10?year=2020&visparam=nao-existe")

    assert resp.status_code == 422
    assert resp.headers["x-error-reason"] == "invalid_visparam"
    assert len(calls) == 0


def test_valid_request_reaches_serve_tile(app_with_layers, monkeypatch):
    from app.api import layers
    from fastapi import HTTPException

    calls = []

    async def _serve(*a, **k):
        calls.append(1)
        # Simula erro EE; só queremos confirmar que passou da validação.
        raise HTTPException(503, "simulated")

    monkeypatch.setattr(layers, "_serve_tile", _serve)

    client = TestClient(app_with_layers)
    resp = client.get("/landsat/100/100/10?year=2020&visparam=landsat-tvi-true")

    assert len(calls) == 1, "requisição válida deve chegar em _serve_tile"
    assert resp.status_code == 503
