"""Weighted statistics over trajectories (each joint path's probability spread equally over the operating draws)."""

from __future__ import annotations

import numpy as np

from app.analysis.native import native_function


def weighted_quantiles(values: np.ndarray, weights: np.ndarray, qs: tuple[float, ...]) -> np.ndarray:
    """Quantiles of `values` [n] or per column of `values` [n, days] under `weights` [n] (summing to one): the smallest
    value whose cumulative weight reaches q. Returns [len(qs)] or [len(qs), days]."""
    native = native_function("weighted_quantiles")
    if native is not None:
        out = native(np.asarray(values, dtype=np.float64), np.asarray(weights, dtype=np.float64), list(qs))
        from app.analysis import shadow
        if shadow.ON:
            shadow.check("native_weighted_quantiles", out, weighted_quantiles_python(values, weights, qs))
        return out
    return weighted_quantiles_python(values, weights, qs)


def weighted_quantiles_python(values: np.ndarray, weights: np.ndarray, qs: tuple[float, ...]) -> np.ndarray:
    v = values if values.ndim == 2 else values[:, None]
    order = np.argsort(v, axis=0, kind="stable")
    sorted_v = np.take_along_axis(v, order, axis=0)
    cw = np.cumsum(weights[order], axis=0)
    out = np.empty((len(qs), v.shape[1]), dtype=np.float64)
    for i, q in enumerate(qs):
        idx = np.argmax(cw >= q - 1e-12, axis=0)
        out[i] = sorted_v[idx, np.arange(v.shape[1])]
    return out if values.ndim == 2 else out[:, 0]


def expectation(per_path_means: np.ndarray, probs: np.ndarray) -> float:
    """E[x] = sum over paths of P(path) x mean over draws."""
    native = native_function("analysis_expectation")
    if native is not None:
        return native(per_path_means, probs)
    return float(per_path_means @ probs)
