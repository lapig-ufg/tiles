import numpy as np
from scipy.interpolate import UnivariateSpline


def spline_smooth(days: np.ndarray, values: np.ndarray, s_factor: float = 0.01) -> np.ndarray:
    s = len(values) * s_factor
    spline = UnivariateSpline(days, values, s=s, k=3)
    return spline(days)
