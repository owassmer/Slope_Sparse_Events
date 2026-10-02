import akoustis_20240514_fixture as fx
import numpy as np
import pytest

from app.analysis.events import event_trace
from app.disputes.forecast import DisputePath, Forecaster, _Walk

STEPS = (('settle', 'I0', 'no'), ('verdict', 'I0', 'award:6752641200:2810220200:top'), ('judgment_response', 'entry', '@2=none'), ('post_trial_motions', '', 'yes'), ('settle', 'I1', 'no'), ('execute_pre_ruling', 'I1', 'no'), ('judgment_response', 'ripe', '@2=none'), ('judgment_default', 'I1', 'holders_file'), ('cash_floor', '1', '@2=initiate_offering'), ('offering', 'floor1', 'no'), ('cash_out', '', '@2=file'), ('post_trial_ruling', '', 'unchanged'), ('settle', 'I2', '@1=no'), ('appeal', '', 'no'), ('stay', 'post', 'no'), ('enforce', 'post', 'levy'), ('judgment_response', 'post', 'none'))

def case():
    d = fx.pending(instance_id='dispute_002')
    fc = Forecaster([d], {}, borrower='B', review=fx.REVIEW, horizon=fx.setup().horizon,
                    hydrate=lambda f: {}, model=fx.model(), setup=fx.setup(), basis=fx.basis())
    return fc, d, _Walk(fc, d)

@pytest.mark.parametrize('answer', ['none', 'file'])
def test_same_day_later_petition_does_not_remove_response(answer):
    fc, d, w = case()
    steps = STEPS[:-1] + (('judgment_response', 'post', answer),)
    p = DisputePath(instance_id=d.instance_id, steps=steps, edges=(), outcome='')
    tr = event_trace(d, p, fc.setup, fc.m, fc.draws, fc.sens, day_only=True)
    assert tr.day[-1][49] == 103
    assert tr.late[len(steps)-1]['petition'][49] == 157
    assert tr.events.petition[49] == 103
    assert tr.groups[len(steps)-1][49] == 2
    prefix = w._trace(steps)
    assert prefix.petition[49] == 157
    assert w.walk_groups(steps)[49] == 2

@pytest.mark.parametrize('light', [False, True])
def test_before_answer_petition_survives_subset_and_light_traces(light):
    fc, d, w = case()
    mask = np.arange(fc.draws.n) == 49
    wide = fc.trace(d, STEPS, real=True, light=light)
    subset = fc.trace(d, STEPS, real=True, light=light, rows=tuple(mask for _ in STEPS))
    assert wide.petition[49] == subset.petition[49] == 157
    assert wide.day[-1][49] == subset.day[-1][49] == 103
    final = fc.trace(d, STEPS, full=True, real=True)
    assert final.petition[49] == 103
