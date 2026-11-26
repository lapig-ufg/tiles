import json
from pathlib import Path

from .catalog import STAC
from .transport import Transport, ServicePaused, atomic_write


def classify(collection):
    name = collection["id"].lower()
    if name.startswith("mosaic-"):
        return "mosaico"
    if collection.get("bdc:type") == "cube" or any(
        s in name for s in ["-16d-", "-8d-", "-2m-"]
    ):
        return "cubo"
    if name.startswith("sentinel-1"):
        return "radar"
    if any(
        s in name
        for s in [
            "ocsmart",
            "topodata",
            "samet",
            "prec_merge",
            "etacc",
            "weather-radar",
            "wqp",
            "rrs",
            "pc-tiete",
            "mod11",
            "myd11",
            "goes",
        ]
    ):
        return "não-imagem óptica TVI: variável ambiental, meteorologia, água ou relevo"
    return "imagem óptica"


def inventory(root):
    root = Path(root)
    transport = Transport(root / "cache/http")
    snapshot = json.loads((root / "evidence/collections.json").read_text())["body"]
    output = root / "evidence/inventory.json"
    rows = json.loads(output.read_text()) if output.exists() else []
    for collection in snapshot["collections"]:
        if collection["id"] in {r["id"] for r in rows}:
            continue
        row = {
            "id": collection["id"],
            "category": classify(collection),
            "license": collection.get("license"),
            "extent": collection.get("extent"),
            "summaries": collection.get("summaries"),
            "public": collection.get("bdc:public"),
            "grid": collection.get("properties", {}).get("bdc:grs"),
            "bands": collection.get("properties", {}).get("eo:bands", []),
            "assets_declared": collection.get("item_assets", {}),
        }
        try:
            result = transport.json(
                f"{STAC}/collections/{collection['id']}/items?limit=1"
            )
            features = result.get("features", [])
            row["sample"] = features[0] if features else None
            if features:
                atomic_write(
                    root / "evidence/items" / (collection["id"] + ".json"),
                    json.dumps(features[0], indent=2).encode(),
                )
                row["formats"] = sorted(
                    {
                        a.get("type", "não declarado")
                        for a in features[0]["assets"].values()
                    }
                )
        except ServicePaused:
            raise
        except Exception as exc:
            row["error"] = str(exc)
        rows.append(row)
        atomic_write(output, json.dumps(rows, ensure_ascii=False, indent=2).encode())
        print(
            collection["id"], "erro" if row.get("error") else "registrada", flush=True
        )
    return rows


if __name__ == "__main__":
    inventory(Path(__file__).parent)
