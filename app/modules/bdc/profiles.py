import copy
import json
import re
from pathlib import Path

from .visualization import VIS

LABELS = {
    "tvi-green": "Vegetação e uso do solo",
    "tvi-red": "Infravermelho e vegetação",
    "tvi-rgb": "Cor natural",
    "tvi-ndvi": "NDVI",
    "landsat-tvi-true": "Cor natural",
    "landsat-tvi-agri": "Agricultura",
    "landsat-tvi-false": "Falsa cor",
    "landsat-tvi-ndvi": "NDVI",
}


def family(collection):
    name = collection["id"].lower()
    if name.startswith("mosaic"):
        return "Mosaicos"
    if collection.get("bdc:type") == "cube":
        return "Cubos"
    if "landsat" in name:
        return "Landsat"
    if name.startswith("s2") or "sentinel-2" in name:
        return "Sentinel-2"
    if name.startswith(("cb", "amz")):
        return "CBERS e Amazonia"
    if "sentinel-1" in name:
        return "Radar"
    return "Outros produtos"


def profiles(collection, item=None):
    source = collection["id"]
    assets = item.get("assets", {}) if item is not None else collection.get('item_assets', {})
    result = []
    if source in {"landsat-2", "LANDSAT-16D-1", "S2_L2A-1", "S2-16D-2"}:
        landsat = source.startswith(("landsat", "LANDSAT"))
        for name, (bands, minimum, maximum, gamma) in VIS.items():
            if name.startswith("landsat-") != landsat or name not in LABELS:
                continue
            if item is not None and not all(b in assets for b in bands):
                continue
            offset = 0
            if source == "S2_L2A-1" and item is None:
                offset = None
            elif source == "S2_L2A-1":
                match = re.search(r"_N(\d{4})_", item["id"])
                if not match:
                    continue
                offset = -1000 if int(match[1]) >= 400 else 0
            result.append(
                dict(
                    id=name,
                    label=LABELS[name],
                    bands=bands,
                    min=minimum,
                    max=maximum,
                    gamma=gamma,
                    scale=0.0000275
                    if source == "landsat-2"
                    else 0.0001
                    if source == "LANDSAT-16D-1"
                    else 1,
                    offset=-0.2 if source == "landsat-2" else offset,
                    ndvi=name.endswith("ndvi"),
                    fidelity="B"
                    if source == "LANDSAT-16D-1"
                    else "A candidato"
                    if landsat
                    else "C",
                    note="Mesmos limites do TVI; equivalência visual ainda em avaliação."
                    if landsat
                    else "BOA: aparência diferente do TOA servido pelo TVI. Offset explícito por produto/baseline.",
                )
            )
        proposed_path = Path(__file__).parent / "proposed-visparams.json"
        if not landsat and proposed_path.exists():
            for name, value in json.loads(proposed_path.read_text()).items():
                base_name = name.removesuffix("-bdc-c-proposta")
                original = next((p for p in result if p["id"] == base_name), None)
                if original:
                    proposal = copy.deepcopy(original)
                    proposal.update(
                        id=name,
                        label=original["label"] + " (calibrado)",
                        min=value["min"],
                        max=value["max"],
                        note="Proposta experimental calibrada em Goiás e Pará. Não homologada para outras áreas.",
                    )
                    result.append(proposal)
        return result
    if source == "CB4A-WPM-PCA-FUSED-1":
        names = [name for name in ("rgb", "RGB", "tci") if name in assets]
        if len(names) == 1:
            return [
                dict(
                    id="catalog-rgb",
                    label="RGB fusionado",
                    bands=names,
                    min=[0] * 3,
                    max=[255] * 3,
                    gamma=1,
                    scale=1,
                    offset=0,
                    ndvi=False,
                    fidelity="D",
                    note="RGB fusionado pelo provedor; não equivale às composições multiespectrais do TVI.",
                )
            ]
    renders = collection.get("properties", {}).get("renders", {})
    for name, spec in renders.items():
        bands = spec.get("assets", [])
        ranges = spec.get("rescale", [])
        if len(bands) != 3 or len(ranges) != 3:
            continue
        if not all(b in assets and "tiff" in assets[b].get("type", "") for b in bands):
            continue
        if not all(len(pair) == 2 and pair[0] < pair[1] for pair in ranges):
            continue
        result.append(
            dict(
                id="catalog-" + name,
                label=spec.get("title", name),
                bands=bands,
                min=[r[0] for r in ranges],
                max=[r[1] for r in ranges],
                gamma=1,
                scale=1,
                offset=0,
                ndvi=False,
                fidelity="D",
                note="Composição e limites declarados no catálogo BDC.",
            )
        )
    return result


def collection_summary(collection):
    assets = collection.get("item_assets", {})
    return dict(
        id=collection["id"],
        title=collection.get("title", collection["id"]),
        description=collection.get("description", ""),
        family=family(collection),
        license=collection.get("license", "Não declarada"),
        extent=collection.get("extent", {}),
        summaries=collection.get("summaries", {}),
        providers=collection.get("providers", []),
        bands=collection.get("properties", {}).get("eo:bands", []),
        formats=sorted({a.get("type", "Não declarado") for a in assets.values()}),
        has_cog=any("tiff" in a.get("type", "") for a in assets.values()),
        public=collection.get("bdc:public", None),
        profiles=profiles(collection),
    )
