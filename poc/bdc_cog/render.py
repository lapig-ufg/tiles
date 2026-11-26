import io
import time
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rio_tiler.io import Reader
from rio_tiler.errors import TileOutsideBounds
from morecantile import tms

from .catalog import landsat_items, period_dates, search, tile_region
from .transport import RangeBridge, atomic_write, digest, file_lock

from app.modules.bdc.visualization import GDAL_OPTIONS, PALETTE, VIS, visualize


def png_bytes(rgba):
    output = io.BytesIO()
    Image.fromarray(rgba).save(output, format="PNG")
    return output.getvalue()


def inspect_header(transport, url):
    start = len(transport.events)
    started = time.monotonic()
    with RangeBridge(transport) as bridge, rasterio.Env(**GDAL_OPTIONS):
        with rasterio.open(bridge.url(url)) as src:
            result = {
                "url": url,
                "crs": str(src.crs),
                "transform": list(src.transform),
                "dtype": src.dtypes,
                "nodata": src.nodata,
                "compression": str(src.compression),
                "block_shapes": src.block_shapes,
                "overviews": src.overviews(1),
                "overview_resampling": src.tags(ns="rio_overview").get(
                    "resampling", "não declarado"
                ),
                "scales": src.scales,
                "offsets": src.offsets,
                "shape": src.shape,
                "count": src.count,
                "tags": src.tags(),
                "image_structure": src.tags(ns="IMAGE_STRUCTURE"),
            }
    result.update(seconds=time.monotonic() - started, requests=transport.events[start:])
    return result


def intersects_tile(item, x, y, z):
    if "bbox" not in item:
        return True
    west, south, east, north = tms.get("WebMercatorQuad").bounds(x, y, z)
    bounds = item["bbox"]
    return (
        bounds[0] < east
        and bounds[2] > west
        and bounds[1] < north
        and bounds[3] > south
    )


def render_items(
    transport, items, source, visparam, x, y, z, mode="SCENE", s2_offset=None
):
    tile_region(x, y, z)
    if mode not in {"SCENE", "BEST_IMAGE", "MOSAIC"}:
        raise ValueError("Composição inválida")
    if mode in {"SCENE", "BEST_IMAGE"} and len(items) > 1:
        raise ValueError("Modo de cena exige no máximo uma aquisição")
    bands, low, high, gamma = VIS[visparam]
    if source == "landsat-2" or source == "LANDSAT-16D-1":
        if not visparam.startswith("landsat-"):
            raise ValueError("Visparam incompatível com Landsat")
    elif source in {"S2_L2A-1", "S2-16D-2"}:
        if not visparam.startswith("tvi-") or s2_offset is None:
            raise ValueError("Sentinel exige visparam compatível e offset explícito")
    elif source != "CB4A-WPM-PCA-FUSED-1" or visparam != "bdc-cbers-rgb-proposta":
        raise ValueError("Fonte ou visparam não suportado")
    count = 3 if bands == ["rgb"] else len(bands)
    composed = np.zeros((count, 256, 256), dtype="float64")
    band_valid = np.zeros_like(composed, dtype=bool)
    with RangeBridge(transport) as bridge, rasterio.Env(**GDAL_OPTIONS):
        for item in items:
            if not intersects_tile(item, x, y, z):
                continue
            arrays, masks = [], []
            try:
                for band in bands:
                    if source == "CB4A-WPM-PCA-FUSED-1":
                        keys = [
                            name
                            for name in ("rgb", "RGB", "tci")
                            if name in item["assets"]
                        ]
                        if len(keys) != 1:
                            raise ValueError("Asset RGB ausente ou ambíguo")
                        asset = item["assets"][keys[0]]
                    else:
                        asset = item["assets"][band]
                    with Reader(bridge.url(asset["href"])) as reader:
                        declared = (
                            asset.get("raster:bands") or asset.get("eo:bands") or [{}]
                        )
                        nodata = declared[0].get("nodata", reader.dataset.nodata)
                        tile = reader.tile(
                            x,
                            y,
                            z,
                            tilesize=256,
                            resampling_method="nearest",
                            reproject_method="nearest",
                            nodata=nodata,
                        )
                        arrays.extend(tile.array.data)
                        masks.extend(~np.ma.getmaskarray(tile.array))
                data = np.array(arrays, dtype="float64")
                valid = np.array(masks, dtype=bool)
                if source == "landsat-2":
                    if mode == "MOSAIC":
                        with Reader(
                            bridge.url(item["assets"]["qa_pixel"]["href"])
                        ) as reader:
                            qa = reader.tile(x, y, z, tilesize=256).array
                            clear = (
                                (qa.data[0].astype("uint32") & ((1 << 3) | (1 << 4)))
                                == 0
                            ) & ~np.ma.getmaskarray(qa)[0]
                            valid &= clear
                    data = data * 0.0000275 - 0.2
                elif source == "LANDSAT-16D-1":
                    data *= 0.0001
                elif source in {"S2_L2A-1", "S2-16D-2"}:
                    data += s2_offset
                composed[valid] = data[valid]
                band_valid |= valid
            except TileOutsideBounds:
                continue
        if bridge.errors:
            raise RuntimeError("; ".join(bridge.errors))
    return visualize(
        composed, band_valid.all(axis=0), low, high, gamma, visparam.endswith("ndvi")
    )


def render_cached(
    transport, root, items, source, visparam, x, y, z, mode="SCENE", s2_offset=None
):
    request = {
        "renderer": 2,
        "items": items,
        "source": source,
        "visparam": VIS[visparam],
        "index": visparam.endswith("ndvi"),
        "palette": PALETTE,
        "xyz": [x, y, z],
        "mode": mode,
        "s2_offset": s2_offset,
        "gdal": rasterio.__gdal_version__,
    }
    path = Path(root) / (digest(request) + ".png")
    started = time.monotonic()
    before = len(transport.events)
    with file_lock(path.with_suffix(".lock")):
        hit = path.exists()
        if hit:
            result = path.read_bytes()
        else:
            result = png_bytes(
                render_items(
                    transport, items, source, visparam, x, y, z, mode, s2_offset
                )
            )
            atomic_write(path, result)
    events = transport.events[before:]
    return result, {
        "seconds": time.monotonic() - started,
        "png_hit": hit,
        "png_bytes": len(result),
        "range_requests": sum(bool(e.get("range")) and not e["cache"] for e in events),
        "remote_bytes": sum(e.get("bytes", 0) for e in events if not e["cache"]),
        "http_cache_hits": sum(e["cache"] for e in events),
        "path": str(path),
    }


def select_layer(transport, source, x, y, z, period, year, month, mode, policy):
    _, bbox = tile_region(x, y, z)
    start, end = period_dates(period, year, month)
    items = search(transport, source, bbox, start, end)
    if source == "landsat-2":
        return landsat_items(items, year, mode, policy)
    if source == "S2_L2A-1":
        return sorted(
            items,
            key=lambda i: (i["properties"].get("eo:cloud_cover", 100), i["id"]),
            reverse=True,
        )
    return sorted(items, key=lambda i: i["id"])
