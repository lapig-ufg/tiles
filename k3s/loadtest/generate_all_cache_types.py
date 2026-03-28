#!/usr/bin/env python3
"""
Gera URLs de tiles cobrindo TODOS os tipos de cache:
- Layers: landsat, s2_harmonized
- Períodos: WET, DRY, MONTH
- Visparams: todos disponíveis por layer
- Anos: recentes (2022-2024)

Usa pontos do GeoJSON como base geográfica.
"""
import json
import math
import random
import sys


def latlon_to_tile(lat, lon, zoom):
    lat_rad = math.radians(lat)
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def main():
    geojson_path = sys.argv[1] if len(sys.argv) > 1 else "/home/tharles/Downloads/dados-gis/geojson/mapbiomas_pastagem_col11.geojson"
    sample_size = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    output = sys.argv[3] if len(sys.argv) > 3 else "/tmp/tiles-all-cache-types.json"

    # Carregar e amostrar pontos
    with open(geojson_path) as f:
        data = json.load(f)

    points = []
    for feat in data.get("features", []):
        geom = feat.get("geometry", {})
        if geom.get("type") == "Point":
            coords = geom["coordinates"]
            points.append({"lat": coords[1], "lon": coords[0]})

    sampled = random.sample(points, min(sample_size, len(points)))
    print(f"Pontos amostrados: {len(sampled)} de {len(points)}")

    # Configurações de cache
    configs = [
        # Landsat
        {"endpoint": "landsat", "visparam": "landsat-tvi-false", "compositeMode": "BEST_IMAGE"},
        {"endpoint": "landsat", "visparam": "landsat-tvi-true", "compositeMode": "BEST_IMAGE"},
        {"endpoint": "landsat", "visparam": "landsat-tvi-agri", "compositeMode": "MOSAIC"},
        # S2 Harmonized
        {"endpoint": "s2_harmonized", "visparam": "tvi-red"},
        {"endpoint": "s2_harmonized", "visparam": "tvi-green"},
        {"endpoint": "s2_harmonized", "visparam": "tvi-rgb"},
    ]

    periods_by_layer = {
        "landsat": [
            {"period": "MONTH", "months": [3, 6, 8, 11]},
            {"period": "WET"},
            {"period": "DRY"},
        ],
        "s2_harmonized": [
            {"period": "WET"},
            {"period": "DRY"},
            {"period": "MONTH", "months": [3, 8]},
        ],
    }

    years = [2022, 2023, 2024]
    zooms = [10, 11, 12]

    entries = []
    seen = set()

    for point in sampled:
        for zoom in zooms:
            x, y = latlon_to_tile(point["lat"], point["lon"], zoom)

            for config in configs:
                layer = config["endpoint"]

                for year in years:
                    for period_cfg in periods_by_layer[layer]:
                        period = period_cfg["period"]
                        months = period_cfg.get("months", [1])

                        for month in months:
                            key = (x, y, zoom, layer, config["visparam"], period, year, month)
                            if key in seen:
                                continue
                            seen.add(key)

                            params = {
                                "period": period,
                                "year": year,
                                "month": month,
                                "visparam": config["visparam"],
                            }
                            if "compositeMode" in config:
                                params["compositeMode"] = config["compositeMode"]

                            entries.append({
                                "x": x, "y": y, "z": zoom,
                                "endpoint": layer,
                                "params": params,
                            })

    # Estatísticas
    stats = {}
    for e in entries:
        key = f"{e['endpoint']}/{e['params']['visparam']}/{e['params']['period']}"
        stats[key] = stats.get(key, 0) + 1

    print(f"\nTotal de tiles: {len(entries)}")
    print("\nDistribuição por tipo:")
    for key, count in sorted(stats.items()):
        print(f"  {key}: {count}")

    result = {
        "metadata": {
            "total_tile_urls": len(entries),
            "sample_points": len(sampled),
            "configs": len(configs),
            "years": years,
            "zooms": zooms,
        },
        "tiles": entries,
    }

    with open(output, "w") as f:
        json.dump(result, f)

    print(f"\nSalvo em: {output}")


if __name__ == "__main__":
    main()
