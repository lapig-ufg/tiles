"""Modelo VisParam com índice declarativo (campos palette e index)."""
import pytest
from pydantic import ValidationError

from app.models.vis_params import IndexConfig, VisParam

NDVI_INDEX = {"type": "normalized_difference", "bands": ["NIR", "RED"], "name": "NDVI"}
PALETTE = ["#a52a2a", "#006837"]


def test_documento_atual_sem_indice_continua_valido():
    vp = VisParam(bands=["SWIR1", "REDEDGE4", "RED"], min="600, 700, 400",
                  max="4300, 5400, 2800", gamma="1.1")
    assert vp.min == [600.0, 700.0, 400.0]
    assert vp.gamma == 1.1
    assert vp.palette is None
    assert vp.index is None


def test_gamma_ausente_e_valido():
    vp = VisParam(bands=["B4", "B3", "B2"], min=[0, 0, 0], max=[3000, 3000, 3000])
    assert vp.gamma is None


def test_indice_com_paleta_e_faixa_unica():
    vp = VisParam(bands=["NIR", "RED"], min="-0.2", max="0.9", palette=PALETTE, index=NDVI_INDEX)
    assert vp.index.name == "NDVI"
    assert vp.index.bands == ["NIR", "RED"]
    assert vp.min == [-0.2]
    assert vp.max == [0.9]
    assert vp.palette == PALETTE


def test_rejeita_banda_do_indice_fora_de_bands():
    with pytest.raises(ValidationError, match="index bands"):
        VisParam(bands=["NIR"], min=[-0.2], max=[0.9], index=NDVI_INDEX)


def test_rejeita_paleta_sem_indice():
    with pytest.raises(ValidationError, match="palette requires index"):
        VisParam(bands=["B4", "B3", "B2"], min=[0, 0, 0], max=[1, 1, 1], palette=PALETTE)


def test_rejeita_faixa_multivalor_com_indice():
    with pytest.raises(ValidationError, match="single value"):
        VisParam(bands=["NIR", "RED"], min=[-0.2, 0], max=[0.9, 1], index=NDVI_INDEX)


def test_indice_exige_exatamente_duas_bandas():
    with pytest.raises(ValidationError):
        IndexConfig(type="normalized_difference", bands=["NIR"])


def test_tipo_de_indice_desconhecido_e_rejeitado():
    with pytest.raises(ValidationError):
        IndexConfig(type="evi", bands=["NIR", "RED"])
