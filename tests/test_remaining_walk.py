"""Continuation counting: joint leaves differ from their supported draw counts."""
import json
import random
from types import SimpleNamespace

import pytest

from app.analysis.remaining_walk import ForestProbe, RemainingProbe, estimate_remaining, spread
from app.disputes.chronological import ChronologicalWalk


@pytest.fixture
def tree(monkeypatch):
    # Terminating population at root is already emitted before a continuation.
    children = {'root': ['a', 'b'], 'a': ['x', 'y'], 'b': ['z'], 'x': [], 'y': [], 'z': []}
    populations = {'root': 100, 'a': 2, 'b': 0, 'x': 3, 'y': 5, 'z': 7}

    def init(self, fc, dispute):
        self.fc = fc
        self.out, self.keys = [], []
        self.bounds = SimpleNamespace(offering=2)
        self.scope = 'root'

    def expand(self, name):
        assert self.scope == name
        if populations[name]:
            self.emit(name, 'terminal')
        for child in children[name]:
            self.scope = child
            try:
                self._loop(child)
            finally:
                self.scope = name

    def emit(self, name, outcome):
        import numpy as np
        self.out.append(np.arange(512) < populations[name])
        self.keys.append(name)

    monkeypatch.setattr(ChronologicalWalk, '__init__', init)
    monkeypatch.setattr(ChronologicalWalk, '_loop', expand)
    monkeypatch.setattr(ChronologicalWalk, 'emit', emit)
    monkeypatch.setattr(ChronologicalWalk, 'run', lambda self: self._loop('root'))
    monkeypatch.setattr('app.disputes.forecast.path_mask', lambda p, n: p)
    state = dict(partition=0, partitions=1, depth=1, remaining=[[0], [1]],
                 recurrence_bounds={'offering': 2})
    return SimpleNamespace(draws=SimpleNamespace(n=512)), state


def test_population_continuation_weighting(tree):
    fc, saved = tree
    results = []
    for choice in (0, 1):
        rng = SimpleNamespace(randrange=lambda n, choice=choice: choice)
        w = RemainingProbe(fc, None, saved, [0], rng)
        w.run()
        results.append((w.histories, w.history_draws))
    assert results == [(3, 8), (3, 12)]
    # Exact subtree: three joint histories, 2 + 3 + 5 history-draws.
    assert sum(r[1] for r in results) / 2 == 10
    assert all(r[0] == 3 for r in results)


def test_shared_replay_keeps_scopes_and_independent_counts(tree):
    fc, saved = tree
    got = []
    targets = [(0, [0]), (0, [0]), (0, [1])]
    walk = ForestProbe(fc, None, [saved], targets, random.Random(4),
                       lambda i, route, w: got.append((route, w.histories, w.history_draws)))
    walk.run()
    assert got == [((0,), 3, 8), ((0,), 3, 12), ((1,), 1, 7)]
    assert walk.scope == 'root'
    assert not walk.out


def test_shallow_partition_target_does_not_hide_deeper_target(tree, monkeypatch):
    fc, saved = tree
    monkeypatch.setattr(RemainingProbe, 'owns', lambda self, route: True)
    got = []
    walk = ForestProbe(fc, None, [saved, saved], [(0, [0]), (1, [0, 0])], random.Random(4),
                       lambda i, route, w: got.append((i, route, w.histories)))
    walk.run()
    assert got == [(0, (0,), 3), (1, (0, 0), 1)]


def test_no_probe_is_unknown_not_zero():
    assert spread([]) == dict(mean=None, standard_error=None, interval_90=None)
    assert spread([5])['interval_90'] is None
    assert spread([5, 5])['interval_90'] == [5, 5]


def test_forest_summary_and_observed_timing(tree, monkeypatch, tmp_path):
    fc, saved = tree
    monkeypatch.setattr('app.analysis.remaining_walk.context', lambda: (fc, None))
    source = tmp_path / 'piece.json'
    source.write_text(json.dumps(saved))
    timing = tmp_path / 'walk-f1.aws' / 'piece.json'
    timing.parent.mkdir()
    timing.write_text(json.dumps(dict(seconds=3600, histories=4, history_draws=17)))
    result = estimate_remaining([source], output=tmp_path / 'out', probes=1000, timing=[timing])
    assert result['status'] == 'completed'
    for metric, exact in [('histories', 4), ('history_draws', 17)]:
        low, high = result['remaining'][metric]['interval_90']
        assert low <= exact <= high
    low, high = result['implied_worker_hours']['AWS']['histories']['interval_90']
    assert low <= 1 <= high
    assert result['by_piece'][0]['sampled'] == 1000


def test_overlapping_inputs_are_not_double_counted(tree, tmp_path):
    _, saved = tree
    paths = [tmp_path / 'a.json', tmp_path / 'b.json']
    for path in paths:
        path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match='overlapping'):
        estimate_remaining(paths, output=tmp_path / 'out', probes=2)


def test_production_bounds_must_match(tree):
    fc, saved = tree
    with pytest.raises(ValueError, match='bounds'):
        RemainingProbe(fc, None, dict(saved, recurrence_bounds={'offering': 1}), [0], random.Random(1))
