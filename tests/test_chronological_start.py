"""Review-date and arbitrary-prefix scheduling, without judgment calls."""
from dataclasses import replace

import numpy as np
import pytest
from test_chronological_walk import walker

from app.analysis.frontier import Decision
from app.disputes.forecast import _S
from tools.measure_chronological import prefix_name


def test_review_date_has_no_seeded_legal_answers(monkeypatch):
    w = walker()
    asked = []
    monkeypatch.setattr(w, '_ask', lambda s, d, outcome: asked.append((s.steps, d)))
    w.run_from(_S(cls='claimed'), np.arange(w.fc.draws.n) == 0)
    assert asked == [((), Decision('settle', 'I0'))]


@pytest.mark.parametrize('steps,flags,expected', [
    ((), {}, {Decision('settle', 'I0')}),
    ((('settle', 'I0', 'no'),), {}, {Decision('verdict', 'I0')}),
    ((('judgment_response', 'entry', '@2=none'),), {},
     {Decision('settle', 'Ientry'), Decision('post_trial_motions')}),
    ((('post_trial_motions', '', 'yes'),), {},
     {Decision('settle', 'I1'), Decision('execute_pre_ruling', 'I1'), Decision('post_trial_ruling')}),
    ((('post_trial_motions', '', 'no'),), {}, {Decision('settle', 'I2'), Decision('appeal')}),
    ((('post_trial_motions', '', 'yes'), ('execute_pre_ruling', 'I1', 'yes')), {},
     {Decision('stay', 'I1')}),
    ((('post_trial_motions', '', 'yes'), ('execute_pre_ruling', 'I1', 'yes'), ('stay', 'I1', 'no')), {},
     {Decision('registration_early', 'I1')}),
    ((('post_trial_motions', '', 'no'), ('appeal', '', 'yes')), {'appealed': True},
     {Decision('stay', 'post'), Decision('settle', 'Iappeal')}),
    ((('post_trial_motions', '', 'no'), ('appeal', '', 'no'), ('stay', 'post', 'no')), {},
     {Decision('enforce', 'post'), Decision('settle', 'I3')}),
])
def test_chain_heads_follow_state_not_inventory_position(steps, flags, expected):
    w = walker()
    base = (('settle', 'I0', 'no'), ('verdict', 'I0', 'award:1065000100:0:top'),
            ('judgment_response', 'entry', '@2=none'))
    if steps and steps[0][0] not in ('settle', 'judgment_response'):
        steps = base + steps
    elif steps and steps[0][0] == 'judgment_response':
        steps = base[:2] + steps
    s = replace(_S(steps=steps, cls='award1065000100', a4='seek'), **flags)
    assert expected <= set(w._pending(s, frozenset()))
    answered = {Decision(n, c) for n, c, _ in steps}
    assert not answered.intersection(w._pending(s, frozenset()))


def test_resolved_without_step_is_not_reasked():
    w = walker()
    assert w._pending(_S(), frozenset({Decision('settle', 'I0')})) == (Decision('verdict', 'I0'),)


def test_prefix_names_are_content_addresses():
    a = (('settle', 'I0', 'no'),)
    b = a + (('verdict', 'I0', 'defense'),)
    assert prefix_name(a) == prefix_name(list(a))
    assert prefix_name(a) != prefix_name(b)
