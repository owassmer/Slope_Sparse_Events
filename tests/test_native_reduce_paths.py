"""Production runner reduction on six real paths, four draws and a 45-day horizon.

This bounded differential exercises class splits and partial masks; it does not
claim to cover the entire 512-draw production tree.
"""
from __future__ import annotations

import os
import pickle
import struct
import subprocess
import sys
from collections import Counter
from dataclasses import fields, is_dataclass, replace
from datetime import timedelta
from pathlib import Path

import akoustis_20240514_fixture as fx
import numpy as np

from app import _native
from app.analysis import core, events, operating, reduce
from app.analysis import setup as settings
from app.analysis.build import basis_for
from app.disputes import forecast as F
from app.finance.bank import load_feed


def exact(actual, expected, at="root"):
    """Compare numerical bytes, Python container types and insertion order."""
    assert type(actual) is type(expected), (at, type(actual), type(expected))
    if isinstance(expected, np.ndarray):
        assert actual.dtype == expected.dtype and actual.shape == expected.shape, at
        if expected.dtype == object:
            assert actual.tolist() == expected.tolist(), at
        else:
            assert numerical_bytes(actual) == numerical_bytes(expected), at
    elif isinstance(expected, dict):
        assert list(actual) == list(expected), at
        for key in expected:
            exact(actual[key], expected[key], f"{at}.{key}")
    elif isinstance(expected, (list, tuple)):
        assert len(actual) == len(expected), at
        for i, (a, b) in enumerate(zip(actual, expected, strict=True)):
            exact(a, b, f"{at}[{i}]")
    elif is_dataclass(expected):
        for field in fields(expected):
            exact(getattr(actual, field.name), getattr(expected, field.name), f"{at}.{field.name}")
    elif isinstance(expected, np.generic):
        assert numerical_bytes(np.asarray(actual)) == numerical_bytes(np.asarray(expected)), at
    elif isinstance(expected, float):
        assert struct.pack("=d", actual) == struct.pack("=d", expected), at
    else:
        assert actual == expected, at


def numerical_bytes(array):
    data = array.tobytes()
    if array.dtype == np.longdouble and np.finfo(np.longdouble).nmant == 63 and array.itemsize > 10:
        # The x87 value has 80 meaningful bits; the remaining ABI padding bytes
        # are uninitialized storage, rather than part of the numerical contract.
        rows = np.frombuffer(data, dtype=np.uint8).reshape(-1, array.itemsize)
        return rows[:, :10].tobytes() if sys.byteorder == "little" else rows[:, -10:].tobytes()
    return data


def answer_kinds(case):
    for name, dtype in (("python", None), ("float32", np.float32), ("longdouble", np.longdouble)):
        if dtype is None:
            yield name, case
        else:
            answers = {key: {branch: dtype(value) for branch, value in distribution.items()}
                       for key, distribution in case[6].items()}
            yield name, (*case[:6], answers, case[7])


def finite_case(monkeypatch):
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", "python")
    monkeypatch.setattr(events, "Chain", events.PythonChain)
    for module in (core, operating, settings):
        monkeypatch.setattr(module, "DRAWS", 4)
    setup = replace(fx.setup(), horizon=fx.REVIEW + timedelta(days=45))
    feed = load_feed(fx.SNAP)
    dispute = fx.pending().model_copy(update={"status": "interpreted"})
    model = fx.model()
    fc = F.Forecaster([dispute], {}, borrower="A", review=fx.REVIEW, horizon=setup.horizon,
                      hydrate=lambda f: {}, setup=setup, model=model, basis=basis_for(feed, setup))
    walk = F._Walk(fc, dispute)
    paths = F.merge_equivalent(walk.run(), walk.keys, {key: node.branches for key, node in fc.nodes.items()})
    known = set(fc.nodes)
    split = next(i for i, p in enumerate(paths) if len(F.expand_classes([p], known, 4)) > 1)
    masked = next(i for i, p in enumerate(paths) if p.mask is not None and not F.path_mask(p, 4).all())
    selected = list(dict.fromkeys([0, split, masked, *(
        next(i for i, p in enumerate(paths) if p.outcome == outcome)
        for outcome in ("paid", "unresolved", "petition"))]))
    paths = [paths[i] for i in selected]
    assert len(paths) == 6
    children = F.expand_classes(paths, known, 4)
    assert len(children) > len(paths)
    assert any(p.mask is not None and not F.path_mask(p, 4).all() for p in children)
    answers = {key: {branch: (j + 1) / sum(range(1, len(node.branches) + 1))
                     for j, branch in enumerate(node.branches)} for key, node in fc.nodes.items()}
    # A zero-probability path must still run and contribute its adverse row.
    edge = children[0].edges[0]
    zero_key, zero_answer = F._conjunctions(edge[0])[0][0] if edge[0].startswith(F.COMPOSITE) else edge
    answers[zero_key] = {branch: float(branch != zero_answer) / (len(answers[zero_key]) - 1)
                        for branch in answers[zero_key]}
    return feed, setup, dispute, model, fc, paths, answers, zero_key


def run_blocks(case, monkeypatch, backend):
    feed, setup, dispute, model, fc, paths, answers, question = case
    monkeypatch.setenv("SLOPE_EXECUTION_BACKEND", backend)
    from app.analysis.rust_chain import make_chain

    monkeypatch.setattr(events, "Chain", events.PythonChain if backend == "python" else make_chain(events.PythonChain))
    full_names, full, scalar_names, scalar = reduce.settings_for(answers, fc.nodes)
    assert full_names == ["jev", "neutral"] and scalar_names
    a = reduce.prepared(feed, setup, dispute, model, {})
    stress_a = reduce.prepared(feed, setup, dispute, model, {}, stress=True)
    observed, stress_rows = [], []

    def on_child(child, trajectories, events, mask):
        observed.append((child, mask, trajectories.cash.copy(), trajectories.due.copy(), events.cash.copy()))

    blocks = []
    for part in (paths[:3], paths[3:]):
        table = reduce.make_tables(a, full_names, scalar_names, fc.ev_range)
        blocks.append(reduce.reduce_paths(a, part, full, scalar, set(fc.nodes), frozenset(), table,
                                          stress_a=stress_a, stress_out=stress_rows, on_child=on_child))
    result = blocks[0].merge(blocks[1])
    assert result.paths == len(paths) and result.groups == len(observed)
    assert result.clipped == 0
    assert len(stress_rows) == len(paths)
    assert len(result.stress) == min(len(paths), reduce.STRESS_KEEP)
    assert result.dscal and result.dser and result.skeys == sorted(result.skeys)
    assert any(F.path_probability(child.edges, full[0]) == 0.0 for child, *_ in observed)
    distribution = {branch: float(i == 0) for i, branch in enumerate(answers[question])}
    output = {
        "table": result.__getstate__(), "children": observed, "stress": stress_rows,
        "metrics": [result.metrics(i) for i in range(len(full_names))],
        "daily": [result.daily(i, a.line.limit) for i in range(len(full_names))],
        "floor": [result.first_floor(i) for i in range(len(full_names))],
        "fine_quantiles": {name: [result._fine_q(name, i, core.QS) for i in range(len(full_names))]
                           for name in result.ranges},
        "override": result.override(question, distribution, answers[question]),
        "full_names": full_names, "scalar_names": scalar_names,
        "probabilities": [reduce.group_probs(child.edges, full) for child, *_ in observed],
        "derivatives": [reduce.atoms_derivative(child.edges, full[0]) for child, *_ in observed],
        "composite_distribution": F.Dist(answers)[observed[0][0].edges[0][0]],
    }
    # Replay one changed question through the production reducer, independently
    # of the stored derivative. Its scalars should agree within the settings'
    # arithmetic precision (float32 products introduce rounding along the path).
    changed = F.Dist({**answers, question: distribution})
    replay = reduce.make_tables(a, ["changed"], [], fc.ev_range)
    reduce.reduce_paths(a, paths, [changed], [], set(fc.nodes), frozenset(), replay)
    replayed = replay.expected()
    assert list(output["override"]) == list(replayed)
    value = next(iter(next(iter(answers.values())).values()))
    tolerance = max(2e-14, 32 * np.finfo(type(value)).eps) if isinstance(value, np.floating) else 2e-14
    np.testing.assert_allclose(list(output["override"].values()), list(replayed.values()), rtol=tolerance, atol=1e-6)
    output["override_replay"] = replay.__getstate__()
    return output


def test_selected_rust_production_reduce_paths_matches_python_and_runs_native_exports(monkeypatch, tmp_path):
    # The retained baseline starts with a Python-only import graph in a separate
    # process. It shares neither native state nor mutable draw caches with Rust.
    baseline = tmp_path / "python_tables.pkl"
    env = {**os.environ, "SLOPE_EXECUTION_BACKEND": "python",
           "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    completed = subprocess.run([sys.executable, __file__, str(baseline)], env=env,
                               capture_output=True, text=True, timeout=300, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    with baseline.open("rb") as stream:
        expected = pickle.load(stream)
    case = finite_case(monkeypatch)
    required = (
        "probability_dist_missing", "probability_path", "probability_groups", "probability_derivative",
        "tables_add", "tables_merge", "tables_fine_q", "tables_first_floor", "tables_override",
        "tables_metrics", "tables_daily", "tables_expected",
        "walk_expand_classes", "daily_kernel", "analysis_bins_from",
        "analysis_peak_day", "analysis_petition_at", "analysis_stress_row",
    )
    calls = Counter()
    for name in required:
        native = getattr(_native, name)  # Missing extension/export is a failure.
        assert native.__module__ == "app._native", name

        def observed(*args, _name=name, _native=native, **kwargs):
            calls[_name] += 1
            return _native(*args, **kwargs)

        monkeypatch.setattr(_native, name, observed)
    for name, variant in answer_kinds(case):
        exact(run_blocks(variant, monkeypatch, "rust"), expected[name], name)
    assert all(calls[name] > 0 for name in required), calls


if __name__ == "__main__":
    import pytest

    with pytest.MonkeyPatch.context() as patch:
        case = finite_case(patch)
        snapshot = {name: run_blocks(variant, patch, "python") for name, variant in answer_kinds(case)}
    with Path(sys.argv[1]).open("wb") as stream:
        pickle.dump(snapshot, stream, protocol=pickle.HIGHEST_PROTOCOL)
