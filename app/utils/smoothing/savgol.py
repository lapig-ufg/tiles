import numpy as np
from scipy.signal import savgol_filter


def savgol_smooth(days: np.ndarray, values: np.ndarray, window_size: int = 11, poly_order: int = 2) -> np.ndarray:
    if len(values) <= window_size:
        return values.copy()
    return savgol_filter(values, window_length=window_size, polyorder=poly_order)
