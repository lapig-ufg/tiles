"""resolve_visualization: aplica o índice declarado no visparam antes do registro do mapa."""
from unittest.mock import patch

import pytest

from app.visualization import indices

NDVI_VIS = {
    "bands": ["NIR", "RED"],
    "min": "-0.2",
    "max": "0.9",
    "gamma": "1.0",
    "palette": ["#a52a2a", "#006837"],
    "index": {"type": "normalized_difference", "bands": ["NIR", "RED"], "name": "NDVI"},
}


def test_sem_indice_devolve_a_mesma_imagem_e_remove_chaves_nulas():
    image = object()
    out_image, vis = indices.resolve_visualization(
        image, {"bands": ["B4"], "min": "0", "max": "1", "gamma": None, "index": None, "palette": None}
    )
    assert out_image is image
    assert vis == {"bands": ["B4"], "min": "0", "max": "1"}


def test_sem_indice_preserva_gamma_informado():
    _, vis = indices.resolve_visualization(object(), {"bands": ["B4"], "min": "0", "max": "1", "gamma": "1.2"})
    assert vis["gamma"] == "1.2"


def test_com_indice_calcula_a_diferenca_normalizada_e_renomeia():
    image = object()
    with patch.object(indices, "ee") as ee_mock:
        derived = ee_mock.Image.return_value.normalizedDifference.return_value.rename.return_value
        out_image, vis = indices.resolve_visualization(image, NDVI_VIS)
    ee_mock.Image.assert_called_once_with(image)
    ee_mock.Image.return_value.normalizedDifference.assert_called_once_with(["NIR", "RED"])
    ee_mock.Image.return_value.normalizedDifference.return_value.rename.assert_called_once_with("NDVI")
    assert out_image is derived
    assert vis == {"bands": ["NDVI"], "min": "-0.2", "max": "0.9", "palette": ["#a52a2a", "#006837"]}


def test_com_indice_descarta_gamma():
    with patch.object(indices, "ee"):
        _, vis = indices.resolve_visualization(object(), NDVI_VIS)
    assert "gamma" not in vis


def test_indice_sem_paleta_nao_emite_chave_palette():
    with patch.object(indices, "ee"):
        _, vis = indices.resolve_visualization(object(), {**NDVI_VIS, "palette": None})
    assert "palette" not in vis


def test_nome_padrao_da_banda_derivada_e_ndvi():
    with patch.object(indices, "ee"):
        _, vis = indices.resolve_visualization(
            object(), {**NDVI_VIS, "index": {"type": "normalized_difference", "bands": ["NIR", "RED"]}}
        )
    assert vis["bands"] == ["NDVI"]


def test_tipo_desconhecido_levanta_value_error():
    with pytest.raises(ValueError, match="evi"):
        indices.resolve_visualization(object(), {**NDVI_VIS, "index": {"type": "evi", "bands": ["NIR", "RED"]}})
