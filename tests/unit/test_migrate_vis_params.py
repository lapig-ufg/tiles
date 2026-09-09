"""Semente dos visparams: rótulo explícito, propagação do índice e idempotência."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "migrate_vis_params.py"
PALETTE = ["#a52a2a", "#006837"]

VISPARAMS = {
    "tvi-ndvi": {
        "display_name": "NDVI",
        "select": (["B8", "B4"], ["NIR", "RED"]),
        "visparam": {
            "bands": ["NIR", "RED"], "min": "-0.2", "max": "0.9", "palette": PALETTE,
            "index": {"type": "normalized_difference", "bands": ["NIR", "RED"], "name": "NDVI"},
        },
    },
    "tvi-rgb": {
        "select": ["B4", "B3", "B2"],
        "visparam": {"min": "200, 300, 700", "max": "3000, 2500, 2300", "bands": ["B4", "B3", "B2"], "gamma": "1.35"},
    },
    "landsat-tvi-ndvi": {
        "display_name": "NDVI",
        "visparam": {
            "LANDSAT/LC08/C02/T1_L2": {
                "bands": ["SR_B5", "SR_B4"], "min": [-0.2], "max": [0.9], "palette": PALETTE,
                "index": {"type": "normalized_difference", "bands": ["SR_B5", "SR_B4"], "name": "NDVI"},
            },
        },
    },
}


def _load_script():
    spec = importlib.util.spec_from_file_location("migrate_vis_params", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, upserted_id):
        self.upserted_id = upserted_id


class _Collection:
    def __init__(self):
        self.docs = {}

    async def update_one(self, flt, update, upsert=False):
        _id = flt["_id"]
        if _id in self.docs:
            return _Result(None)
        self.docs[_id] = dict(update["$setOnInsert"])
        return _Result(_id)


def test_build_documents_usa_display_name_explicito_e_propaga_indice():
    script = _load_script()
    docs = {d["_id"]: d for d in script.build_documents(VISPARAMS)}
    assert docs["tvi-ndvi"]["display_name"] == "NDVI"
    assert docs["tvi-ndvi"]["category"] == "sentinel2"
    assert docs["tvi-ndvi"]["band_config"] == {"original_bands": ["B8", "B4"], "mapped_bands": ["NIR", "RED"]}
    assert docs["tvi-ndvi"]["vis_params"]["index"]["name"] == "NDVI"
    assert docs["tvi-ndvi"]["vis_params"]["palette"] == PALETTE
    assert docs["tvi-ndvi"]["vis_params"]["min"] == [-0.2]
    assert docs["tvi-rgb"]["display_name"] == "Tvi Rgb"
    assert docs["landsat-tvi-ndvi"]["category"] == "landsat"
    assert docs["landsat-tvi-ndvi"]["display_name"] == "NDVI"
    assert docs["landsat-tvi-ndvi"]["satellite_configs"][0]["vis_params"]["index"]["bands"] == ["SR_B5", "SR_B4"]


@pytest.mark.asyncio
async def test_upsert_missing_nao_sobrescreve_existentes():
    script = _load_script()
    collection = _Collection()
    docs = script.build_documents(VISPARAMS)
    assert await script.upsert_missing(collection, docs) == (3, 0)
    collection.docs["tvi-ndvi"]["display_name"] = "NDVI ajustado em produção"
    assert await script.upsert_missing(collection, docs) == (0, 3)
    assert collection.docs["tvi-ndvi"]["display_name"] == "NDVI ajustado em produção"
