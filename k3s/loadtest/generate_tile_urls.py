#!/usr/bin/env python3
"""
Gera URLs de tiles a partir de pontos de um arquivo GeoJSON.

Converte coordenadas lat/lon para coordenadas de tile XYZ em múltiplos
níveis de zoom, produzindo um arquivo JSON consumível pelo loadtest.py.

Uso:
    python3 generate_tile_urls.py \
        --geojson-path /path/to/mapbiomas_pastagem_col11.geojson \
        --sample-size 1000 \
        --zoom-levels 10,11,12 \
        --years 2022,2023,2024 \
        --output tile_urls.json
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path


def latlon_to_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """Converte coordenadas geográficas para coordenadas de tile XYZ."""
    lat_rad = math.radians(lat)
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def load_geojson_points(path: str) -> list[dict]:
    """Carrega pontos de um arquivo GeoJSON, retornando lista de {lat, lon, id}."""
    with open(path, "r") as f:
        data = json.load(f)

    points = []
    for feature in data.get("features", []):
        geom = feature.get("geometry", {})
        if geom.get("type") != "Point":
            continue

        coords = geom.get("coordinates", [])
        if len(coords) < 2:
            continue

        lon, lat = coords[0], coords[1]
        props = feature.get("properties", {})
        points.append({
            "lat": lat,
            "lon": lon,
            "id": props.get("id", len(points) + 1),
        })

    return points


def sample_points(points: list[dict], sample_size: int) -> list[dict]:
    """Amostra pontos com distribuição espacial."""
    if sample_size >= len(points):
        return points

    # Amostragem estratificada: divide em grid e amostra de cada célula
    lat_min = min(p["lat"] for p in points)
    lat_max = max(p["lat"] for p in points)
    lon_min = min(p["lon"] for p in points)
    lon_max = max(p["lon"] for p in points)

    # Grid de ~sqrt(sample_size) células
    grid_size = max(int(math.sqrt(sample_size)), 5)
    lat_step = (lat_max - lat_min) / grid_size if lat_max != lat_min else 1
    lon_step = (lon_max - lon_min) / grid_size if lon_max != lon_min else 1

    # Distribui pontos nas células
    cells: dict[tuple[int, int], list[dict]] = {}
    for p in points:
        cell_y = min(int((p["lat"] - lat_min) / lat_step), grid_size - 1)
        cell_x = min(int((p["lon"] - lon_min) / lon_step), grid_size - 1)
        cells.setdefault((cell_x, cell_y), []).append(p)

    # Amostra proporcional de cada célula
    sampled = []
    points_per_cell = max(sample_size // len(cells), 1)
    for cell_points in cells.values():
        n = min(points_per_cell, len(cell_points))
        sampled.extend(random.sample(cell_points, n))

    # Complementa se necessário
    if len(sampled) < sample_size:
        remaining = [p for p in points if p not in sampled]
        extra = min(sample_size - len(sampled), len(remaining))
        sampled.extend(random.sample(remaining, extra))

    return sampled[:sample_size]


def generate_tile_entries(
    points: list[dict],
    zoom_levels: list[int],
    years: list[int],
) -> list[dict]:
    """Gera entradas de tile únicas a partir dos pontos."""
    tiles_set = set()
    entries = []

    for point in points:
        for zoom in zoom_levels:
            x, y = latlon_to_tile(point["lat"], point["lon"], zoom)
            tile_key = (x, y, zoom)

            if tile_key in tiles_set:
                continue
            tiles_set.add(tile_key)

            for year in years:
                # Landsat — período mensal
                month = random.randint(1, 12)
                entries.append({
                    "x": x,
                    "y": y,
                    "z": zoom,
                    "endpoint": "landsat",
                    "params": {
                        "period": "MONTH",
                        "year": year,
                        "month": month,
                        "visparam": "landsat-tvi-false",
                        "compositeMode": "BEST_IMAGE",
                    },
                    "source_point": {
                        "lat": point["lat"],
                        "lon": point["lon"],
                        "id": point["id"],
                    },
                })

                # S2 Harmonized — período úmido
                entries.append({
                    "x": x,
                    "y": y,
                    "z": zoom,
                    "endpoint": "s2_harmonized",
                    "params": {
                        "period": "WET",
                        "year": year,
                        "visparam": "tvi-red",
                    },
                    "source_point": {
                        "lat": point["lat"],
                        "lon": point["lon"],
                        "id": point["id"],
                    },
                })

    return entries


def build_urls(entries: list[dict], base_url: str = "") -> list[dict]:
    """Adiciona URL formatada a cada entrada."""
    for entry in entries:
        x, y, z = entry["x"], entry["y"], entry["z"]
        ep = entry["endpoint"]
        params = entry["params"]

        query_parts = [f"{k}={v}" for k, v in params.items()]
        query = "&".join(query_parts)
        entry["url"] = f"{base_url}/api/layers/{ep}/{x}/{y}/{z}?{query}"

    return entries


def main():
    parser = argparse.ArgumentParser(
        description="Gera URLs de tiles a partir de pontos GeoJSON"
    )
    parser.add_argument(
        "--geojson-path",
        required=True,
        help="Caminho para o arquivo GeoJSON de entrada",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=1000,
        help="Número de pontos a amostrar (padrão: 1000)",
    )
    parser.add_argument(
        "--zoom-levels",
        default="10,11,12",
        help="Níveis de zoom separados por vírgula (padrão: 10,11,12)",
    )
    parser.add_argument(
        "--years",
        default="2022,2023,2024",
        help="Anos separados por vírgula (padrão: 2022,2023,2024)",
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="URL base do servidor (ex: http://localhost:8083)",
    )
    parser.add_argument(
        "--output",
        default="tile_urls.json",
        help="Caminho do arquivo JSON de saída (padrão: tile_urls.json)",
    )

    args = parser.parse_args()

    zoom_levels = [int(z) for z in args.zoom_levels.split(",")]
    years = [int(y) for y in args.years.split(",")]

    # Carrega pontos
    print(f"Carregando GeoJSON: {args.geojson_path}")
    points = load_geojson_points(args.geojson_path)
    print(f"  Total de pontos: {len(points)}")

    if not points:
        print("ERRO: Nenhum ponto encontrado no GeoJSON", file=sys.stderr)
        sys.exit(1)

    # Amostragem
    sampled = sample_points(points, args.sample_size)
    print(f"  Pontos amostrados: {len(sampled)}")

    # Cobertura geográfica
    lat_min = min(p["lat"] for p in sampled)
    lat_max = max(p["lat"] for p in sampled)
    lon_min = min(p["lon"] for p in sampled)
    lon_max = max(p["lon"] for p in sampled)
    print(f"  Cobertura: lat [{lat_min:.2f}, {lat_max:.2f}], lon [{lon_min:.2f}, {lon_max:.2f}]")

    # Gera tiles
    entries = generate_tile_entries(sampled, zoom_levels, years)
    print(f"  Tiles gerados: {len(entries)}")

    # Adiciona URLs
    entries = build_urls(entries, args.base_url)

    # Estatísticas por zoom e endpoint
    stats = {}
    for e in entries:
        key = f"z{e['z']}-{e['endpoint']}"
        stats[key] = stats.get(key, 0) + 1
    print("  Distribuição:")
    for key, count in sorted(stats.items()):
        print(f"    {key}: {count}")

    # Salva
    output = {
        "metadata": {
            "geojson_path": args.geojson_path,
            "total_points": len(points),
            "sampled_points": len(sampled),
            "zoom_levels": zoom_levels,
            "years": years,
            "total_tile_urls": len(entries),
            "coverage": {
                "lat_min": lat_min,
                "lat_max": lat_max,
                "lon_min": lon_min,
                "lon_max": lon_max,
            },
        },
        "tiles": entries,
    }

    output_path = Path(args.output)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nSalvo em: {output_path} ({output_path.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
