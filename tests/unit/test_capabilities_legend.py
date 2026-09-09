"""Legenda dos visparams de índice publicada em visparam_details."""
import sys
from unittest.mock import patch

import pytest

from app.utils import capabilities as caps
from app.visualization import visParam as real_visparam

PALETTE = ["#a52a2a", "#006837"]
NDVI_INDEX = {"type": "normalized_difference", "bands": ["NIR", "RED"], "name": "NDVI"}


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, length=None):
        return list(self._docs)


class _Collection:
    def __init__(self, docs):
        self._docs = docs

    def find(self, _query):
        return _Cursor(self._docs)


class _Db:
    def __init__(self, docs):
        self.vis_params = _Collection(docs)


DOCS = [
    {"name": "tvi-red", "display_name": "TVI Red", "category": "sentinel2", "active": True,
     "vis_params": {"bands": ["REDEDGE4", "SWIR1", "RED"], "min": [700, 600, 400],
                    "max": [5400, 4300, 2800], "gamma": 1.1}},
    {"name": "tvi-ndvi", "display_name": "NDVI", "category": "sentinel2", "active": True,
     "vis_params": {"bands": ["NIR", "RED"], "min": [-0.2], "max": [0.9], "palette": PALETTE,
                    "index": NDVI_INDEX}},
    {"name": "landsat-tvi-ndvi", "display_name": "NDVI", "category": "landsat", "active": True,
     "satellite_configs": [{
         "collection_id": "LANDSAT/LC08/C02/T1_L2",
         "vis_params": {"bands": ["SR_B5", "SR_B4"], "min": "-0.2", "max": "0.9", "palette": PALETTE,
                        "index": {"type": "normalized_difference", "bands": ["SR_B5", "SR_B4"],
                                  "name": "NDVI"}},
     }]},
]


def _details(capabilities, satellite):
    coll = next(c for c in capabilities["collections"] if c["satellite"] == satellite)
    return {d["name"]: d for d in coll["visparam_details"]}


def test_build_legend_com_faixa_em_lista():
    legend = caps.build_legend({"min": [-0.2], "max": [0.9], "palette": PALETTE, "index": NDVI_INDEX})
    assert legend == {"label": "NDVI", "min": -0.2, "max": 0.9, "palette": PALETTE}


def test_build_legend_com_faixa_em_string():
    legend = caps.build_legend({"min": "-0.2", "max": "0.9", "palette": PALETTE, "index": NDVI_INDEX})
    assert legend["min"] == -0.2
    assert legend["max"] == 0.9


def test_build_legend_sem_indice_devolve_none():
    assert caps.build_legend({"bands": ["B4"], "min": [0], "max": [1]}) is None
    assert caps.build_legend(None) is None


@pytest.mark.asyncio
async def test_capabilities_dinamicas_incluem_legend_apenas_nos_indices():
    provider = caps.CapabilitiesProvider()
    with patch.object(caps, "get_database", return_value=_Db(DOCS)):
        result = await provider.get_capabilities()
    sentinel = _details(result, "sentinel")
    landsat = _details(result, "landsat")
    assert "legend" not in sentinel["tvi-red"]
    assert sentinel["tvi-ndvi"]["legend"] == {"label": "NDVI", "min": -0.2, "max": 0.9, "palette": PALETTE}
    assert landsat["landsat-tvi-ndvi"]["legend"] == {"label": "NDVI", "min": -0.2, "max": 0.9, "palette": PALETTE}


def test_fallback_fixo_lista_ndvi_com_legend():
    # Outras suítes substituem app.visualization.visParam em sys.modules por stubs
    # e não restauram; o fallback importa o módulo sob demanda.
    with patch.dict(sys.modules, {"app.visualization.visParam": real_visparam}):
        result = caps.CapabilitiesProvider()._get_hardcoded_capabilities()
    sentinel_coll = next(c for c in result["collections"] if c["satellite"] == "sentinel")
    landsat_coll = next(c for c in result["collections"] if c["satellite"] == "landsat")
    assert "tvi-ndvi" in sentinel_coll["visparam"]
    assert "landsat-tvi-ndvi" in landsat_coll["visparam"]
    sentinel = _details(result, "sentinel")
    landsat = _details(result, "landsat")
    assert sentinel["tvi-ndvi"]["display_name"] == "NDVI"
    assert sentinel["tvi-ndvi"]["legend"]["label"] == "NDVI"
    assert sentinel["tvi-ndvi"]["legend"]["min"] == -0.2
    assert landsat["landsat-tvi-ndvi"]["legend"]["max"] == 0.9
    assert len(landsat["landsat-tvi-ndvi"]["legend"]["palette"]) == 9
    assert "legend" not in sentinel["tvi-red"]
