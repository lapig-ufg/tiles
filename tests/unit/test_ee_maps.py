"""Criação de mapa EE via REST com credencial por requisição."""
import pytest
from unittest.mock import MagicMock, patch

from app.utils import ee_maps

CRED = {"access_token": "at-user", "project_id": "proj-1", "expires_at": 9999999999999}


def _image():
    image = MagicMock()
    image.visualize.return_value = MagicMock(name="visualized")
    return image


@patch("app.utils.ee_maps.ee.serializer.encode", return_value={"result": "0", "values": {}})
@patch("app.utils.ee_maps.requests.post")
def test_cria_mapa_com_bearer_do_usuario(mock_post, _enc):
    mock_post.return_value = MagicMock(
        status_code=200, json=lambda: {"name": "projects/proj-1/maps/m1"}
    )
    image = _image()
    url = ee_maps.create_map_with_credential(
        image, {"bands": ["B4"], "min": 0, "max": 3000, "select": ["ignorado"]}, CRED
    )
    assert url == "https://earthengine.googleapis.com/v1/projects/proj-1/maps/m1/tiles/{z}/{x}/{y}"
    image.visualize.assert_called_once_with(bands=["B4"], min=0, max=3000)
    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer at-user"
    assert kwargs["headers"]["X-Goog-User-Project"] == "proj-1"


@patch("app.utils.ee_maps.ee.serializer.encode", return_value={})
@patch("app.utils.ee_maps.requests.post")
def test_erro_do_ee_vira_runtime_error(mock_post, _enc):
    mock_post.return_value = MagicMock(status_code=403, text='{"error": "denied"}')
    with pytest.raises(RuntimeError):
        ee_maps.create_map_with_credential(_image(), {}, CRED)
