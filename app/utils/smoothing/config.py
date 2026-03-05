from enum import Enum
from typing import Dict, Any


class SmoothingMethod(str, Enum):
    RAW = "raw"
    SAVGOL = "savgol"
    WHITTAKER = "whittaker"
    SPLINE = "spline"
    LOESS = "loess"


class Satellite(str, Enum):
    LANDSAT = "landsat"
    SENTINEL2 = "sentinel2"
    MODIS = "modis"


DEFAULT_PARAMS: Dict[Satellite, Dict[SmoothingMethod, Dict[str, Any]]] = {
    Satellite.LANDSAT: {
        SmoothingMethod.WHITTAKER: {"lmbd": 10.0},
        SmoothingMethod.SAVGOL: {"window_size": 11, "poly_order": 2},
        SmoothingMethod.LOESS: {"frac": 0.1},
        SmoothingMethod.SPLINE: {"s_factor": 0.01},
    },
    Satellite.SENTINEL2: {
        SmoothingMethod.WHITTAKER: {"lmbd": 5.0},
        SmoothingMethod.SAVGOL: {"window_size": 7, "poly_order": 3},
        SmoothingMethod.LOESS: {"frac": 0.05},
        SmoothingMethod.SPLINE: {"s_factor": 0.005},
    },
    Satellite.MODIS: {
        SmoothingMethod.WHITTAKER: {"lmbd": 100.0},
        SmoothingMethod.SAVGOL: {"window_size": 13, "poly_order": 2},
        SmoothingMethod.LOESS: {"frac": 0.15},
        SmoothingMethod.SPLINE: {"s_factor": 0.02},
    },
}

DEFAULT_METHOD = SmoothingMethod.WHITTAKER
