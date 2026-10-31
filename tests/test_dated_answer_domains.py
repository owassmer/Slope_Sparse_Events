"""A saved failing walk: an earlier offering must precede a later answer domain."""
from types import SimpleNamespace

import akoustis_20240514_fixture as fx
import numpy as np
import pytest

from app.analysis.events import event_trace, group_branches
from app.disputes.forecast import _S, DisputePath, Forecaster, _Walk, path_mask

# part1840009.pkl, trajectory 145, from var/diag/keyerr-diag.json. Keep the
# actual steps, not the 9.6 MB part or its unrelated probability expressions.
SAVED = (
    ('settle', 'I0', 'no'),
    ('verdict', 'I0', 'award:6752641200:2810220200:top'),
    ('judgment_response', 'entry', '@2=none'),
    ('post_trial_motions', '', 'yes'),
    ('settle', 'I1', 'no'),
    ('execute_pre_ruling', 'I1', 'yes'),
    ('stay', 'I1', 'yes'),
    ('registration_early', 'I1', 'yes'),
    ('listing', '', 'compliant'),
    ('cash_floor', '1', '@2=initiate_offering'),
    ('offering', 'floor1', 'no'),
    ('cash_out', '', '@2=initiate_offering'),
    ('offering', 'cash_out', 'yes'),
    ('judgment_response', 'I1', '@2=initiate_offering'),
    ('offering', 'I1', 'yes'),
    ('judgment_response', 'ripe', '@2=initiate_offering'),
    ('offering', 'ripe', 'yes'),
    ('judgment_default', 'I1', 'holders_file'),
)


@pytest.mark.parametrize('holder_answer', ['holders_file', 'accelerated', 'no'])
def test_saved_floor_failure_is_rewalked_with_feasible_answer_domains(monkeypatch, holder_answer):
    d = fx.pending(instance_id='dispute_002')
    fc = Forecaster([d], {}, borrower='B', review=fx.REVIEW, horizon=fx.setup().horizon,
                    hydrate=lambda f: {}, model=fx.model(), setup=fx.setup(), basis=fx.basis())
    walk = _Walk(fc, d)
    row = 145
    saved = SAVED[:-1] + (('judgment_default', 'I1', holder_answer),)
    old = event_trace(d, DisputePath(d.instance_id, saved, '', ()), fc.setup, fc.m, fc.draws, fc.sens)
    floor = old.questions[9]
    assert old.questions[15]['day'][row] == 96
    assert any(init[row] == 96 and close[row] == 101 and closed[row]
               for init, close, closed in floor['sit']['offerings'])
    assert floor['day'][row] == 155
    assert floor['sit']['ledger'][row] == 0
    assert floor['groups'][row] == 0  # not a snapshot taken after this decision's answer
    # The saved petition_cash_out findings (day 161, group 0) came from a stale stay: sized at $0 before the day-96
    # offering's day-101 proceeds were booked, so the day-161 writ levied $3.47M, then re-sized effective from 148.
    # Sized on the dated balance ($3.09M on day 148, $370k above the need posted), the stay is effective from 148,
    # the day-161 writ is stayed and takes nothing, and cash never runs out inside the horizon.
    stay = old.stays[6]
    assert stay['day'][row] == 148 and stay['cash'][row] == 309_260_877 and stay['stay_offer'][row] == 37_030_653
    assert old.events.lock[row, 148] == 37_030_653
    assert not old.events.kinds['levy'][row].any()
    if holder_answer != 'holders_file':
        cash_out = old.questions[11]
        assert cash_out['day'][row] >= fc.days
        assert cash_out['groups'][row] == -1
    assert 'initiate_offering' not in group_branches('cash_out', 0)

    # Rewalk every answer from the last prefix before the faulty scheduling,
    # without probabilities, model calls, or discarding inconvenient branches.
    walk._population = np.arange(fc.draws.n) == row
    state = _S(steps=SAVED[:8], cls='award6752641200', a4='seek', stayed=True, early=True)
    offered = set()
    emit = walk.emit

    def checked_emit(state, outcome):
        emit(state, outcome)
        path = walk.out[-1]
        for decision in (('judgment_response', 'ripe'), ('judgment_default', 'I1')):
            assert sum(step[:2] == decision for step in path.steps) <= 1
        for key, tags, _ in path.classes:
            for edge, answer in path.edges:
                if edge == key:
                    for tag in tags:
                        assert answer in fc.nodes[fc.class_key(key, tag)].branches
        tr = event_trace(d, path, fc.setup, fc.m, fc.draws, fc.sens, rows=walk._rows(path.steps))
        mask = path_mask(path, fc.draws.n)
        assert mask[row]
        for i, (node, _, branch) in enumerate(path.steps):
            if i < 8 or node not in ('cash_floor', 'cash_out', 'judgment_response'):
                continue
            question = tr.questions[i]
            group = int(question['groups'][row])
            if question['day'][row] >= fc.days or group < 0:
                continue
            answer = branch.split('=')[-1]
            assert answer in group_branches(node, group), (path.steps, i, group)
            offered.add((node, answer))

    monkeypatch.setattr(walk, 'emit', checked_emit)
    walk.a4_i1(state)
    assert walk.out
    # Both feasible stress and continued-operation branches remain in the walk.
    assert {('cash_floor', 'file'), ('cash_floor', 'neither'),
            ('judgment_response', 'initiate_offering'), ('judgment_response', 'none')} <= offered


def test_distress_chooses_earliest_per_draw_not_floor_first(monkeypatch):
    walk = _Walk.__new__(_Walk)
    walk.N, walk.fc = 180, SimpleNamespace(draws=SimpleNamespace(n=4))
    monkeypatch.setattr(walk, 'mask_of', lambda steps: np.array([True, True, True, False]))
    days = {'cash_floor': np.array([120, 80, 90, 90]), 'cash_out': np.array([100, 100, 90, 80])}
    monkeypatch.setattr(walk, '_trace', lambda steps: SimpleNamespace(
        day=[days[steps[-1][0]]], petition=np.full(4, -1)))
    candidates, pick = walk._next_distress(_S())
    assert [c[0] for c in candidates] == ['cash_floor', 'cash_out']
    np.testing.assert_array_equal(pick, [1, 0, 0, -1])  # ties keep the engine's walk order
