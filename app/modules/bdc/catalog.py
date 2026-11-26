import calendar
import math
import re
from datetime import date
from urllib.parse import urlencode

import geohash

STAC = "https://data.inpe.br/bdc/stac/v1"


def period_dates(period, year, month=1):
    if period == "WET":
        return date(year, 1, 1).isoformat(), date(year, 4, 30).isoformat()
    if period == "DRY":
        return date(year, 6, 1).isoformat(), date(year, 10, 30).isoformat()
    if period == "MONTH":
        return date(year, month, 1).isoformat(), date(
            year, month, calendar.monthrange(year, month)[1]
        ).isoformat()
    raise ValueError("Período inválido")


def sensor(year):
    if year < 1985:
        raise ValueError("Ano anterior a 1985")
    return (
        "LT05"
        if year <= 2011
        else "LE07"
        if year <= 2013
        else "LC08"
        if year <= 2024
        else "LC09"
    )


def tile_at(lon, lat, z):
    return int((lon + 180) / 360 * 2**z), int(
        (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 2**z
    )


def tile_region(x, y, z):
    if not 6 <= z <= 18 or not 0 <= x < 2**z or not 0 <= y < 2**z:
        raise ValueError("Tile fora dos limites")
    lon = x / 2**z * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / 2**z))))
    key = geohash.encode(lat, lon, precision=3)
    box = geohash.bbox(key)
    return key, [box[k] for k in ("w", "s", "e", "n")]


def search(transport, collection, bbox, start, end, max_pages=100):
    found = {}
    for page in range(1, max_pages + 1):
        query = urlencode(
            {
                "collections": collection,
                "bbox": ",".join(map(str, bbox)),
                "datetime": f"{start}T00:00:00Z/{end}T00:00:00Z",
                "limit": 100,
                "page": page,
            }
        )
        result = transport.json(f"{STAC}/search?{query}")
        features = result.get("features", [])
        if not features:
            return list(found.values())
        fresh = [v for v in features if v["id"] not in found]
        if not fresh:
            raise RuntimeError("Paginação não avançou; seleção incompleta recusada")
        for item in fresh:
            dt = item["properties"].get("datetime") or item["properties"].get(
                "start_datetime"
            )
            if dt and start <= dt[:10] < end:
                found[item["id"]] = item
        if (
            not any(v["rel"] == "next" for v in result.get("links", []))
            and len(features) < 100
        ):
            return list(found.values())
    raise RuntimeError("Limite de páginas atingido; seleção incompleta recusada")


def landsat_items(items, year, mode, policy):
    pattern = re.compile(rf"^{sensor(year)}_L2S[PR]_\d{{6}}_\d{{8}}_\d{{8}}_02_T1$")
    selected = [i for i in items if pattern.fullmatch(i["id"])]
    if mode == "BEST_IMAGE":
        if policy not in {"legacy-desc", "least-cloud"}:
            raise ValueError("BEST_IMAGE exige política explícita")
        selected = [
            i for i in selected if 0 <= i["properties"].get("eo:cloud_cover", 101) < 40
        ]
        selected.sort(
            key=lambda i: (i["properties"]["eo:cloud_cover"], i["id"]),
            reverse=policy == "legacy-desc",
        )
        return selected[:1]
    if mode != "MOSAIC":
        raise ValueError("Composição inválida")
    return sorted(selected, key=lambda i: (gee_landsat_id(i), i["id"]))


def gee_landsat_id(item):
    parts = item["id"].split("_")
    if len(parts) != 7 or parts[-1] != "T1":
        raise ValueError("Cena Landsat Tier 1 inválida")
    return f"LANDSAT/{parts[0]}/C02/T1_L2/{parts[0]}_{parts[2]}_{parts[3]}"
