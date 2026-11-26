import json
from pathlib import Path

from .catalog import tile_at
from .render import render_cached, render_items, png_bytes
from .transport import Transport, atomic_write
import time


def main():
    root = Path(__file__).parent
    item = json.loads((root / "evidence/pilot-item.json").read_text())
    transport = Transport(root / "cache/http")
    results = []
    for z in [6, 7, 8, 9, 10, 12, 14]:
        x, y = tile_at(-49.4, -16.7, z)
        data, cold = render_cached(
            transport,
            root / "cache/benchmark-png",
            [item],
            "landsat-2",
            "landsat-tvi-true",
            x,
            y,
            z,
        )
        before = len(transport.events)
        start = time.monotonic()
        repeated = png_bytes(
            render_items(transport, [item], "landsat-2", "landsat-tvi-true", x, y, z)
        )
        events = transport.events[before:]
        blocks = {
            "seconds": time.monotonic() - start,
            "remote_ranges": sum(
                bool(e.get("range")) and not e["cache"] for e in events
            ),
            "identical_png": data == repeated,
        }
        _, warm = render_cached(
            transport,
            root / "cache/benchmark-png",
            [item],
            "landsat-2",
            "landsat-tvi-true",
            x,
            y,
            z,
        )
        results.append(dict(z=z, first=cold, persisted_blocks=blocks, png=warm))
        atomic_write(
            root / "evidence/benchmark.json", json.dumps(results, indent=2).encode()
        )
        print(
            z,
            cold["seconds"],
            cold["range_requests"],
            blocks["seconds"],
            warm["seconds"],
            flush=True,
        )


if __name__ == "__main__":
    main()
