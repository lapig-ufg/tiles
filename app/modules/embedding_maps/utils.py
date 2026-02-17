"""
Helpers: hash deterministico, validacao ROI, conversao GEE, cache keys.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

import ee

from .schemas import RoiConfig, RoiType


# --------------------------------------------------------------------------- #
# Job ID deterministico (idempotencia)                                         #
# --------------------------------------------------------------------------- #

def compute_job_id(params: Dict[str, Any]) -> str:
    """SHA256 deterministico dos params canonizados."""
    canonical = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:24]


# --------------------------------------------------------------------------- #
# Validacao ROI                                                                #
# --------------------------------------------------------------------------- #

MAX_AREA_KM2 = 50_000
MAX_VERTICES = 10_000


def _estimate_bbox_area_km2(bbox: List[float]) -> float:
    """Estimativa grosseira da area de um bbox em km2."""
    import math
    west, south, east, north = bbox
    lat_mid = (south + north) / 2.0
    width_km = abs(east - west) * 111.32 * math.cos(math.radians(lat_mid))
    height_km = abs(north - south) * 110.574
    return width_km * height_km


def _count_vertices(geojson: Dict[str, Any]) -> int:
    """Conta vertices de um GeoJSON (Polygon/MultiPolygon/FeatureCollection)."""
    gtype = geojson.get("type", "")
    if gtype == "FeatureCollection":
        return sum(_count_vertices(f.get("geometry", {})) for f in geojson.get("features", []))
    if gtype == "Feature":
        return _count_vertices(geojson.get("geometry", {}))
    if gtype == "Polygon":
        return sum(len(ring) for ring in geojson.get("coordinates", []))
    if gtype == "MultiPolygon":
        return sum(
            sum(len(ring) for ring in poly)
            for poly in geojson.get("coordinates", [])
        )
    return 0


def validate_roi(roi: RoiConfig) -> RoiConfig:
    """Valida area max e vertices max."""
    if roi.roi_type == RoiType.BBOX:
        if roi.bbox is None:
            raise ValueError("bbox obrigatorio quando roi_type=bbox")
        area = _estimate_bbox_area_km2(roi.bbox)
        if area > MAX_AREA_KM2:
            raise ValueError(f"Area estimada {area:.0f} km2 excede limite de {MAX_AREA_KM2} km2")
    else:
        if roi.geojson is None:
            raise ValueError("geojson obrigatorio quando roi_type!=bbox")
        verts = _count_vertices(roi.geojson)
        if verts > MAX_VERTICES:
            raise ValueError(f"GeoJSON com {verts} vertices excede limite de {MAX_VERTICES}")
    return roi


# --------------------------------------------------------------------------- #
# Conversao para ee.Geometry                                                   #
# --------------------------------------------------------------------------- #

def roi_to_ee_geometry(roi: RoiConfig) -> ee.Geometry:
    """Converte RoiConfig para ee.Geometry."""
    if roi.roi_type == RoiType.BBOX:
        w, s, e, n = roi.bbox
        return ee.Geometry.BBox(w, s, e, n)
    elif roi.roi_type == RoiType.POLYGON:
        return ee.Geometry(roi.geojson)
    else:  # feature_collection
        fc = ee.FeatureCollection(roi.geojson)
        return fc.geometry()


# --------------------------------------------------------------------------- #
# Cache keys                                                                   #
# --------------------------------------------------------------------------- #

def build_cache_key(job_id: str, product: str, z: int, x: int, y: int, version: int = 1) -> str:
    """Cache key para tile PNG: emb:{job_id}:{product}:{z}:{x}:{y}:v{version}"""
    return f"emb:{job_id}:{product}:{z}:{x}:{y}:v{version}"


def build_meta_cache_key(job_id: str, product: str) -> str:
    """Cache key para EE tile URL: emb:meta:{job_id}:{product}"""
    return f"emb:meta:{job_id}:{product}"


def build_stats_cache_key(job_id: str, product: str, params_hash: str = "") -> str:
    """Cache key para estatisticas: emb:stats:{job_id}:{product}:{hash}"""
    return f"emb:stats:{job_id}:{product}:{params_hash}"


def build_lock_key(job_id: str, product: str, z: int, x: int, y: int) -> str:
    """Lock key para geracao de tile."""
    return f"lock:emb:{job_id}:{product}:{z}:{x}:{y}"
