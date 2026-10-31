"""Exact native-root comparison with the unshared chronological scheduler."""
import random
import time

import numpy as np
import pytest
from benchmark_chronological import root
from test_chronological_walk import walker

from app.disputes.forecast import _S
from app.disputes.state_graph import GraphWalk


@pytest.mark.parametrize('name', ['saved', 'none383', 'none279'])
def test_expansion_matches_plain(name):
    prefix, row, cls = root(name)
    if name != 'saved':
        prefix += (('post_trial_ruling', '', 'set_aside'),)
    state = _S(steps=prefix, cls=cls, a4='seek', stayed=True, early=True)
    plain = walker()
    plain.fc.draws = plain.fc.draws.sub(np.arange(512) == row)
    plain.fc.draws.prefixes = {}
    from question_history import question_records
    visits = 0
    loop = plain._loop

    def counted(*args):
        nonlocal visits
        visits += 1
        return loop(*args)

    plain._loop = counted
    started = time.monotonic()
    with question_records(plain) as records:
        plain.run_from(state)
    plain_seconds = time.monotonic() - started
    base = walker()
    base.fc.draws = base.fc.draws.sub(np.arange(512) == row)
    base.fc.draws.prefixes = {}
    shared = GraphWalk(base.fc, base.d)
    started = time.monotonic()
    graph = shared.run_from(state)
    graph_seconds = time.monotonic() - started
    for _, _, _, captured in graph._histories():
        for key, at, blob in captured:
            assert blob in records[key, at, None]
    shared.materialize()
    print(dict(root=name, histories=graph.history_count(), plain_visits=visits,
               states=len(graph.nodes), hits=graph.hits,
               plain_seconds=plain_seconds, graph_seconds=graph_seconds))
    assert graph.complete
    assert graph.history_count() == len(plain.out)
    for a, b in zip(shared.out, plain.out, strict=True):
        assert a.steps == b.steps
        assert a.edges == b.edges
        assert a.outcome == b.outcome
        assert a.mask == b.mask
        assert a.classes == b.classes
    assert shared.keys == plain.keys
    from walk_order import Checker, FutureChecker
    for path in random.Random(6).sample(shared.out, min(3, len(shared.out))):
        assert not Checker(plain.fc, plain.d, 0, len(prefix)).history(path)
        assert not FutureChecker(plain.fc, plain.d, 0, records).history(path)
    from app.disputes.state_graph import freeze
    assert {k: {freeze(r) for r in v} for k, v in shared.fc.facts.items()} == {
        k: {freeze(r) for r in v} for k, v in plain.fc.facts.items()}
