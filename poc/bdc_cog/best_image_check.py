import json
from pathlib import Path
from urllib.parse import urlencode

import numpy as np

from .catalog import gee_landsat_id, landsat_items, period_dates, search, tile_region
from .compare import decode, metrics
from .transport import Transport, atomic_write


def main():
    root = Path(__file__).parent
    t = Transport(root / "cache/http")
    x, y, z = 5943, 8963, 14
    _, bbox = tile_region(x, y, z)
    start, end = period_dates("MONTH", 2024, 8)
    items = search(t, "landsat-2", bbox, start, end)
    candidates = landsat_items(items, 2024, "MOSAIC", None)
    candidates = [
        i for i in candidates if 0 <= i["properties"].get("eo:cloud_cover", 101) < 40
    ]
    candidates.sort(key=lambda i: i["properties"]["eo:cloud_cover"], reverse=True)
    url = (
        f"https://tiles.lapig.iesa.ufg.br/api/layers/landsat/{x}/{y}/{z}?"
        + urlencode(
            dict(
                period="MONTH",
                year=2024,
                month=8,
                visparam="landsat-tvi-true",
                compositeMode="BEST_IMAGE",
            )
        )
    )
    ref = decode(t.request(url)[1])
    results = []
    for item in candidates:
        image_id = gee_landsat_id(item)
        url = (
            f"https://tiles.lapig.iesa.ufg.br/api/imagery/landsat/{x}/{y}/{z}?"
            + urlencode(dict(imageId=image_id, visparam="landsat-tvi-true"))
        )
        scene = decode(t.request(url)[1])
        result = {
            "id": image_id,
            "cloud_cover": item["properties"]["eo:cloud_cover"],
            "identical_rgba": bool(np.array_equal(ref, scene)),
            "metrics": metrics(ref, scene),
        }
        results.append(result)
        atomic_write(
            root / "evidence/best-image-check.json",
            json.dumps(results, indent=2).encode(),
        )
        print(
            result["id"],
            result["cloud_cover"],
            result["identical_rgba"],
            result["metrics"]["mae"],
            flush=True,
        )


if __name__ == "__main__":
    main()
