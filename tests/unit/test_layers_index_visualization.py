"""Construtores de camada renderizam visparams de índice (banda derivada + paleta).

A fixture replica os stubs de `test_landsat_band_fallback.py`: o módulo
`app.api.layers` importa cache, rate limiter e loaders pesados que não fazem
parte do que se testa aqui.
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

PALETTE = ["#a52a2a", "#006837"]
S2_NDVI = {
    "select": (["B8", "B4"], ["NIR", "RED"]),
    "visparam": {
        "bands": ["NIR", "RED"], "min": "-0.2", "max": "0.9", "palette": PALETTE,
        "index": {"type": "normalized_difference", "bands": ["NIR", "RED"], "name": "NDVI"},
    },
}
S2_RED = {
    "select": (["B4", "B8A", "B11"], ["RED", "REDEDGE4", "SWIR1"]),
    "visparam": {"gamma": "1.1", "max": "5400, 4300, 2800", "min": "700, 600, 400",
                 "bands": ["REDEDGE4", "SWIR1", "RED"]},
}
LANDSAT_NDVI = {
    "bands": ["SR_B5", "SR_B4"], "min": "-0.2", "max": "0.9", "palette": PALETTE,
    "index": {"type": "normalized_difference", "bands": ["SR_B5", "SR_B4"], "name": "NDVI"},
}
DATES = {"dtStart": "2020-06-01", "dtEnd": "2020-10-30"}


@pytest.fixture
def layers_module():
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
    cache_mod.adelete_meta = _noop
    cache_mod.atile_lock = _fake_lock
    cache_mod.PNG_TTL = 30 * 24 * 3600
    cache_mod.PNG_TTL_HISTORICAL = 365 * 24 * 3600
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
        "landsat-tvi-true": {"visparam": {}},
        "landsat-tvi-false": {"visparam": {}},
        "landsat-tvi-agri": {"visparam": {}},
        "landsat-tvi-ndvi": {"visparam": {}},
    }
    visParam_mod.NDVI_PALETTE = PALETTE
    visParam_mod.get_landsat_collection = lambda y: "LANDSAT/LC08/C02/T1_L2"
    visParam_mod.get_landsat_vis_params = lambda *a, **k: dict(LANDSAT_NDVI)
    sys.modules["app.visualization.visParam"] = visParam_mod

    vpdb = type(sys)("app.visualization.vis_params_db")
    async def _vp(*a, **k): return dict(LANDSAT_NDVI)
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
    vpl.get_landsat_vis_params = lambda *a, **k: dict(LANDSAT_NDVI)
    vpl.get_landsat_collection = lambda year: "LANDSAT/LC08/C02/T1_L2"
    sys.modules["app.visualization.vis_params_loader"] = vpl

    caps = type(sys)("app.utils.capabilities")
    caps.get_capabilities_provider = lambda: type("P", (), {
        "get_capabilities": staticmethod(lambda *a, **k: {"collections": []}),
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
    class _EarthEngineRateLimitedError(Exception):
        def __init__(self, message: str = "", sa_name=None):
            super().__init__(message)
            self.sa_name = sa_name
    http_util.EarthEngineRateLimitedError = _EarthEngineRateLimitedError
    sys.modules["app.utils.http"] = http_util

    ee_tile_fetch_mod = type(sys)("app.utils.ee_tile_fetch")
    async def _fetch_tile(*_a, **_k): return b""
    ee_tile_fetch_mod.fetch_tile_with_rotation = _fetch_tile
    sys.modules["app.utils.ee_tile_fetch"] = ee_tile_fetch_mod

    from app.api import layers
    return layers


def _capture_getmapid(captured):
    def fake(params):
        captured.update(params)
        return {"tile_fetcher": type("T", (), {"url_format": "https://ee/{x}/{y}/{z}"})()}
    return fake


def test_s2_com_indice_registra_banda_derivada_e_paleta(layers_module):
    import ee
    captured = {}
    with patch.object(ee.data, "getMapId", side_effect=_capture_getmapid(captured)):
        url = layers_module._create_s2_layer_sync(None, DATES, S2_NDVI)
    assert url == "https://ee/{x}/{y}/{z}"
    assert captured["bands"] == ["NDVI"]
    assert captured["palette"] == PALETTE
    assert captured["min"] == "-0.2"
    assert captured["max"] == "0.9"
    assert "gamma" not in captured
    assert "index" not in captured


def test_s2_sem_indice_mantem_parametros_originais(layers_module):
    import ee
    captured = {}
    with patch.object(ee.data, "getMapId", side_effect=_capture_getmapid(captured)):
        layers_module._create_s2_layer_sync(None, DATES, S2_RED)
    assert captured["bands"] == ["REDEDGE4", "SWIR1", "RED"]
    assert captured["gamma"] == "1.1"
    assert "palette" not in captured


def test_landsat_with_params_com_indice_registra_banda_derivada(layers_module):
    import ee
    captured = {}
    with patch.object(ee.data, "getMapId", side_effect=_capture_getmapid(captured)):
        url = layers_module._create_landsat_layer_with_params(None, DATES, dict(LANDSAT_NDVI), "BEST_IMAGE")
    assert url == "https://ee/{x}/{y}/{z}"
    assert captured["bands"] == ["NDVI"]
    assert captured["palette"] == PALETTE
    assert "index" not in captured


def test_landsat_sync_com_indice_registra_banda_derivada(layers_module):
    import ee
    captured = {}
    with patch.object(ee.data, "getMapId", side_effect=_capture_getmapid(captured)):
        url = layers_module._create_landsat_layer_sync(None, DATES, "landsat-tvi-ndvi", "MOSAIC")
    assert url == "https://ee/{x}/{y}/{z}"
    assert captured["bands"] == ["NDVI"]
    assert captured["min"] == "-0.2"
    assert captured["palette"] == PALETTE
