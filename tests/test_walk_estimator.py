"""Knuth weights count histories, never judgment probability mass."""
from itertools import count
from types import SimpleNamespace

import pytest

from app.analysis.walk_estimator import ProbeWalk, interval, summarize
from app.disputes.chronological import ChronologicalWalk


class Choices:
    def __init__(self, choices):
        self.choices = iter(choices)

    def randrange(self, degree):
        choice = next(self.choices)
        assert choice < degree
        return choice


def toy(monkeypatch, choices):
    # Unequal subtrees: root -> A (one leaf), B (three leaves).
    # The second pass must preserve the parent's scoped context.
    tree = {'': ['A', 'B'], 'A': [], 'B': ['B1', 'B2', 'B3'],
            'B1': [], 'B2': [], 'B3': []}

    def expand(self, state):
        name = state.name
        assert self.scope == name
        if not tree[name]:
            self.out.append(name)
        for child in tree[name]:
            self.scope = child
            self._loop(SimpleNamespace(name=child, steps=state.steps + ((child, '', 'answer'),)))
        self.scope = name

    monkeypatch.setattr(ChronologicalWalk, '_loop', expand)
    clock = count()
    monkeypatch.setattr('app.analysis.walk_estimator.time.perf_counter', lambda: next(clock))
    walk = object.__new__(ProbeWalk)
    walk.rng = Choices(choices)
    walk.intercept = None
    walk.weight = 1
    walk.leaves = walk.cost = walk.shared_cost = 0
    walk.top = None
    walk.degrees = []
    walk.out, walk.keys = [], []
    walk.scope = ''
    walk._loop(SimpleNamespace(name='', steps=()))
    return walk


def test_unequal_tree_unbiased_and_scoped(monkeypatch):
    short = toy(monkeypatch, [0])
    long = [toy(monkeypatch, [1, i]) for i in range(3)]
    assert short.leaves == 2
    assert all(w.leaves == 6 for w in long)
    assert short.leaves / 2 + sum(w.leaves / 6 for w in long) == 4
    # Six nodes in the full tree, unit cost at every node.
    assert short.cost == 3
    assert all(w.cost == 9 for w in long)
    assert short.cost / 2 + sum(w.cost / 6 for w in long) == 6
    assert short.degrees == [2, 0]
    assert long[0].degrees == [2, 3, 0]
    assert short.out == long[0].out == []


def test_intervals_and_branch_contributions():
    assert interval([])['mean'] is None
    assert interval([4])['approximate_95_percent_interval'] is None
    assert interval([4, 4])['approximate_95_percent_interval'] == [4, 4]
    result = summarize([dict(leaves=2, seconds=4, shared_seconds=1, top_branch='A'),
                        dict(leaves=6, seconds=8, shared_seconds=1, top_branch='B')])
    assert result['leaves']['mean'] == 4
    assert result['top_branches']['A']['leaves']['mean'] == 1
    assert result['top_branches']['B']['leaves']['mean'] == 3
    assert sum(b['cost_share'] for b in result['top_branches'].values()) + result['shared_cost_share'] == pytest.approx(1)
