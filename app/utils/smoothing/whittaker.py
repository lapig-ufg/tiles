import numpy as np
from scipy import sparse
from scipy.sparse.linalg import spsolve


def _second_order_diff_matrix(n: int) -> sparse.csc_matrix:
    e = np.ones(n)
    D = sparse.spdiags([e, -2 * e, e], [0, 1, 2], n - 2, n, format="csc")
    return D


def whittaker_smooth(days: np.ndarray, values: np.ndarray, lmbd: float = 10.0) -> np.ndarray:
    n = len(values)
    w = np.ones(n)
    W = sparse.diags(w)
    D = _second_order_diff_matrix(n)
    Z = W + lmbd * D.T.dot(D)
    return spsolve(Z.tocsc(), w * values)
