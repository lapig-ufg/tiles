from typing import List

import numpy as np

from .config import DEFAULT_METHOD, DEFAULT_PARAMS, Satellite, SmoothingMethod
from .loess import loess_smooth
from .savgol import savgol_smooth
from .spline import spline_smooth
from .whittaker import whittaker_smooth

MIN_POINTS = 4


def _dates_to_julian_days(dates: List[str]) -> np.ndarray:
    from datetime import datetime
    base = datetime.strptime(dates[0], "%Y-%m-%d")
    return np.array([
        (datetime.strptime(d, "%Y-%m-%d") - base).days for d in dates
    ], dtype=float)


def smooth_timeseries(
    dates: List[str],
    values: List[float],
    satellite: Satellite,
    method: SmoothingMethod = DEFAULT_METHOD,
) -> List[float]:
    if method == SmoothingMethod.RAW or len(values) < MIN_POINTS:
        return list(values)

    vals = np.array(values, dtype=float)
    days = _dates_to_julian_days(dates)
    params = DEFAULT_PARAMS.get(satellite, {}).get(method, {})

    if method == SmoothingMethod.SAVGOL:
        result = savgol_smooth(days, vals, **params)
    elif method == SmoothingMethod.WHITTAKER:
        result = whittaker_smooth(days, vals, **params)
    elif method == SmoothingMethod.SPLINE:
        result = spline_smooth(days, vals, **params)
    elif method == SmoothingMethod.LOESS:
        result = loess_smooth(days, vals, **params)
    else:
        return list(values)

    return result.tolist()


def get_method_display_name(method: SmoothingMethod) -> str:
    names = {
        SmoothingMethod.RAW: "Raw",
        SmoothingMethod.SAVGOL: "Savgol",
        SmoothingMethod.WHITTAKER: "Whittaker",
        SmoothingMethod.SPLINE: "Spline",
        SmoothingMethod.LOESS: "LOESS",
    }
    return names.get(method, method.value)
