from typing import Dict, List, Optional, Tuple

import ee
import numpy as np
import pandas as pd

from app.utils.smoothing import (
    SmoothingMethod,
    get_method_display_name,
    smooth_timeseries,
)
from app.utils.smoothing.config import DEFAULT_METHOD, Satellite


def process_gee_region_data(
    raw_data: list,
    band_name: str,
    valid_range: Optional[Tuple[float, float]] = None,
) -> Tuple[List[str], List[float]]:
    if not raw_data or len(raw_data) <= 1:
        return [], []

    header = raw_data[0]
    time_index = header.index("time")
    band_index = header.index(band_name)

    processed = [
        (row[time_index], row[band_index])
        for row in raw_data[1:]
        if row[band_index] is not None
    ]

    if not processed:
        return [], []

    df = pd.DataFrame(processed, columns=["time", band_name])
    df["date"] = pd.to_datetime(df["time"], unit="ms").dt.strftime("%Y-%m-%d")
    grouped = df.groupby("date")[band_name].mean().reset_index()

    if valid_range:
        lo, hi = valid_range
        grouped = grouped[(grouped[band_name] >= lo) & (grouped[band_name] <= hi)]

    return grouped["date"].tolist(), grouped[band_name].tolist()


def fetch_precipitation(
    point: ee.Geometry,
    data_inicio: str,
    data_fim: str,
    scale: int = 5000,
) -> Tuple[List[str], List[float]]:
    chirps = (
        ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
        .filterDate(data_inicio, data_fim)
        .filterBounds(point)
    )

    precip_data = chirps.getRegion(point, scale).getInfo()

    if not precip_data or len(precip_data) <= 1:
        return [], []

    header = precip_data[0]
    time_index = header.index("time")
    precip_index = header.index("precipitation")

    processed = [
        (row[time_index], row[precip_index])
        for row in precip_data[1:]
        if row[precip_index] is not None
    ]

    if not processed:
        return [], []

    df = pd.DataFrame(processed, columns=["time", "precipitation"])
    df["date"] = pd.to_datetime(df["time"], unit="ms").dt.strftime("%Y-%m-%d")
    grouped = df.groupby("date")["precipitation"].sum().reset_index()

    return grouped["date"].tolist(), grouped["precipitation"].tolist()


def apply_smoothing(
    dates: List[str],
    values: List[float],
    satellite: Satellite,
    method: SmoothingMethod = DEFAULT_METHOD,
) -> List[float]:
    return smooth_timeseries(dates, values, satellite, method)


def build_plotly_response(
    ndvi_dates: List[str],
    ndvi_values: List[float],
    ndvi_smoothed: List[float],
    precip_dates: List[str],
    precip_values: List[float],
    method: SmoothingMethod,
) -> List[Dict]:
    display_name = get_method_display_name(method)

    return [
        {
            "x": list(ndvi_dates),
            "y": list(ndvi_smoothed),
            "type": "scatter",
            "mode": "lines",
            "name": f"NDVI ({display_name})",
            "line": {"color": "rgb(50, 168, 82)"},
        },
        {
            "x": list(ndvi_dates),
            "y": list(ndvi_values),
            "type": "scatter",
            "mode": "markers",
            "name": "NDVI (Original)",
            "marker": {"color": "rgba(50, 168, 82, 0.3)"},
        },
        {
            "x": list(precip_dates),
            "y": list(precip_values),
            "type": "bar",
            "name": "Precipitation",
            "marker": {"color": "blue"},
            "yaxis": "y2",
        },
    ]


def parse_smoothing_method(method_str: Optional[str]) -> SmoothingMethod:
    if not method_str:
        return DEFAULT_METHOD
    try:
        return SmoothingMethod(method_str.lower())
    except ValueError:
        return DEFAULT_METHOD
