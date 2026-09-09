"""Entradas NDVI do dicionário fixo: coerentes com o modelo e aceitas na validação Landsat."""
from app.models.vis_params import VisParam
from app.visualization.validation import KNOWN_LANDSAT_VISPARAMS
from app.visualization.visParam import NDVI_PALETTE, VISPARAMS, get_landsat_vis_params

LANDSAT_NIR_RED = {
    "LANDSAT/LT05/C02/T1_L2": ["SR_B4", "SR_B3"],
    "LANDSAT/LE07/C02/T1_L2": ["SR_B4", "SR_B3"],
    "LANDSAT/LC08/C02/T1_L2": ["SR_B5", "SR_B4"],
    "LANDSAT/LC09/C02/T1_L2": ["SR_B5", "SR_B4"],
}


def test_paleta_de_nove_cores():
    assert len(NDVI_PALETTE) == 9
    assert NDVI_PALETTE[0] == "#a52a2a"
    assert NDVI_PALETTE[-1] == "#006837"


def test_tvi_ndvi_valida_no_modelo():
    entry = VISPARAMS["tvi-ndvi"]
    vp = VisParam(**entry["visparam"])
    assert entry["display_name"] == "NDVI"
    assert entry["select"] == (["B8", "B4"], ["NIR", "RED"])
    assert vp.bands == ["NIR", "RED"]
    assert vp.index.bands == ["NIR", "RED"]
    assert vp.index.name == "NDVI"
    assert vp.min == [-0.2]
    assert vp.max == [0.9]
    assert vp.palette == NDVI_PALETTE
    assert vp.gamma is None


def test_landsat_tvi_ndvi_por_colecao():
    assert VISPARAMS["landsat-tvi-ndvi"]["display_name"] == "NDVI"
    for collection, bands in LANDSAT_NIR_RED.items():
        vis = get_landsat_vis_params("landsat-tvi-ndvi", collection)
        vp = VisParam(**vis)
        assert vp.bands == bands, collection
        assert vp.index.bands == bands, collection
        assert vp.min == [-0.2]
        assert vp.max == [0.9]
        assert vp.palette == NDVI_PALETTE
        assert "gamma" not in vis


def test_landsat_tvi_ndvi_aceito_na_validacao():
    assert "landsat-tvi-ndvi" in KNOWN_LANDSAT_VISPARAMS


def test_entradas_existentes_nao_mudaram():
    assert VISPARAMS["tvi-red"]["visparam"]["bands"] == ["REDEDGE4", "SWIR1", "RED"]
    assert "display_name" not in VISPARAMS["tvi-red"]
