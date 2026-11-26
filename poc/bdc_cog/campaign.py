import argparse
import json
from pathlib import Path
from urllib.parse import urlencode

from .catalog import gee_landsat_id, landsat_items, period_dates, search, tile_at
from .compare import board, decode, metrics
from .render import VIS, render_cached, select_layer, intersects_tile
from .transport import ServicePaused, Transport, atomic_write, digest

ROOT = Path(__file__).parent
REGIONS = [("cerrado-goias", -49.4, -16.7), ("amazonia-para", -54.9, -3.1)]


def manifest():
    rows = []
    for region, lon, lat in REGIONS:
        for year in [2010, 2013, 2020, 2025]:
            for period in ["MONTH", "WET", "DRY"]:
                for z in [14, 12, 10, 8]:
                    x, y = tile_at(lon, lat, z)
                    for vis in [k for k in VIS if k.startswith("landsat")]:
                        rows.append(
                            dict(
                                region=region,
                                lon=lon,
                                lat=lat,
                                source="landsat-2",
                                layer="landsat",
                                year=year,
                                month=8,
                                period=period,
                                z=z,
                                x=x,
                                y=y,
                                visparam=vis,
                            )
                        )
        for year in [2020, 2024]:
            for period in ["MONTH", "WET", "DRY"]:
                for z in [14, 12, 10, 8]:
                    x, y = tile_at(lon, lat, z)
                    for vis in [k for k in VIS if k.startswith("tvi-")]:
                        rows.append(
                            dict(
                                region=region,
                                lon=lon,
                                lat=lat,
                                source="S2_L2A-1",
                                layer="s2_harmonized",
                                year=year,
                                month=8,
                                period=period,
                                z=z,
                                x=x,
                                y=y,
                                visparam=vis,
                            )
                        )
    return sorted(rows, key=lambda row: digest(row))


def reference(transport, row, image_id=None):
    layer = row["layer"]
    x, y, z = (row[k] for k in ["x", "y", "z"])
    if image_id:
        url = (
            f"https://tiles.lapig.iesa.ufg.br/api/imagery/{layer}/{x}/{y}/{z}?"
            + urlencode({"imageId": image_id, "visparam": row["visparam"]})
        )
    else:
        params = {k: row[k] for k in ["period", "year", "month", "visparam"]}
        if layer == "landsat":
            params["compositeMode"] = "MOSAIC"
        url = (
            f"https://tiles.lapig.iesa.ufg.br/api/layers/{layer}/{x}/{y}/{z}?"
            + urlencode(params)
        )
    meta, data = transport.request(url)
    if any(k.lower() in {"x-tile-error", "x-error-reason"} for k in meta["headers"]):
        raise ValueError("Referência contém indicação de erro")
    return decode(data), url


def choose_scene(t, row):
    start, end = period_dates(row["period"], row["year"], row["month"])
    items = search(
        t,
        row["source"],
        [row["lon"] - 0.01, row["lat"] - 0.01, row["lon"] + 0.01, row["lat"] + 0.01],
        start,
        end,
    )
    if row["source"] == "landsat-2":
        selected = landsat_items(items, row["year"], "BEST_IMAGE", "least-cloud")
        return (selected[0], gee_landsat_id(selected[0])) if selected else (None, None)
    items.sort(key=lambda i: (i["properties"].get("eo:cloud_cover", 100), i["id"]))
    if not items:
        return None, None
    query = urlencode(
        dict(
            lat=row["lat"],
            lon=row["lon"],
            start=start,
            end=end,
            limit=200,
            sort="date_asc",
            maxCloud=100,
        )
    )
    catalog = t.json(
        f"https://tiles.lapig.iesa.ufg.br/api/imagery/s2_harmonized/catalog?{query}"
    )
    for item in items:
        acquisition = item["id"].split("_")[2]
        mgrs = item["id"].split("_")[5]
        match = next(
            (
                i
                for i in catalog["items"]
                if acquisition in i["id"] and i["id"].endswith(mgrs)
            ),
            None,
        )
        if match:
            return item, match["id"]
    return None, None


def run(limit, stage, source=None):
    t = Transport(ROOT / "cache/http")
    out = ROOT / "evidence/campaign"
    out.mkdir(parents=True, exist_ok=True)
    rows = manifest()
    atomic_write(ROOT / "evidence/manifest.json", json.dumps(rows, indent=2).encode())
    completed = 0
    for row in rows:
        if source and row["source"] != source:
            continue
        if stage == "scene" and row["visparam"].endswith("ndvi"):
            continue
        key = digest([row, stage])
        record = out / (key + ".json")
        if record.exists():
            continue
        if completed >= limit:
            break
        result = dict(row, stage=stage)
        try:
            if stage == "scene":
                item, image_id = choose_scene(t, row)
                if item is None:
                    result["status"] = "sem aquisição correspondente"
                    raise ValueError(result["status"])
                items = [item]
                mode = "SCENE"
            else:
                items = select_layer(
                    t,
                    row["source"],
                    row["x"],
                    row["y"],
                    row["z"],
                    row["period"],
                    row["year"],
                    row["month"],
                    "MOSAIC",
                    None,
                )
                image_id = None
                mode = "MOSAIC"
            if not items:
                raise ValueError("Sem cenas BDC")
            reads = sum(intersects_tile(i, row["x"], row["y"], row["z"]) for i in items)
            result["intersecting_scenes"] = reads
            if reads > 64:
                raise ValueError(
                    f"Orçamento de 64 cenas intersectantes excedido: {reads}; sem truncamento"
                )
            result["items"] = [i["id"] for i in items]
            ref, url = reference(t, row, image_id)
            result["reference_url"] = url
            data, stats = render_cached(
                t,
                ROOT / "cache/png",
                items,
                row["source"],
                row["visparam"],
                row["x"],
                row["y"],
                row["z"],
                mode,
                0 if row["source"].startswith("S2") else None,
            )
            candidate = decode(data)
            result.update(
                status="medido", metrics=metrics(ref, candidate), performance=stats
            )
            _, warm = render_cached(
                t,
                ROOT / "cache/png",
                items,
                row["source"],
                row["visparam"],
                row["x"],
                row["y"],
                row["z"],
                mode,
                0 if row["source"].startswith("S2") else None,
            )
            result["warm_png"] = warm
            board(
                ref,
                candidate,
                out / (key + ".png"),
                f"{row['region']} / {row['year']} / {row['period']} / z{row['z']} / {row['visparam']}",
            )
        except ServicePaused as exc:
            atomic_write(
                ROOT / f"evidence/campaign-paused-{stage}.json",
                json.dumps(dict(row, stage=stage, error=str(exc)), indent=2).encode(),
            )
            print(str(exc), flush=True)
            return 2
        except Exception as exc:
            result.update(status="não medido", error=str(exc))
            if list((ROOT / "cache/http").glob("*.paused")):
                atomic_write(record, json.dumps(result, indent=2).encode())
                print(str(exc), flush=True)
                return 2
        atomic_write(record, json.dumps(result, ensure_ascii=False, indent=2).encode())
        completed += 1
        print(
            row["source"],
            row["region"],
            row["year"],
            row["period"],
            row["z"],
            row["visparam"],
            result["status"],
            result.get("error", ""),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--stage", choices=["scene", "layer"], default="scene")
    parser.add_argument("--source")
    args = parser.parse_args()
    raise SystemExit(run(args.limit, args.stage, args.source))
