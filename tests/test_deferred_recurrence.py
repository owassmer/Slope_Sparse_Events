"""Date-local deferred records and settlement on newly effective legal states."""
from dataclasses import replace

import numpy as np
from test_question_futures import sample

from app.analysis.events import DisputePath, _same_state, event_trace
from app.disputes.forecast import _Walk


def trace(fc, d, steps):
    return event_trace(d, DisputePath(d.instance_id, steps, '', ()), fc.setup, fc.m, fc.draws, fc.sens)


def test_offering_record_does_not_inherit_a_later_settlement_quote():
    fc, d, state = sample()
    prefix = state.steps[:2] + (('judgment_response', 'entry', 'initiate_offering'),
                               ('offering', 'entry', 'yes'))
    a = trace(fc, d, prefix)
    b = trace(fc, d, prefix + (('post_trial_motions', '', 'yes'), ('settle', 'I1', 'no'),
                              ('listing', '', 'compliant')))
    assert _same_state(a.questions[3], b.questions[3])
    assert not b.questions[3]['settle_offer'].any()


def test_motion_projection_does_not_use_a_later_debtor_payment():
    fc, d, state = sample('yes')
    prefix = state.steps + (('execute_pre_ruling', 'I1', 'yes'), ('stay', 'I1', 'no'))
    a = trace(fc, d, prefix)
    b = trace(fc, d, prefix + (('registration_early', 'I1', 'yes'),
                              ('judgment_response', 'I1', 'pay')))
    assert _same_state(a.questions[len(prefix)-1], b.questions[len(prefix)-1])


def test_effective_early_stay_opens_settlement_before_ruling(monkeypatch):
    fc, d, state = sample('yes', row=145)
    state = replace(state, steps=state.steps + (('execute_pre_ruling', 'I1', 'yes'),
                                              ('stay', 'I1', 'yes')), stayed=True)
    walk = _Walk(fc, d)
    asked = []
    monkeypatch.setattr(walk, 'settle', lambda s, interval, then: asked.append(interval))
    walk.ripe_i1(state)
    assert asked == ['Istay']
    result = trace(fc, d, state.steps + (('settle', 'Istay', 'no'),))
    day = result.questions[len(state.steps)]['day']
    stay = result.stays[len(state.steps)-1]['day']
    np.testing.assert_array_equal(day, stay)


def test_appeal_opens_its_own_settlement(monkeypatch):
    fc, d, state = sample('yes')
    walk = _Walk(fc, d)
    asked = []
    monkeypatch.setattr(walk, 'settle', lambda s, interval, then: asked.append(interval))
    walk.appeal_settlement(state)
    assert asked == ['Iappeal']
