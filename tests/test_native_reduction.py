"""Byte-exact contracts for ordered products, sparse counts, and quantiles."""
import numpy as np
import pytest

from app import _native
from app.analysis.shadow import same
from app.analysis.stats import weighted_quantiles_python


def test_edge_product_order_including_fortran_layout_and_empty_edges():
    rng = np.random.default_rng(771)
    for n, width in ((0, 0), (13, 0), (19, 41), (300, 79)):
        vals = np.r_[rng.random(23), 0.0, 1.0]
        refs = np.asfortranarray(rng.integers(len(vals), size=(n, width)))
        expected = np.ones(n)
        for j in range(width):
            expected *= vals[refs[:, j]]
        assert same(_native.edge_products(refs, vals), expected)


@pytest.mark.parametrize("bins", [1, 7, 128, 1024, 1031])
def test_sparse_histogram_weighting_matches_numpy_bits(bins):
    rng = np.random.default_rng(411)
    rows = 5
    parts = [rng.integers(rows * bins, size=n) for n in (0, 4000, 1309, 103)]
    idx, cnt, lens = [], [], []
    for flat in parts:
        i, c = _native.sparse_counts(flat, rows * bins)
        counts = np.bincount(flat, minlength=rows * bins)
        expected_idx = np.flatnonzero(counts)
        assert same((i, c), (expected_idx.astype(np.int32), counts[expected_idx].astype(np.uint32)))
        idx.append(i)
        cnt.append(c)
        lens.append(len(i))
    idx, cnt = np.concatenate(idx), np.concatenate(cnt)
    lens = np.asarray(lens, dtype=np.int64)
    for probs in (rng.random(len(lens)), np.array([0.0, 0.0, 1.0, 0.0])):
        w = np.repeat(probs, lens) * cnt
        h = np.bincount(idx, weights=w, minlength=rows * bins).reshape(rows, bins)
        for normalise in (False, True):
            expected = h / np.maximum(h.sum(axis=1, keepdims=True), 1e-300) if normalise else h
            assert same(_native.weighted_counts(idx, cnt, lens, probs, rows, bins, normalise), expected)


def test_bins_and_histogram_quantiles_preserve_boundaries():
    rng = np.random.default_rng(113)
    lo, width = rng.random(5) * 100, rng.random(5) * 10 + 1
    x = rng.integers(-1000, 1000, size=(29, 5)).astype(np.float64)
    rows = np.arange(5)[None, :]
    expected = (np.clip(((x - lo) / width).astype(np.int64), 0, 127) + rows * 128).ravel()
    assert same(_native.bins_flat(x, lo, width, 128, None), expected)
    rr = rng.integers(5, size=100)
    values = rng.integers(-1000, 1000, size=100).astype(np.float64)
    expected = np.clip(((values - lo[rr]) / width[rr]).astype(np.int64), 0, 127) + rr * 128
    assert same(_native.bins_flat(values, lo, width, 128, rr), expected)
    h = rng.random((5, 128))
    h /= h.sum(axis=1, keepdims=True)
    qs = (0.0, 0.05, 0.5, 0.95, 1.0)
    cum = np.cumsum(h, axis=1)
    expected = np.stack([lo + (np.argmax(cum >= q - 1e-12, axis=1) + 0.5) * width for q in qs])
    assert same(_native.histogram_quantiles(h, lo, width, list(qs)), expected)


@pytest.mark.parametrize("two_dimensional", [False, True])
def test_stable_quantiles_match_ties_zero_weights_nan_and_noncontiguous_views(two_dimensional):
    rng = np.random.default_rng(318)
    values = rng.integers(-5, 5, size=(131, 7)).astype(np.float64)
    values[0] = np.nan
    values[1] = -0.0
    values[2] = 0.0
    values = values[:, ::2] if two_dimensional else values[:, 2]
    weights = rng.random(131)
    weights[::3] = 0.0
    weights /= weights.sum()
    qs = (0.0, 0.05, 0.5, 0.95, 1.0)
    assert same(_native.weighted_quantiles(values, weights, list(qs)),
                weighted_quantiles_python(values, weights, qs))


def test_native_reduction_rejects_invalid_indices():
    with pytest.raises(ValueError):
        _native.edge_products(np.array([[2]], dtype=np.int64), np.ones(2))
    with pytest.raises(ValueError):
        _native.sparse_counts(np.array([-1], dtype=np.int64), 3)


def test_weighted_counts_normalization_preserves_nan_rows():
    idx = np.array([0, 2], dtype=np.int32)
    counts = np.ones(2, dtype=np.uint32)
    lengths = np.array([1, 1], dtype=np.int64)
    probabilities = np.array([np.nan, 1.0])
    expected = np.bincount(idx, weights=probabilities, minlength=4).reshape(2, 2)
    expected /= np.maximum(expected.sum(axis=1, keepdims=True), 1e-300)
    assert same(_native.weighted_counts(idx, counts, lengths, probabilities, 2, 2, True), expected)
