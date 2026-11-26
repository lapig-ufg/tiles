import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

from .render import GDAL_OPTIONS
from .transport import RangeBridge, Transport, atomic_write


def main():
    root = Path(__file__).parent
    t = Transport(root / "cache/http")
    results = []
    for source, band in [
        ("landsat-2", "red"),
        ("S2_L2A-1", "B04"),
        ("LANDSAT-16D-1", "red"),
        ("S2-16D-2", "B04"),
    ]:
        item = json.loads(
            (root / "evidence" / f"goias-2024-08-{source}.json").read_text()
        )[0]
        with RangeBridge(t) as bridge, rasterio.Env(**GDAL_OPTIONS):
            url = bridge.url(item["assets"][band]["href"])
            with rasterio.open(url) as src:
                col = (src.width // 2 // 2) * 2
                row = (src.height // 2 // 2) * 2
                window = Window(col, row, 512, 512)
                base = src.read(1, window=window).astype("float64")
                overview = src.read(1, window=window, out_shape=(256, 256)).astype(
                    "float64"
                )
                nodata = src.nodata if src.nodata is not None else 0
                blocks = base.reshape(256, 2, 256, 2)
                valid = (blocks != nodata).all(axis=(1, 3))
                mean = blocks.mean(axis=(1, 3))
                nearest = base[::2, ::2]
                results.append(
                    {
                        "source": source,
                        "item": item["id"],
                        "valid": int(valid.sum()),
                        "mae_average": float(
                            np.abs(overview[valid] - mean[valid]).mean()
                        )
                        if valid.any()
                        else None,
                        "mae_nearest": float(
                            np.abs(overview[valid] - nearest[valid]).mean()
                        )
                        if valid.any()
                        else None,
                        "max_average": float(
                            np.abs(overview[valid] - mean[valid]).max()
                        )
                        if valid.any()
                        else None,
                    }
                )
        atomic_write(
            root / "evidence/overview-check.json",
            json.dumps(results, indent=2).encode(),
        )
        print(results[-1], flush=True)


if __name__ == "__main__":
    main()
