"""Notes decisions must see earlier-dated events even when those are walked later."""
from dataclasses import replace

import akoustis_20240514_fixture as fx
import numpy as np
import pytest

from app.disputes import notes
from app.disputes.forecast import _S, DisputePath, Dist, Forecaster, _Walk, atoms

# The production failure's history; instance identity fixes the sampled event dates.
STEPS = (
    ('settle', 'I0', 'no'),
    ('verdict', 'I0', 'award:2397555350:2260000400:2535110300'),
    ('judgment_response', 'entry', '@2=initiate_offering'), ('offering', 'entry', 'no'),
    ('post_trial_motions', '', 'yes'), ('settle', 'I1', 'no'), ('execute_pre_ruling', 'I1', 'yes'),
    ('cash_floor', '1', '@2=initiate_offering'), ('offering', 'floor1', 'yes'), ('stay', 'I1', 'no'),
    ('registration_early', 'I1', 'yes'), ('judgment_response', 'I1', '@2=initiate_offering'),
    ('offering', 'I1', 'no'), ('cash_out', '', '@0,2=neither'),
    ('judgment_response', 'ripe', '@2=initiate_offering'), ('offering', 'ripe', 'yes'),
    ('judgment_default', 'I1', 'no'), ('nonpayment', '', 'petition'),
    ('post_trial_ruling', '', 'reduced:1695000300:1130000200:2260000400'),
    ('cash_floor', '2', '@0,2=file'), ('judgment_default', 'ruling', 'accelerated'),
    ('settle', 'I2', 'yes'), ('listing', '', 'compliant'),
)
ORIGIN = 20


@pytest.fixture
def case():
    d = fx.pending(instance_id='dispute_002')
    fc = Forecaster([d], {}, borrower='B', review=fx.REVIEW, horizon=fx.setup().horizon,
                    hydrate=lambda f: {}, model=fx.model(), setup=fx.setup(), basis=fx.basis())
    w = _Walk(fc, d)
    key = fc.node(d, 'holders_involuntary', 'judgment_ruling', 'motions_pending')
    return fc, d, w, key


def test_later_walked_settlement_restores_a_live_notes_decision(case):
    fc, d, w, key = case
    mask = w.mask_of(STEPS)
    prefix, _ = notes.decision_row(fc, d, STEPS[:ORIGIN + 1], ORIGIN, 'holders', mask)
    complete, _ = notes.decision_row(fc, d, STEPS, ORIGIN, 'holders', mask)
    assert not fc.live(fc.nodes[key], prefix).any()
    assert np.flatnonzero(fc.live(fc.nodes[key], complete)).tolist() == [7]
    assert complete['day'][7] == 168
    assert complete['sit']['notes_due_day'][7] == 108
    assert complete['petition'][7] == -1


def test_filing_answer_does_not_change_its_own_question_facts(case):
    fc, d, w, key = case
    mask = w.mask_of(STEPS)
    filed = STEPS[:ORIGIN] + (('judgment_default', 'ruling', 'holders_file'),) + STEPS[ORIGIN + 1:]
    quiet, _ = notes.decision_row(fc, d, STEPS, ORIGIN, 'holders', mask)
    yes, _ = notes.decision_row(fc, d, filed, ORIGIN, 'holders', mask)
    for name in ('day', 'cash', 'owed', 'petition'):
        np.testing.assert_array_equal(quiet[name], yes[name])
    np.testing.assert_array_equal(notes.record(fc, d, STEPS, key, mask),
                                  notes.record(fc, d, filed, key, mask))
    actual = fc.trace(d, filed, full=True, real=True)
    assert actual.petition[7] == 168


def test_prompt_uses_actual_context_and_classes_override_stale_prefix(case):
    fc, d, w, key = case
    mask = w.mask_of(STEPS)
    fc.canon_put(key, STEPS[:ORIGIN], np.full(fc.draws.n, '', dtype=object))
    cls = notes.record(fc, d, STEPS, key, mask)
    tag = cls[7]
    assert tag
    entries = w._classes_of(((key, 'yes'),), STEPS, mask, {key: cls})
    assert tag in entries[0][1]
    state, _, _ = fc.state(fc.nodes[fc.class_key(key, tag)])
    events = ' '.join(state['assumed_events'])
    assert 'pending' not in events
    assert 'agreed to settle' in events
    assert state['situation']['decision_date'] == '30 Oct 2024'


def test_notes_filing_continuations_survive_prefix_petition_shortcut(case, monkeypatch):
    fc, d, w, _ = case
    s = _S(steps=STEPS[:ORIGIN], cls='reduced1695000300')
    # The prior distress decisions are already in this saved prefix.
    monkeypatch.setattr(w, 'first', lambda *args: False)
    continued = []
    monkeypatch.setattr(w, 'floor', lambda *args: pytest.fail('ended before earlier-dated settlement'))
    w.notes_petition(s, 'ruling', continued.append)
    by_branch = {x.steps[-1][2]: x for x in continued}
    assert set(by_branch) == {'yes', 'holders_file', 'accelerated', 'no'}
    filed = by_branch['holders_file'].steps + STEPS[ORIGIN + 1:]
    assert fc.trace(d, filed, full=True, real=True).petition[7] == 168
    rng = np.random.default_rng(9)
    keys = {k for x in continued for edge, _ in x.edges for k in atoms(edge)}
    for _ in range(10):
        dist = Dist({k: dict(zip(fc.nodes[k].branches, rng.dirichlet(np.ones(len(fc.nodes[k].branches))), strict=True))
                     for k in keys})
        assert sum(dist[x.edges[-1][0]]['yes'] for x in continued) == pytest.approx(1)
    # Emission must retain the recovered class on the actual filing continuation.
    y = replace(by_branch['holders_file'], steps=filed)
    w.emit(y, 'petition')
    path: DisputePath = w.out[-1]
    holder = next(c for c in path.classes if fc.nodes[c[0]].node == 'holders_involuntary')
    assert holder[1]
