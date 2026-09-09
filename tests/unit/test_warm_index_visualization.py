"""Pré-aquecimento com visparam de índice: banda derivada e paleta chegam ao registro do mapa.

Mesma técnica de `test_warm_tvi_credential.py`: `ops` importado na coleta e
colaboradores trocados com `patch.object` e `patch.dict(sys.modules, ...)`.
"""
import sys
import types
from unittest.mock import MagicMock, patch

import app.tasks.cache_operations as ops

CRED = {"access_token": "at", "project_id": "p", "expires_at": 9999999999999}
DATES = {"dtStart": "2020-01-01", "dtEnd": "2020-12-31"}
PALETTE = ["#a52a2a", "#006837"]


def _loader_stub():
    mod = types.ModuleType("app.visualization.vis_params_loader")
    mod.get_VISPARAMS_sync = lambda: {
        "tvi-ndvi": {
            "select": (["B8", "B4"], ["NIR", "RED"]),
            "visparam": {
                "bands": ["NIR", "RED"], "min": "-0.2", "max": "0.9", "palette": PALETTE,
                "index": {"type": "normalized_difference", "bands": ["NIR", "RED"], "name": "NDVI"},
            },
        },
    }
    return mod


def _vis_param_stub():
    mod = types.ModuleType("app.visualization.visParam")
    mod.get_landsat_collection = lambda year: "LANDSAT/LC08/C02/T1_L2"
    mod.get_landsat_vis_params = lambda name, collection: {
        "bands": ["SR_B5", "SR_B4"], "min": [-0.2], "max": [0.9], "palette": PALETTE,
        "index": {"type": "normalized_difference", "bands": ["SR_B5", "SR_B4"], "name": "NDVI"},
    }
    return mod


def _pool_getmapid(captured):
    def fake(params):
        captured.update(params)
        return {"tile_fetcher": type("T", (), {"url_format": "pool-url"})()}
    return fake


def test_warm_s2_ndvi_com_credencial_envia_banda_derivada_e_paleta():
    with patch.dict(sys.modules, {"app.visualization.vis_params_loader": _loader_stub()}), \
         patch.object(ops, "get_campaign_token", return_value=CRED), \
         patch.object(ops, "create_map_with_credential",
                      return_value="https://ee/s2/{z}/{x}/{y}") as mock_create:
        url = ops._warm_create_s2_url(MagicMock(), DATES, "tvi-ndvi", tvi_campaign_id="c1")
    assert url == "https://ee/s2/{z}/{x}/{y}"
    _, vis, _ = mock_create.call_args.args
    assert vis == {"bands": ["NDVI"], "min": "-0.2", "max": "0.9", "palette": PALETTE}


def test_warm_s2_ndvi_pelo_pool_envia_banda_derivada():
    captured = {}
    with patch.dict(sys.modules, {"app.visualization.vis_params_loader": _loader_stub()}), \
         patch.object(ops.ee.data, "getMapId", side_effect=_pool_getmapid(captured)):
        url = ops._warm_create_s2_url(MagicMock(), DATES, "tvi-ndvi")
    assert url == "pool-url"
    assert captured["bands"] == ["NDVI"]
    assert captured["palette"] == PALETTE
    assert "gamma" not in captured
    assert "index" not in captured


def test_warm_landsat_ndvi_pelo_pool_converte_faixa_e_envia_banda_derivada():
    captured = {}
    with patch.dict(sys.modules, {"app.visualization.visParam": _vis_param_stub()}), \
         patch.object(ops.ee.data, "getMapId", side_effect=_pool_getmapid(captured)):
        url = ops._warm_create_landsat_url(MagicMock(), DATES, "landsat-tvi-ndvi", "MOSAIC")
    assert url == "pool-url"
    assert captured["bands"] == ["NDVI"]
    assert captured["min"] == "-0.2"
    assert captured["max"] == "0.9"
    assert captured["palette"] == PALETTE
    assert "index" not in captured


def test_warm_landsat_ndvi_com_credencial_envia_banda_derivada():
    with patch.dict(sys.modules, {"app.visualization.visParam": _vis_param_stub()}), \
         patch.object(ops, "get_campaign_token", return_value=CRED), \
         patch.object(ops, "create_map_with_credential",
                      return_value="https://ee/tpl/{z}/{x}/{y}") as mock_create:
        ops._warm_create_landsat_url(MagicMock(), DATES, "landsat-tvi-ndvi", "MOSAIC", tvi_campaign_id="c1")
    _, vis, _ = mock_create.call_args.args
    assert vis == {"bands": ["NDVI"], "min": [-0.2], "max": [0.9], "palette": PALETTE}
