"""Teste de integração leve: handlers de tile devolvem status real (não 200)
quando `_serve_tile` levanta HTTPException ou exceção genérica. Valida a
ligação entre os handlers e `tile_error_response.from_exception`.
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_layers():
    # Forçar re-import limpando bindings anteriores — outros testes podem ter
    # instalado stubs diferentes e layers.py pode ter capturado refs antigas.
    from tests.conftest import reset_app_imports
    reset_app_imports()

    # Substituir cadeia de módulos pesados por stubs antes do import de layers.
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

    # Stub rate_limiter: decoradores viram passthrough
    rate_limiter_mod = type(sys)("app.middleware.rate_limiter")
    def _pt(*_a, **_k):
        def deco(fn): return fn
        return deco
    rate_limiter_mod.limit_sentinel = _pt
    rate_limiter_mod.limit_landsat = _pt
    sys.modules["app.middleware.rate_limiter"] = rate_limiter_mod

    # Stubs para vis params e capabilities (carregados por layers)
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
    async def _vp(*a, **k): return {"bands": ["B4", "B3", "B2"], "min": 0, "max": 1}
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
            "tvi-red":          {"bands": ["B4"]},
            "landsat-tvi-true": {"bands": ["SR_B4", "SR_B3", "SR_B2"]},
            "landsat-tvi-false":{"bands": ["SR_B4", "SR_B5", "SR_B3"]},
            "landsat-tvi-agri": {"bands": ["SR_B6", "SR_B5", "SR_B4"]},
        }
    vpl.get_visparams = _vps
    vpl.get_landsat_vis_params = lambda *a, **k: {"bands": ["SR_B4"]}
    vpl.get_landsat_collection = lambda year: "LANDSAT/LC08/C02/T1_L2"
    sys.modules["app.visualization.vis_params_loader"] = vpl

    caps = type(sys)("app.utils.capabilities")

    class _FakeProvider:
        async def get_capabilities(self):
            vps = ["landsat-tvi-true", "landsat-tvi-false", "landsat-tvi-agri",
                   "tvi-red"]
            return {"collections": [
                {"name": "landsat",       "year": list(range(1984, 2030)),
                 "period": ["WET", "DRY", "MONTH"], "visparam": vps},
                {"name": "s2_harmonized", "year": list(range(2015, 2030)),
                 "period": ["WET", "DRY", "MONTH"], "visparam": vps},
            ]}

    caps.get_capabilities_provider = lambda: _FakeProvider()
    sys.modules["app.utils.capabilities"] = caps

    svc_tile = type(sys)("app.services.tile")
    # tile2goehashBBOX retorna (geohash, bbox) nessa ordem
    svc_tile.tile2goehashBBOX = lambda x, y, z: ("abc", {"w": -50, "s": -10, "e": -49, "n": -9})
    sys.modules["app.services.tile"] = svc_tile

    gee_pool = type(sys)("app.core.gee_pool")
    # usado como @gee_retry(...) — é factory de decorador
    gee_pool.gee_retry = lambda *a, **k: (lambda fn: fn)
    sys.modules["app.core.gee_pool"] = gee_pool

    http_util = type(sys)("app.utils.http")
    async def _hgb(url, **k): return b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    http_util.http_get_bytes = _hgb
    sys.modules["app.utils.http"] = http_util

    from app.api import layers

    app = FastAPI()
    app.include_router(layers.router)
    return app


def test_landsat_handler_returns_real_http_status_on_http_exception(app_with_layers, monkeypatch):
    from app.api import layers

    async def _raise_http(*_args, **_kwargs):
        raise HTTPException(status_code=503, detail="Earth Engine temporarily unavailable")

    monkeypatch.setattr(layers, "_serve_tile", _raise_http)

    client = TestClient(app_with_layers)
    resp = client.get("/landsat/100/100/10?year=2020&visparam=landsat-tvi-true")

    assert resp.status_code == 503
    assert resp.headers["cache-control"] == "no-store, must-revalidate"
    assert resp.headers["x-error-reason"] == "ee_unavailable"
    assert resp.headers["content-type"] == "image/png"


def test_landsat_handler_returns_429_with_retry_after(app_with_layers, monkeypatch):
    from app.api import layers

    async def _raise_429(*_args, **_kwargs):
        raise HTTPException(status_code=429, detail="rate limited")

    monkeypatch.setattr(layers, "_serve_tile", _raise_429)

    client = TestClient(app_with_layers)
    resp = client.get("/landsat/100/100/10?year=2020&visparam=landsat-tvi-true")

    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "30"
    assert resp.headers["x-error-reason"] == "ee_rate_limit"
    assert resp.headers["cache-control"] == "no-store, must-revalidate"


def test_landsat_handler_returns_500_on_unexpected(app_with_layers, monkeypatch):
    from app.api import layers

    async def _raise_generic(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(layers, "_serve_tile", _raise_generic)

    client = TestClient(app_with_layers)
    resp = client.get("/landsat/100/100/10?year=2020&visparam=landsat-tvi-true")

    assert resp.status_code == 500
    assert resp.headers["x-error-reason"] == "internal_error"
    assert resp.headers["cache-control"] == "no-store, must-revalidate"


def test_s2_harmonized_handler_returns_real_status(app_with_layers, monkeypatch):
    from app.api import layers

    async def _raise_http(*_args, **_kwargs):
        raise HTTPException(status_code=504, detail="gateway timeout")

    monkeypatch.setattr(layers, "_serve_tile", _raise_http)

    client = TestClient(app_with_layers)
    resp = client.get("/s2_harmonized/100/100/10?year=2020&visparam=tvi-red")

    assert resp.status_code == 504
    assert resp.headers["x-error-reason"] == "ee_timeout"
    assert resp.headers["cache-control"] == "no-store, must-revalidate"


# -------------------------------------------------------------------------
# Testes que exercitam o caminho real dentro de `_serve_tile` (sem mockar
# o wrapper inteiro). Se o except interno não estiver bem ligado em
# `tile_error_response.from_exception`, estes testes quebram.
# -------------------------------------------------------------------------

def test_internal_serve_tile_ee_layer_creation_failure_returns_real_status(
    app_with_layers, monkeypatch,
):
    """Simula falha durante criação do layer EE (linha ~521 de layers.py).

    O caminho dentro de `_serve_tile` tem `except Exception: return
    tile_error_response.from_exception(e)`. Queremos garantir que esse
    retorno chega intacto ao cliente — status 503 + no-store + X-Error-Reason.
    """
    from app.api import layers
    from unittest.mock import AsyncMock

    # CB fechado (não interfere) — sem Redis real em teste.
    _cb = AsyncMock()
    _cb.is_open = AsyncMock(return_value=False)
    _cb.record_success = AsyncMock()
    _cb.record_failure = AsyncMock()
    monkeypatch.setattr(layers, "get_ee_circuit_breaker", lambda: _cb)

    async def _boom(*_a, **_k):
        raise HTTPException(status_code=503, detail="ee down")

    monkeypatch.setattr(layers, "get_landsat_vis_params_async", _boom)

    client = TestClient(app_with_layers)
    resp = client.get("/landsat/100/100/10?year=2020&visparam=landsat-tvi-true")

    assert resp.status_code == 503
    assert resp.headers["x-error-reason"] == "ee_unavailable"
    assert resp.headers["cache-control"] == "no-store, must-revalidate"
    assert resp.headers["content-type"] == "image/png"


def test_internal_serve_tile_tile_download_failure_returns_real_status(
    app_with_layers, monkeypatch,
):
    """Simula 429 real do EE durante download do tile (linha ~559 de layers.py).

    O `except HTTPException` interno retorna `tile_error_response.from_exception`.
    Queremos status 429 + Retry-After + X-Error-Reason: ee_rate_limit.
    """
    from app.api import layers
    from unittest.mock import AsyncMock

    _cb = AsyncMock()
    _cb.is_open = AsyncMock(return_value=False)
    _cb.record_success = AsyncMock()
    _cb.record_failure = AsyncMock()
    monkeypatch.setattr(layers, "get_ee_circuit_breaker", lambda: _cb)

    # Cache meta válido → pula branch de criação de layer, vai direto pro download.
    async def _valid_meta(*_a, **_k):
        return {"url": "https://ee.example/{x}/{y}/{z}",
                "date": "2999-01-01T00:00:00"}
    monkeypatch.setattr(layers, "get_meta", _valid_meta)

    async def _boom(*_a, **_k):
        raise HTTPException(status_code=429, detail="rate limited")
    monkeypatch.setattr(layers, "_http_get_bytes", _boom)

    client = TestClient(app_with_layers)
    resp = client.get("/landsat/100/100/10?year=2020&visparam=landsat-tvi-true")

    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "30"
    assert resp.headers["x-error-reason"] == "ee_rate_limit"
    assert resp.headers["cache-control"] == "no-store, must-revalidate"
