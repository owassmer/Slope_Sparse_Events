"""Probability arithmetic retains scalar types, evaluation order and dictionary order."""
import json
import math
import struct
import sys

import numpy as np
import pytest

from app import _native
from app.analysis import reduce
from app.analysis.shadow import same, where
from app.disputes import forecast


class PythonDist(forecast.Dist):
    """Composite oracle whose recursive lookups cannot select the Rust dispatch."""

    def __missing__(self, key):
        return self._missing_py(key)


def key(parts):
    return "=" + json.dumps(parts, separators=(",", ":"))


def exact(actual, expected):
    if isinstance(actual, dict):
        assert isinstance(expected, dict)
        assert list(actual) == list(expected)
        for name in actual:
            exact(actual[name], expected[name])
        return
    if isinstance(actual, (list, tuple)):
        assert type(actual) is type(expected) and len(actual) == len(expected)
        for a, b in zip(actual, expected, strict=True):
            exact(a, b)
        return
    if isinstance(actual, (np.ndarray, np.floating)) and x87(actual.dtype):
        # This ABI stores an 80-bit number in 16 bytes. NumPy leaves the last
        # six bytes as C-stack padding: even np.array(v) vs its __call__(v)
        # produces different padding with identical values. Compare every
        # numeric bit, including signed zero and the full NaN payload.
        if isinstance(actual, np.ndarray):
            assert isinstance(expected, np.ndarray)
            assert actual.dtype == expected.dtype and actual.shape == expected.shape
            a = np.ascontiguousarray(actual).view(np.uint8).reshape(-1, 16)
            b = np.ascontiguousarray(expected).view(np.uint8).reshape(-1, 16)
            assert a[:, :10].tobytes() == b[:, :10].tobytes()
        else:
            assert type(actual) is type(expected)
            assert actual.tobytes()[:10] == expected.tobytes()[:10]
        return
    assert same(actual, expected), where(actual, expected)
    if isinstance(actual, np.floating):
        assert actual.tobytes() == expected.tobytes()


def x87(dtype):
    return (sys.byteorder == "little" and dtype == np.dtype(np.longdouble) and dtype.itemsize == 16
            and np.finfo(dtype).nmant == 63 and np.finfo(dtype).nexp == 15)


@pytest.mark.skipif(np.finfo(np.longdouble).nmant <= 52, reason="longdouble has no additional precision")
def test_exact_comparator_retains_low_longdouble_mantissa_bits():
    before = np.longdouble(0.5)
    after = np.nextafter(before, np.longdouble(1.0))
    assert float(before) == float(after)
    with pytest.raises(AssertionError):
        exact(before, after)
    with pytest.raises(AssertionError):
        exact(np.array([before]), np.array([after]))


@pytest.mark.parametrize("values", [
    [1e16, 1.0, -1e16],
    [0.1] * 11 + [1e-17] * 9,
    [-0.0],
    [],
    [np.float64(0.1)] * 7 + [0.3, np.float64(1e-17)],
    [0.1, 1e-17, np.float32(0.2), 0.3, np.float64(1e-17)],
    [np.longdouble("0.1234567890123456789"), np.longdouble("0.234567890123456789")],
    [math.inf, -math.inf],
    [float("nan")],
    [1, 0.1, -0.2],
    [2**100, -(2**100), 0.7],
    [True, False, -0.3],
    [2**63, -(2**63), 1e16, 1.0, -1e16],
    [2**62, 2**62, -(2**63), 1e16, 1.0, -1e16],
])
def test_composite_sum_cache_and_scalar_types_match_independent_python(values):
    dist = {f"q{i}": {"a": value} for i, value in enumerate(values)}
    composite = key([[(f"q{i}", "a")] for i in range(len(values))])
    native, oracle = forecast.Dist(dist), PythonDist(dist)
    expected = oracle[composite]
    actual = _native.probability_dist_missing(native, composite)
    exact(actual, expected)
    assert native[composite] is actual
    assert list(native) == list(oracle)
    assert native[composite] is native[composite]


@pytest.mark.parametrize("parts", [[], [[]], [[], []], [[("q", "a"), ("r", "b")]]])
def test_composite_empty_products_and_clipping(parts):
    composite = key(parts)
    values = {"q": {"a": -4.0}, "r": {"b": 2.0}}
    exact(_native.probability_dist_missing(forecast.Dist(values), composite), PythonDist(values)[composite])


def test_nested_composites_are_computed_and_cached_natively():
    inner = key([[('q', 'a'), ('r', 'b')]])
    outer = key([[(inner, 'yes'), ('s', 'a')], [(inner, 'no'), ('t', 'a')]])
    values = {"q": {"a": 0.7}, "r": {"b": 0.3}, "s": {"a": 0.9}, "t": {"a": 0.4}}
    native, oracle = forecast.Dist(values), PythonDist(values)
    edges = ((outer, "yes"),)
    exact(_native.probability_path(edges, native), forecast._path_probability_py(edges, oracle))
    exact(native, oracle)


def test_conjunction_metadata_is_cached_across_probability_settings(monkeypatch):
    composite = key([[('q', 'a'), ('r', 'a')]])
    forecast._conjunctions.cache_clear()
    loads = forecast.json.loads
    calls = []

    def observed_loads(value, *args, **kwargs):
        calls.append(value)
        return loads(value, *args, **kwargs)

    monkeypatch.setattr(forecast.json, "loads", observed_loads)
    first = forecast.Dist({"q": {"a": 0.1}, "r": {"a": 0.2}})
    second = forecast.Dist({"q": {"a": 0.7}, "r": {"a": 0.8}})
    assert _native.probability_dist_missing(first, composite)["yes"] == 0.1 * 0.2
    assert _native.probability_dist_missing(second, composite)["yes"] == 0.7 * 0.8
    assert len(calls) == 1


@pytest.mark.parametrize("kind", [float, np.float32, np.float64, np.longdouble, np.int64])
def test_path_groups_and_derivatives_preserve_numpy_scalar_arithmetic(kind):
    values = [kind(0.1), kind(0.7), kind(0.9), kind(0.3), kind(0.2)]
    dist = {f"q{i}": {"a": value} for i, value in enumerate(values)}
    edges = tuple((f"q{i}", "a") for i in range(len(values)))
    exact(_native.probability_path(edges, forecast.Dist(dist)),
          forecast._path_probability_py(edges, PythonDist(dist)))
    exact(_native.probability_groups(edges, [forecast.Dist(dist), forecast.Dist(dist)]),
          reduce.group_probs_py(edges, [PythonDist(dist), PythonDist(dist)]))
    exact(_native.probability_derivative(edges, forecast.Dist(dist)),
          reduce.atoms_derivative_py(edges, PythonDist(dist)))


@pytest.mark.parametrize("kind", [float, np.float32, np.float64, np.longdouble])
def test_sharded_group_dtype_inference_retains_original_scalar_products(kind):
    edges = (("q", "a"), ("r", "a"))
    values = [{"q": {"a": kind(q)}, "r": {"a": kind(r)}} for q, r in [(0.3, 0.7), (0.8, 0.1)]]
    expected = np.array([forecast._path_probability_py(edges, PythonDist(dist)) for dist in values])
    actual = _native.probability_groups(edges, [forecast.Dist(dist) for dist in values], True)
    exact(actual, expected)
    assert _native.probability_groups(edges, [forecast.Dist(dist) for dist in values]).dtype == np.dtype("float64")
    exact(_native.probability_groups(edges, [], True), np.array([]))


@pytest.mark.parametrize("zero", [0.0, -0.0])
def test_zero_edges_keep_probability_sign_and_derivative_order(zero):
    values = {"z": {"a": zero}, "b": {"a": 0.7}, "c": {"a": 0.2}}
    for edges in [(), (("z", "a"),), (("b", "a"), ("z", "a"), ("c", "a")),
                  (("z", "a"), ("b", "a"), ("z2", "a"))]:
        dist = {**values, "z2": {"a": zero}}
        exact(_native.probability_path(edges, forecast.Dist(dist)),
              forecast._path_probability_py(edges, PythonDist(dist)))
        exact(_native.probability_derivative(edges, forecast.Dist(dist)),
              reduce.atoms_derivative_py(edges, PythonDist(dist)))
    actual = _native.probability_groups((("z", "a"),), [forecast.Dist(values)])
    assert actual.tobytes() == struct.pack("d", zero)
    exact(_native.probability_groups((), []), np.ones(0))


@pytest.mark.parametrize("branch", ["yes", "no"])
def test_composite_derivative_conjunction_and_prefix_suffix_order(branch):
    composite = key([[('c', 'yes'), ('a', 'yes')], [('a', 'no'), ('b', 'yes')],
                     [('c', 'no'), ('a', 'yes')]])
    values = {"a": {"yes": np.float32(0.3), "no": 0.7},
              "b": {"yes": np.float64(0.4)}, "c": {"yes": 0.6, "no": np.float32(0.4)},
              "d": {"a": 0.9}, "e": {"a": 0.15}}
    edges = (("d", "a"), (composite, branch), ("e", "a"))
    actual = _native.probability_derivative(edges, forecast.Dist(values))
    expected = reduce.atoms_derivative_py(edges, PythonDist(values))
    exact(actual, expected)
    assert [(q, a) for q, a, _ in actual] == [(q, a) for q, a, _ in expected]


def test_long_path_products_keep_edge_order_and_derivative_cancellation_is_filtered():
    rng = np.random.default_rng(5993)
    values = {f"q{i}": {"a": float(v)} for i, v in enumerate(0.8 + rng.random(107) * 0.2)}
    edges = tuple((name, "a") for name in values)
    for ordered in (edges, tuple(reversed(edges))):
        exact(_native.probability_path(ordered, forecast.Dist(values)),
              forecast._path_probability_py(ordered, PythonDist(values)))
        exact(_native.probability_derivative(ordered, forecast.Dist(values)),
              reduce.atoms_derivative_py(ordered, PythonDist(values)))
    composite = key([[('z', 'a'), ('positive', 'a')], [('z', 'a'), ('negative', 'a')],
                     [('last', 'a'), ('z', 'a')]])
    values = {"z": {"a": 0.0}, "positive": {"a": 1.0}, "negative": {"a": -1.0},
              "last": {"a": 0.0}}
    exact(_native.probability_derivative(((composite, "yes"),), forecast.Dist(values)),
          reduce.atoms_derivative_py(((composite, "yes"),), PythonDist(values)))
    assert _native.probability_derivative(((composite, "yes"),), forecast.Dist(values)) == []


@pytest.mark.parametrize("edges,expected", [
    ((("q", "a"), ("q", "b")), "q"),
    (((key([[('q', 'a'), ('r', 'b')]]), "yes"), ("r", "b")), "r"),
    (((key([[('b', 'a'), ('a', 'a')]]), "yes"),
      (key([[('a', 'a'), ('b', 'a')]]), "no")), "a"),
])
def test_nonlinear_repeat_is_rejected_before_probability_lookup(edges, expected):
    message = f"a path reads {expected} twice: its probability is not linear in it"
    for function in [_native.probability_derivative, reduce.atoms_derivative_py]:
        with pytest.raises(ValueError, match=message):
            function(edges, PythonDist())


def test_composite_and_lookup_errors_retain_host_exception_types():
    for function in [_native.probability_path, forecast._path_probability_py]:
        for edges, values, missing in [((("absent", "yes"),), {}, "absent"),
                                      ((("q", "missing"),), {"q": {"yes": 1.0}}, "missing")]:
            with pytest.raises(KeyError) as error:
                function(edges, forecast.Dist(values))
            assert error.value.args == (missing,)
    with pytest.raises(json.JSONDecodeError):
        _native.probability_dist_missing(forecast.Dist(), "=not-json")
    with pytest.raises(KeyError) as error:
        _native.probability_dist_missing(forecast.Dist(), "ordinary")
    assert error.value.args == ("ordinary",)
    for malformed in ("=[[[]]]", '= [[["q"]]]', '= [[["q", "yes", "extra"]]]'):
        for function in [_native.probability_dist_missing, lambda dist, k: dist[k]]:
            with pytest.raises(ValueError):
                function(PythonDist(), malformed)


def test_production_dispatch_has_no_numeric_oracle_fallback(monkeypatch):
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "rust")

    def forbidden(*args, **kwargs):
        raise AssertionError("probability oracle called in production")

    monkeypatch.setattr(forecast.Dist, "_missing_py", forbidden)
    monkeypatch.setattr(forecast, "_path_probability_py", forbidden)
    monkeypatch.setattr(reduce, "group_probs_py", forbidden)
    monkeypatch.setattr(reduce, "atoms_derivative_py", forbidden)
    composite = key([[('q', 'yes'), ('r', 'yes')]])
    dist = forecast.Dist({"q": {"yes": 0.5}, "r": {"yes": 0.25}})
    assert dist[composite] == {"yes": 0.125, "no": 0.875}
    assert forecast.path_probability(((composite, "yes"),), dist) == 0.125
    exact(reduce.group_probs(((composite, "yes"),), [dist]), np.array([0.125]))
    assert reduce.edge_prob("q", "yes", dist) == 0.5
    assert reduce.atoms_derivative(((composite, "yes"),), dist) == [("q", "yes", 0.25), ("r", "yes", 0.5)]


@pytest.mark.parametrize("length", [32, 8192])
def test_probability_lookups_reuse_original_key_objects(length):
    class OriginalKey(str):
        __hash__ = str.__hash__

        def __eq__(self, other):
            assert other is self, "native lookup rebuilt the Python key"
            return True

    branch = OriginalKey("answer")
    keys = [OriginalKey("q" * length + str(i)) for i in range(20)]
    edges = tuple((k, branch) for k in keys)
    values = {k: {branch: 0.99} for k in keys}
    expected = forecast._path_probability_py(edges, PythonDist(values))
    exact(_native.probability_path(edges, forecast.Dist(values)), expected)
    exact(_native.probability_groups(edges, [forecast.Dist(values), forecast.Dist(values)]),
          np.array([expected, expected]))
    assert _native.probability_edge(keys[0], branch, forecast.Dist(values)) == 0.99
    derivative = _native.probability_derivative(edges, forecast.Dist(values))
    assert all(q is k and a is branch for (q, a, _), k in zip(derivative, keys, strict=True))


def test_composite_cache_keeps_original_large_key_object():
    class OriginalKey(str):
        __hash__ = str.__hash__

        def __eq__(self, other):
            assert other is self, "native composite lookup rebuilt the Python key"
            return True

    composite = OriginalKey(key([[("q" * 8192, "yes")]]))
    values = {"q" * 8192: {"yes": 0.75}}
    dist = forecast.Dist(values)
    assert _native.probability_dist_missing(dist, composite) == {"yes": 0.75, "no": 0.25}
    assert any(k is composite for k in dist)
    assert _native.probability_path(((composite, "yes"),), dist) == 0.75
    exact(_native.probability_groups(((composite, "yes"),), [dist, dist]), np.array([0.75, 0.75]))
