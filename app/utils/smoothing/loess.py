import numpy as np


def loess_smooth(days: np.ndarray, values: np.ndarray, frac: float = 0.1) -> np.ndarray:
    from statsmodels.nonparametric.smoothers_lowess import lowess
    result = lowess(values, days, frac=frac, return_sorted=False)
    return result
