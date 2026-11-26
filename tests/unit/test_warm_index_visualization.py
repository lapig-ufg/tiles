"""Pré-aquecimento com visparam de índice: banda derivada e paleta chegam ao registro do mapa."""
from unittest.mock import MagicMock, patch

import app.tasks.cache_operations as ops

CRED = {"access_token": "at", "project_id": "p", "expires_at": 9999999999999}
DATES = {"dtStart": "2020-01-01", "dtEnd": "2020-12-31"}
PALETTE = ["#a52a2a", "#006837"]


def _s2_config():
    return {
        "select": (["B8", "B4"], ["NIR", "RED"]),
        "visparam": {
            "bands": ["NIR", "RED"], "min": "-0.2", "max": "0.9", "palette": PALETTE,
            "index": {"type": "normalized_difference", "bands": ["NIR", "RED"], "name": "NDVI"},
        },
    }


def _landsat_config():
    return "LANDSAT/LC08/C02/T1_L2", {
        "bands": ["SR_B5", "SR_B4"], "min": "-0.2", "max": "0.9", "palette": PALETTE,
        "index": {"type": "normalized_difference", "bands": ["SR_B5", "SR_B4"], "name": "NDVI"},
    }


def _pool_getmapid(captured):
    def fake(params):
        captured.update(params)
        return {"tile_fetcher": type("T", (), {"url_format": "pool-url"})()}
    return fake


def test_warm_s2_ndvi_com_credencial_envia_banda_derivada_e_paleta():
    with patch.object(ops, "resolve_s2_config_sync", return_value=_s2_config()), \
         patch.object(ops, "get_campaign_token", return_value=CRED), \
         patch.object(ops, "create_map_with_credential",
                      return_value="https://ee/s2/{z}/{x}/{y}") as mock_create:
        url = ops._warm_create_s2_url(MagicMock(), DATES, "tvi-ndvi", tvi_campaign_id="c1")
    assert url == "https://ee/s2/{z}/{x}/{y}"
    _, vis, _ = mock_create.call_args.args
    assert vis == {"bands": ["NDVI"], "min": "-0.2", "max": "0.9", "palette": PALETTE}


def test_warm_s2_ndvi_pelo_pool_envia_banda_derivada():
    captured = {}
    with patch.object(ops, "resolve_s2_config_sync", return_value=_s2_config()), \
         patch.object(ops.ee.data, "getMapId", side_effect=_pool_getmapid(captured)):
        url = ops._warm_create_s2_url(MagicMock(), DATES, "tvi-ndvi")
    assert url == "pool-url"
    assert captured["bands"] == ["NDVI"]
    assert captured["palette"] == PALETTE
    assert "gamma" not in captured
    assert "index" not in captured


def test_warm_landsat_ndvi_pelo_pool_preserva_faixa_e_envia_banda_derivada():
    captured = {}
    with patch.object(ops, "resolve_landsat_config_sync", return_value=_landsat_config()), \
         patch.object(ops.ee.data, "getMapId", side_effect=_pool_getmapid(captured)):
        url = ops._warm_create_landsat_url(MagicMock(), DATES, "landsat-tvi-ndvi", "MOSAIC")
    assert url == "pool-url"
    assert captured["bands"] == ["NDVI"]
    assert captured["min"] == "-0.2"
    assert captured["max"] == "0.9"
    assert captured["palette"] == PALETTE
    assert "index" not in captured


def test_warm_landsat_ndvi_com_credencial_envia_banda_derivada():
    with patch.object(ops, "resolve_landsat_config_sync", return_value=_landsat_config()), \
         patch.object(ops, "get_campaign_token", return_value=CRED), \
         patch.object(ops, "create_map_with_credential",
                      return_value="https://ee/tpl/{z}/{x}/{y}") as mock_create:
        ops._warm_create_landsat_url(MagicMock(), DATES, "landsat-tvi-ndvi", "MOSAIC", tvi_campaign_id="c1")
    _, vis, _ = mock_create.call_args.args
    assert vis == {"bands": ["NDVI"], "min": "-0.2", "max": "0.9", "palette": PALETTE}
