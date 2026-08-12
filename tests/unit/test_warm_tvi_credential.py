"""Warming com credencial da campanha (TVI moderno).

`ops` é importado no momento da coleta (antes de qualquer fixture chamar
`reset_app_imports()`), e os colaboradores são trocados com `patch.object`/
`patch.dict(sys.modules, ...)` — nunca por caminho string, que forçaria um
re-import num `sys.modules` já podado por outros testes.
"""
import sys
import types
from unittest.mock import MagicMock, patch

import app.tasks.cache_operations as ops

CRED = {"access_token": "at", "project_id": "p", "expires_at": 9999999999999}

DATES = {"dtStart": "2020-01-01", "dtEnd": "2020-12-31"}


def _vis_param_stub():
    mod = types.ModuleType("app.visualization.visParam")
    mod.get_landsat_collection = lambda year: "LANDSAT/X"
    mod.get_landsat_vis_params = lambda name, collection: {
        "bands": ["B1"], "min": [0], "max": [1],
    }
    return mod


def _loader_stub():
    mod = types.ModuleType("app.visualization.vis_params_loader")
    mod.get_VISPARAMS_sync = lambda: {
        "tvi-green": {"select": ["B4", "B3", "B2"], "visparam": {"min": 0}},
    }
    return mod


def test_warm_landsat_usa_credencial_da_campanha():
    with patch.dict(sys.modules, {"app.visualization.visParam": _vis_param_stub()}), \
         patch.object(ops, "get_campaign_token", return_value=CRED) as mock_token, \
         patch.object(ops, "create_map_with_credential",
                      return_value="https://ee/tpl/{z}/{x}/{y}") as mock_create, \
         patch.object(ops.ee.data, "getMapId") as mock_getmapid:
        url = ops._warm_create_landsat_url(
            MagicMock(), DATES, "landsat-tvi-false", "MOSAIC", tvi_campaign_id="c1",
        )
    assert url == "https://ee/tpl/{z}/{x}/{y}"
    mock_token.assert_called_once_with("c1")
    mock_create.assert_called_once()
    mock_getmapid.assert_not_called()


def test_warm_s2_usa_credencial_da_campanha():
    with patch.dict(sys.modules, {"app.visualization.vis_params_loader": _loader_stub()}), \
         patch.object(ops, "get_campaign_token", return_value=CRED) as mock_token, \
         patch.object(ops, "create_map_with_credential",
                      return_value="https://ee/s2/{z}/{x}/{y}"), \
         patch.object(ops.ee.data, "getMapId") as mock_getmapid:
        url = ops._warm_create_s2_url(MagicMock(), DATES, "tvi-green", tvi_campaign_id="c1")
    assert url == "https://ee/s2/{z}/{x}/{y}"
    mock_token.assert_called_once_with("c1")
    mock_getmapid.assert_not_called()


def test_warm_landsat_sem_campanha_segue_pelo_pool():
    with patch.dict(sys.modules, {"app.visualization.visParam": _vis_param_stub()}), \
         patch.object(ops, "get_campaign_token") as mock_token, \
         patch.object(ops, "create_map_with_credential") as mock_create, \
         patch.object(ops.ee.data, "getMapId",
                      return_value={"tile_fetcher": type("T", (), {"url_format": "pool-url"})()}):
        url = ops._warm_create_landsat_url(MagicMock(), DATES, "landsat-tvi-false", "MOSAIC")
    assert url == "pool-url"
    mock_token.assert_not_called()
    mock_create.assert_not_called()
