"""Array reconstruction must retain each lane's independent order verdict."""
from collections import OrderedDict
from copy import copy
from dataclasses import replace

import numpy as np
from test_walk_f34_dated_facts import FIXTURES, load

from app.analysis.array_history_checks import order_history
from app.analysis.check_replay import Replay
from app.analysis.tree_history_checks import HistoryChecks
from app.analysis.walk_checks import Checker


def test_array_order_matches_scalar_with_missing_decisions_and_prefix_reuse():
    _, _, path, _, _ = load(next(p for p in FIXTURES if '0002_168' in p.name))
    fc = HistoryChecks.forecaster()
    native = np.array([0, 17, 168])
    fc.draws = fc.draws.sub(np.isin(np.arange(fc.draws.n), native))
    d = next(d for d in fc.disputes if d.instance_id == path.instance_id)
    replay, cache = Replay(fc, d, fc.draws), OrderedDict()
    for p in (path, replace(path, steps=path.steps[:7]), path):
        actual = order_history(fc, d, p, replay, cache)
        for slot in range(len(native)):
            scalar = copy(fc)
            scalar.draws = fc.draws.sub(np.arange(len(native)) == slot)
            expected = Checker(scalar, d, 0, 0).history(p)
            assert actual[slot] == expected
        if len(p.steps) == 7:
            assert all(actual)
