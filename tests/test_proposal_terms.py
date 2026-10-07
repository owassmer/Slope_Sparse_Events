"""Proposals project commitments, never intervening decisions' answers."""
import numpy as np
from question_history import question_records
from test_question_futures import sample
from walk_order import FutureChecker

from app.analysis.events import Chain, Trace
from app.disputes.forecast import _Walk


def chain_pair():
    fc, d, state = sample('yes', row=125)
    prefix = state.steps + (('post_trial_ruling', '', 'unchanged'), ('appeal', '', 'yes'))
    chains = []
    for answer in ('none', 'levy'):
        ch = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
        ch.run(prefix + (('enforce', 'post', answer),), day_only=True)
        chains.append(ch)
    return chains


def test_settlement_projection_excludes_unmade_registration_order():
    no, yes = chain_pair()
    order = yes.decision_day('court_order', 'registration_post')
    start = order - 1
    assert (start >= yes.EF).all()
    for ch in (no, yes):
        ch.settle(start, np.full(ch.n, ch.N - 1), False)
    np.testing.assert_array_equal(no.settle_offer, yes.settle_offer)
    np.testing.assert_array_equal(no.settlement_pricing['cash'], yes.settlement_pricing['cash'])
    # Once the order has actually been made it is a committed writ, not stripped.
    payment = order + 30
    a, b = (ch.proposed_terms(order, payment) for ch in (no, yes))
    assert (a['cash'] != b['cash']).any()


def test_motion_record_matches_its_own_day_replay():
    fc, d, state = sample('yes', row=125)
    walk = _Walk(fc, d)
    probe = ('stay', 'I1', 'no')
    with question_records(walk) as records:
        key = walk.node('stay_motion', 'I1', state.cls, s=state, probe=probe)
        walk.emit(walk.take(state, probe, (key, 'no'), (key,)), 'unresolved')
    checker = FutureChecker(fc, d, 0, records)
    assert checker.history(walk.out[0]) == []
    assert checker.checked['stay_motion'] == 1


def test_motion_terms_stay_fixed_while_approval_security_changes():
    no, yes = chain_pair()
    rows = []
    for ch in (no, yes):
        tr = Trace(ch.ev)
        ch.advance(tr, 'stay', 'post', 'denied')
        rows.append(ch.questions[len(ch.rec[0]) - 1]['security_terms'])
    for key in rows[0]:
        np.testing.assert_array_equal(rows[0][key], rows[1][key])
    # Actual court-day sizing still sees the order and levy reached by that day.
    a, b = (ch.stay_facts(len(ch.rec[0]) - 1) for ch in (no, yes))
    assert (a['cash'] != b['cash']).any()
