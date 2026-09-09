"""Conversão dos documentos de visparam para o formato consumido pelos construtores."""
import pytest

from app.models.vis_params import VisParamDocument
from app.visualization import vis_params_db

PALETTE = ["#a52a2a", "#006837"]


def _doc_s2_ndvi():
    return VisParamDocument(
        _id="tvi-ndvi", name="tvi-ndvi", display_name="NDVI", category="sentinel2",
        band_config={"original_bands": ["B8", "B4"], "mapped_bands": ["NIR", "RED"]},
        vis_params={
            "bands": ["NIR", "RED"], "min": [-0.2], "max": [0.9], "palette": PALETTE,
            "index": {"type": "normalized_difference", "bands": ["NIR", "RED"], "name": "NDVI"},
        },
    )


def _doc_landsat_ndvi():
    return VisParamDocument(
        _id="landsat-tvi-ndvi", name="landsat-tvi-ndvi", display_name="NDVI", category="landsat",
        satellite_configs=[{
            "collection_id": "LANDSAT/LC08/C02/T1_L2",
            "vis_params": {
                "bands": ["SR_B5", "SR_B4"], "min": [-0.2], "max": [0.9], "palette": PALETTE,
                "index": {"type": "normalized_difference", "bands": ["SR_B5", "SR_B4"], "name": "NDVI"},
            },
        }],
    )


def _doc_s2_rgb():
    return VisParamDocument(
        _id="tvi-rgb", name="tvi-rgb", display_name="RGB", category="sentinel2",
        band_config={"original_bands": ["B4", "B3", "B2"]},
        vis_params={"bands": ["B4", "B3", "B2"], "min": "200, 300, 700", "max": "3000, 2500, 2300", "gamma": "1.35"},
    )


@pytest.fixture
def manager():
    m = vis_params_db.vis_params_manager
    saved_cache, saved_flag = dict(m._cache), m._initialized
    m._cache = {"tvi-ndvi": _doc_s2_ndvi(), "landsat-tvi-ndvi": _doc_landsat_ndvi(), "tvi-rgb": _doc_s2_rgb()}
    m._initialized = True
    yield m
    m._cache, m._initialized = saved_cache, saved_flag


@pytest.mark.asyncio
async def test_sentinel_propaga_index_e_palette_sem_gamma(manager):
    result = await vis_params_db.get_visparams_dict()
    vis = result["tvi-ndvi"]["visparam"]
    assert vis["index"] == {"type": "normalized_difference", "bands": ["NIR", "RED"], "name": "NDVI"}
    assert vis["palette"] == PALETTE
    assert vis["min"] == "-0.2"
    assert vis["max"] == "0.9"
    assert "gamma" not in vis
    assert result["tvi-ndvi"]["select"] == (["B8", "B4"], ["NIR", "RED"])


@pytest.mark.asyncio
async def test_sentinel_sem_indice_mantem_gamma_e_nao_emite_chaves_nulas(manager):
    result = await vis_params_db.get_visparams_dict()
    vis = result["tvi-rgb"]["visparam"]
    assert vis["gamma"] == "1.35"
    assert "index" not in vis
    assert "palette" not in vis


@pytest.mark.asyncio
async def test_landsat_por_colecao_sem_gamma_none(manager):
    vis = await vis_params_db.get_landsat_vis_params_async("landsat-tvi-ndvi", "LANDSAT/LC08/C02/T1_L2")
    assert vis["bands"] == ["SR_B5", "SR_B4"]
    assert vis["index"]["name"] == "NDVI"
    assert vis["palette"] == PALETTE
    assert "gamma" not in vis


@pytest.mark.asyncio
async def test_landsat_no_dicionario_geral_sem_gamma_none(manager):
    result = await vis_params_db.get_visparams_dict()
    vis = result["landsat-tvi-ndvi"]["visparam"]["LANDSAT/LC08/C02/T1_L2"]
    assert "gamma" not in vis
    assert vis["index"]["bands"] == ["SR_B5", "SR_B4"]
