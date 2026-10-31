"""Population traversal must preserve each draw's histories and dated cash facts."""
from copy import copy

import numpy as np
import pytest
from question_history import question_records
from test_chronological_walk import walker
from walk_order import Checker, FutureChecker

from app.analysis.events import plain
from app.disputes.chronological import ChronologicalWalk
from app.disputes.forecast import path_mask
from app.disputes.state_graph import freeze


def sample(native, count=16):
    w = walker()
    fc, d = w.fc, w.d
    fc.draws = fc.draws.sub(np.isin(np.arange(fc.draws.n), native))
    fc.draws.prefixes = {}
    result = {i: [] for i in native}
    loop, emit, finish = w._loop, w.emit, w.terminal_trace
    traces = []

    def bounded(s, chain, pending, outcome, committed, mask, rows):
        mask = mask & np.array([len(result[i]) < count for i in native])
        return loop(s, chain, pending, outcome, committed, mask, rows)

    def terminal(s):
        tr = finish(s)
        traces.append(tr)
        return tr

    def emitted(s, outcome):
        before = len(w.out)
        emit(s, outcome)
        for p in w.out[before:]:
            mask = path_mask(p, fc.draws.n)
            replay_fc = copy(fc)
            replay_fc.nodes, replay_fc.grouped = dict(fc.nodes), set(fc.grouped)
            for row, native_id in enumerate(native):
                if mask is not None and not mask[row]:
                    continue
                checker = FutureChecker(replay_fc, d, row, records)
                facts = set()
                original = checker.facts

                def recording(blob, original=original, facts=facts):
                    value = original(blob)
                    facts.add(freeze(value))
                    return value

                checker.facts = recording
                questions = set()
                snapshot = checker.snapshot

                def question(*args, snapshot=snapshot, questions=questions):
                    value = snapshot(*args)
                    questions.add(freeze((args[0], value)))
                    return value

                checker.snapshot = question
                assert not checker.history(p)
                assert not Checker(fc, d, row, 0).history(p)
                tr = traces[-1]
                # Events remain compact when the chronological leaf has split.
                local = row if tr.rows is None else list(tr.rows).index(row)
                def event_row(value, local=local):
                    if isinstance(value, dict):
                        return {k: event_row(v) for k, v in value.items()}
                    return value[local] if isinstance(value, np.ndarray) else value

                cash = tuple(freeze(event_row(getattr(tr.events, k)))
                             for k in ('cash', 'lock', 'capacity', 'petition', 'kinds', 'incurred', 'proceeds'))
                result[native_id].append((tuple((n, c, plain(b)) for n, c, b in p.steps),
                                          p.outcome, cash, frozenset(facts), frozenset(questions)))
        traces.clear()

    w._loop, w.terminal_trace = bounded, terminal
    with question_records(w) as records:
        recorded_emit = w.emit
        emit = recorded_emit
        w.emit = emitted
        w.run()
    assert all(len(v) == count for v in result.values())
    return result


def test_population_matches_solo():
    native = [0, 145, 146, 365]
    population = sample(native)
    for row in native:
        assert population[row] == sample([row])[row]


@pytest.mark.parametrize('size', [8, 64, 512])
def test_every_draw_matches_solo(size):
    native = sorted([146] + [i for i in range(512) if i != 146][:size - 1])
    population = sample(native, count=1)
    for row in native:
        assert population[row] == sample([row], count=1)[row]


def test_freezing_one_draw_does_not_freeze_the_other_draws_question():
    from app.analysis.events import BIG
    from app.disputes.forecast import _S

    w = walker()
    n = w.fc.draws.n
    day = np.full(n, 167)
    row = {'day': day, 'cash': np.arange(n)}
    w._dated_records, w._dated_classes = (), {}
    w._question_queue = (('hearing', 0, day, (), (('hearing', (), row),), None),)
    boundary = np.full(n, 161)
    boundary[1] = 170
    w.freeze_due(_S(), boundary, np.ones(n, dtype=bool))
    frozen = w._dated_records[0][2]
    assert frozen['day'][0] == BIG  # a later offering can still change this draw's facts
    assert frozen['day'][1] == 167
    assert w._question_queue[0][2][0] == 167
    assert w._question_queue[0][2][1] == BIG


def test_production_uses_population(monkeypatch):
    w = walker()
    calls = []
    monkeypatch.setattr(ChronologicalWalk, 'run', lambda self: calls.append(self.fc.draws.n) or [])
    assert w.fc.paths(w.d) == []
    assert calls == [512]
    from app.disputes import parallel

    def structural(*args, **kwargs):
        raise AssertionError('parallel entry point bypassed population chronology')

    monkeypatch.setattr(parallel, 'walk', structural)
    assert parallel.all_paths(w.fc, 4)[w.d.instance_id] == {'': []}
    assert calls == [512, 512]
