"""Warming com credencial da campanha (TVI moderno)."""
from unittest.mock import MagicMock, patch

import app.tasks.cache_operations as ops

CRED = {"access_token": "at", "project_id": "p", "expires_at": 9999999999999}

DATES = {"dtStart": "2020-01-01", "dtEnd": "2020-12-31"}


def _landsat_config():
    return "LANDSAT/X", {"bands": ["B1"], "min": "0.0", "max": "1.0"}


def _s2_config():
    return {"select": ["B4", "B3", "B2"], "visparam": {"min": 0}}


def test_warm_landsat_usa_credencial_da_campanha():
    with patch.object(ops, "resolve_landsat_config_sync", return_value=_landsat_config()), \
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
    with patch.object(ops, "resolve_s2_config_sync", return_value=_s2_config()), \
         patch.object(ops, "get_campaign_token", return_value=CRED) as mock_token, \
         patch.object(ops, "create_map_with_credential",
                      return_value="https://ee/s2/{z}/{x}/{y}"), \
         patch.object(ops.ee.data, "getMapId") as mock_getmapid:
        url = ops._warm_create_s2_url(MagicMock(), DATES, "tvi-green", tvi_campaign_id="c1")
    assert url == "https://ee/s2/{z}/{x}/{y}"
    mock_token.assert_called_once_with("c1")
    mock_getmapid.assert_not_called()


def test_warm_landsat_sem_campanha_segue_pelo_pool():
    with patch.object(ops, "resolve_landsat_config_sync", return_value=_landsat_config()), \
         patch.object(ops, "get_campaign_token") as mock_token, \
         patch.object(ops, "create_map_with_credential") as mock_create, \
         patch.object(ops.ee.data, "getMapId",
                      return_value={"tile_fetcher": type("T", (), {"url_format": "pool-url"})()}):
        url = ops._warm_create_landsat_url(MagicMock(), DATES, "landsat-tvi-false", "MOSAIC")
    assert url == "pool-url"
    mock_token.assert_not_called()
    mock_create.assert_not_called()
