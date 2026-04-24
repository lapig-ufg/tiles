"""Testes do fallback automático BEST_IMAGE → MOSAIC quando o Earth Engine
retorna `no band named` (PR #3).

Cenas reprocessadas de Landsat 5 por vezes chegam à coleção sem todas as
bandas. Em modo BEST_IMAGE a seleção acaba numa dessas cenas; em MOSAIC
o conjunto é composto e o problema some.
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest


@pytest.fixture
def layers_module():
    from tests.conftest import reset_app_imports
    reset_app_imports()

    # Stubs para deps que não temos (mesmo jeito do teste de integração)
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
    visParam_mod.get_landsat_collection = lambda y: "LANDSAT/LT05/C02/T1_L2"
    visParam_mod.get_landsat_vis_params = lambda *a, **k: {"bands": ["SR_B4", "SR_B5", "SR_B3"]}
    sys.modules["app.visualization.visParam"] = visParam_mod

    vpdb = type(sys)("app.visualization.vis_params_db")
    async def _vp(*a, **k): return {"bands": ["SR_B4", "SR_B5", "SR_B3"], "min": 0, "max": 1}
    vpdb.get_landsat_vis_params_async = _vp
    vpdb.vis_params_manager = object()
    vpdb.get_visparams_dict = lambda: {}
    vpdb.get_landsat_collection = lambda y: "LANDSAT/LT05/C02/T1_L2"
    sys.modules["app.visualization.vis_params_db"] = vpdb

    vpl = type(sys)("app.visualization.vis_params_loader")
    vpl.VISPARAMS = {}
    vpl.get_VISPARAMS_sync = lambda: {}
    async def _vps(*a, **k): return {}
    vpl.get_visparams = _vps
    vpl.get_landsat_vis_params = lambda *a, **k: {"bands": ["SR_B4", "SR_B5", "SR_B3"]}
    vpl.get_landsat_collection = lambda year: "LANDSAT/LT05/C02/T1_L2"
    sys.modules["app.visualization.vis_params_loader"] = vpl

    caps = type(sys)("app.utils.capabilities")
    caps.get_capabilities_provider = lambda: type("P", (), {
        "get_capabilities": staticmethod(lambda *a, **k: {"collections": {"landsat": [{"year": 2006}]}}),
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
    return layers


def test_fallback_retries_with_mosaic_when_best_image_has_no_band(layers_module):
    """Quando BEST_IMAGE levanta 'no band named SR_B4', função refaz com MOSAIC."""
    import ee

    calls = []

    def fake_getMapId(args):
        calls.append(list(args.get("image").__class__.__name__) if args.get("image") else None)
        # Primeira chamada falha, segunda passa. O controle é via contador.
        if len(calls) == 1:
            raise ee.EEException('Image has no band named "SR_B4".')
        return {"tile_fetcher": type("T", (), {"url_format": "https://ee/{x}/{y}/{z}"})()}

    with patch.object(ee.data, "getMapId", side_effect=fake_getMapId):
        url = layers_module._create_landsat_layer_with_params(
            geom=None,
            dates={"dtStart": "2006-01-01", "dtEnd": "2006-04-30"},
            vis={"bands": ["SR_B4", "SR_B5", "SR_B3"], "min": 0, "max": 1},
            composite_mode="BEST_IMAGE",
        )

    assert url == "https://ee/{x}/{y}/{z}"
    assert len(calls) == 2, "deveria ter tentado BEST_IMAGE e então MOSAIC"


def test_fallback_does_not_loop_if_mosaic_also_fails(layers_module):
    """Se MOSAIC também falhar (caso extremo), propaga HTTPException (não looping infinito)."""
    import ee
    from fastapi import HTTPException

    attempts = []

    def fake_getMapId(args):
        attempts.append(1)
        raise ee.EEException('Image has no band named "SR_B4".')

    with patch.object(ee.data, "getMapId", side_effect=fake_getMapId):
        with pytest.raises(HTTPException) as exc_info:
            layers_module._create_landsat_layer_with_params(
                geom=None,
                dates={"dtStart": "2006-01-01", "dtEnd": "2006-04-30"},
                vis={"bands": ["SR_B4", "SR_B5", "SR_B3"], "min": 0, "max": 1},
                composite_mode="BEST_IMAGE",
            )

    # Exatamente 2 tentativas (BEST_IMAGE + MOSAIC). Sem recursão infinita.
    assert len(attempts) == 2
    assert exc_info.value.status_code == 500


def test_mosaic_mode_does_not_retry_on_band_missing(layers_module):
    """Se já entrou em MOSAIC e falha, não há onde recuar — levanta direto."""
    import ee
    from fastapi import HTTPException

    attempts = []

    def fake_getMapId(args):
        attempts.append(1)
        raise ee.EEException('Image has no band named "SR_B4".')

    with patch.object(ee.data, "getMapId", side_effect=fake_getMapId):
        with pytest.raises(HTTPException):
            layers_module._create_landsat_layer_with_params(
                geom=None,
                dates={"dtStart": "2006-01-01", "dtEnd": "2006-04-30"},
                vis={"bands": ["SR_B4", "SR_B5", "SR_B3"], "min": 0, "max": 1},
                composite_mode="MOSAIC",
            )

    assert len(attempts) == 1, "MOSAIC não deve tentar fallback (não existe um)"


def test_non_band_ee_error_is_not_retried(layers_module):
    """Erros EE não-banda (ex.: quota) devem propagar na primeira chamada."""
    import ee

    attempts = []

    def fake_getMapId(args):
        attempts.append(1)
        raise ee.EEException("User memory limit exceeded.")

    with patch.object(ee.data, "getMapId", side_effect=fake_getMapId):
        with pytest.raises(ee.EEException):
            layers_module._create_landsat_layer_with_params(
                geom=None,
                dates={"dtStart": "2006-01-01", "dtEnd": "2006-04-30"},
                vis={"bands": ["SR_B4", "SR_B5", "SR_B3"], "min": 0, "max": 1},
                composite_mode="BEST_IMAGE",
            )

    assert len(attempts) == 1, "erros não-banda não devem retry"


def test_retry_helper_reraises_exc_param_not_bare(layers_module):
    """`_retry_with_mosaic_if_band_missing` deve usar `raise exc` explícito.

    Bare `raise` só funciona dentro de um frame com exceção ativa em sys.exc_info.
    Se a função for chamada fora de except (ex: teste isolado), bare raise quebra
    com RuntimeError. Queremos que ela reerga a própria `exc` recebida.
    """
    import ee
    exc = ee.EEException("some non-band error from EE")
    with pytest.raises(ee.EEException) as exc_info:
        layers_module._retry_with_mosaic_if_band_missing(
            func=lambda *a, **k: None,
            exc=exc,
            composite_mode="MOSAIC",
            call_args=(),
            collection="LANDSAT/LT05/C02/T1_L2",
            bands=["SR_B4"],
        )
    # Confirma que reergueu a mesma exceção, não uma RuntimeError do bare raise.
    assert exc_info.value is exc
