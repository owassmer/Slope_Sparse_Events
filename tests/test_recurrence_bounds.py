"""Bounds change actions, not amounts/dates; the case caps offering initiations."""
from types import SimpleNamespace

import pytest

from app.analysis.frontier import Decision
from app.analysis.recurrence_measurement import SampleWalk, answer_weights, signed_interval, summarize_pairs
from app.disputes.chronological import ChronologicalWalk
from app.disputes.forecast import _S, _Walk
from app.disputes.recurrence import SCENARIOS, RecurrenceBounds


def test_bounds_count_path_not_global_and_include_failed_offerings():
    b = SCENARIOS['offerings']
    steps = (('offering', 'entry', 'no'), ('offering', 'floor1', 'no'))
    assert b.reached('offering', steps)
    assert not b.reached('offering', steps[:1])
    assert not RecurrenceBounds().reached('offering', steps * 10)
    with pytest.raises(ValueError):
        RecurrenceBounds(settle=0)


@pytest.mark.parametrize('node,limit,answer', [('settle', 3, 'no'), ('cash_floor', 2, 'neither'),
                                              ('judgment_default', 1, 'no')])
def test_default_books_no_cash_action_or_probability(node, limit, answer):
    walk = object.__new__(ChronologicalWalk)
    walk.bounds = RecurrenceBounds(**{node: limit})
    result = []
    walk._continuations = [lambda s, *args: result.append(s)]
    s = _S(steps=tuple((node, str(i), 'no') for i in range(limit)), k=3)
    walk._ask(s, Decision(node, 'next'), 'unresolved')
    assert result[0].steps == s.steps + ((node, 'next', answer),)
    assert result[0].edges == s.edges
    assert result[0].k == (4 if node == 'cash_floor' else 3)


def test_offering_bound_removes_initiation_not_books_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(_Walk, 'offer', lambda *args: calls.append(args))
    w = object.__new__(ChronologicalWalk)
    s = _S(steps=(('offering', 'entry', 'no'), ('offering', 'floor1', 'no')))
    w.bounds = SCENARIOS['offerings']
    w.offer(s, 'floor2', lambda _: pytest.fail('third initiation survived'))
    assert calls == []
    w.bounds = SCENARIOS['unbounded']
    w.offer(s, 'floor2', None)
    assert calls == [(w, s, 'floor2', None)]


def test_case_bound_and_unbounded_sensitivity_reach_production(monkeypatch):
    from test_chronological_walk import walker

    w = walker()
    assert w.bounds == RecurrenceBounds(offering=2)
    seen = []
    monkeypatch.setattr(ChronologicalWalk, 'run', lambda self: seen.append(self.bounds) or [])
    assert w.fc.paths(w.d) == []
    w.fc.sens['offering_initiations'] = True
    assert w.fc.paths(w.d) == []
    assert seen == [RecurrenceBounds(offering=2), RecurrenceBounds()]
    assert RecurrenceBounds.from_model({'parameters': {}}) == RecurrenceBounds()


def test_engine_counts_failed_initiations_per_draw_and_as_of_day():
    import numpy as np
    from test_chronological_walk import walker

    from app.analysis.events import Chain
    from app.disputes.state14 import Situation

    w = walker()
    ch = Chain(w.d, w.fc.setup, w.fc.m, w.fc.draws, w.fc.sens)
    # Failed offerings consume no shares, but each initiation consumes one slot.
    for i, day in enumerate((0, 10)):
        ch.initiate(np.full(ch.n, day), str(i))
        ch.offering_outcome(str(i), False)
    assert len(ch._offers) == 2
    assert not ch.offering_bound_reached(9).any()
    assert ch.offering_bound_reached(10).all()
    assert not ch.offering_available(20).any()
    assert not ch.initiate(np.full(ch.n, 20), 'third').any()
    sit = ch.c_situation(np.full(ch.n, 20))
    assert sit['offering_bound_reached'].all()
    fake = SimpleNamespace(_g=lambda: SimpleNamespace(at_rep=lambda *a, **k: True))
    assert 'including failed offerings' in Situation.offering_unavailable_reason(fake)
    ch.sens = {'offering_initiations': True}
    assert not ch.offering_bound_reached(20).any()
    assert ch.offering_available(20).all()
    # A booking on another draw must not spend this draw's allowance.
    ch.sens = {}
    ch._offers[-1]['rows'][0] = False
    assert not ch.offering_bound_reached(20)[0]
    assert ch.offering_bound_reached(20)[1:].all()


def test_signed_deltas_and_unknown_conditional_balance():
    assert signed_interval([-2, -2])['approximate_95_percent_interval'] == [-2, -2]
    r = summarize_pairs([dict(answers='uniform', scenario='floor', touched=True,
                             before=[100, 0, 0], after=[80, 1, 20])])['uniform']['floor']
    assert r['before']['balance_given_filing_dollars']['mean'] is None
    assert r['after']['balance_given_filing_dollars']['mean'] == 20
    assert r['aggregate_delta']['collections_dollars']['mean'] == -20
    assert r['per_touched_history_delta'] == r['aggregate_delta']


def test_synthetic_sampling_preserves_scope_and_is_reproducible(monkeypatch):
    def expand(self, state, chain, pending):
        if state.steps:
            assert self.scope == state.steps
            self.result = state.steps
            return
        for i in range(3):
            self.scope = ((str(i), '', ''),)
            self._loop(SimpleNamespace(steps=self.scope), None, set())
        self.scope = ()
    monkeypatch.setattr(ChronologicalWalk, '_loop', expand)
    def sample():
        w = object.__new__(SampleWalk)
        w.seed, w.answers, w.intercept, w.result = 42, 'uniform', None, None
        w._loop(SimpleNamespace(steps=()), None, set())
        return w.result
    assert sample() == sample()
    assert answer_weights(4, 'uniform') == [1, 1, 1, 1]
    assert answer_weights(4, 'early_heavy') == [4, 1, 1, 1]
    assert answer_weights(4, 'late_heavy') == [1, 1, 1, 4]
