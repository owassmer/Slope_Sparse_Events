"""The checker catches future-labelled keys even when cash/facts already use as_of."""
import akoustis_20240514_fixture as fx
import numpy as np
import pytest
from question_history import question_records
from walk_order import FutureChecker

from app.disputes.forecast import _S, Forecaster, _Walk


def sample(motions=None, row=0):
    d = fx.pending(instance_id='dispute_002')
    fc = Forecaster([d], {}, borrower='B', review=fx.REVIEW, horizon=fx.setup().horizon,
                    hydrate=lambda _: pytest.fail('no judgment'), model=fx.model(), setup=fx.setup(), basis=fx.basis())
    if row is not None:
        fc.draws = fc.draws.sub(np.arange(fc.draws.n) == row)
    verdict = next(b for b in fc.verdict_classes(d) if b.startswith('award:'))
    steps = (('settle', 'I0', 'no'), ('verdict', 'I0', verdict), ('judgment_response', 'entry', 'none'))
    if motions is not None:
        steps += (('post_trial_motions', '', motions),)
    return fc, d, _S(steps=steps, cls='award' + verdict.split(':')[1])


@pytest.mark.parametrize('answer,interval', [('yes', 'I1'), ('no', 'I2')])
def test_settlement_cannot_borrow_its_interval_from_later_motions(answer, interval):
    fc, d, state = sample(answer)
    walk = _Walk(fc, d)
    probe = ('settle', interval, 'no')
    with question_records(walk) as records:
        key = walk.node('settlement_offer', interval, state.cls, s=state, probe=probe)
        walk.emit(walk.take(state, probe, (key, 'no'), (key,)), 'unresolved')
    checker = FutureChecker(fc, d, 0, records)
    bad = checker.history(walk.out[0])
    assert bad == []
    # Both occasions now follow the deadline decision; neither starts at entry.
    tr = walk._trace(state.steps + (probe,))
    motions_day = walk._trace(state.steps).day[-1]
    assert (tr.day[-1] >= motions_day).all()


@pytest.mark.parametrize('context,step', [
    ('I1', ('registration_early', 'I1', 'yes')),
    ('I1', ('registration_early', 'I1', 'no')),
    ('post', ('enforce', 'post', 'levy')),
    ('post', ('enforce', 'post', 'none')),
])
def test_court_order_record_matches_its_emitted_action(context, step):
    fc, d, state = sample('yes', row=1 if context == 'post' else 0)
    if context == 'post':
        state = state.add(('post_trial_ruling', '', 'unchanged'), None)
        state = state.add(('appeal', '', 'yes'), None)
    walk = _Walk(fc, d)
    probe = ('court_order', 'registration_' + context, '')
    with question_records(walk) as records:
        key = walk.node('registration_early', context, state.cls, s=state, probe=probe)
        walk.court(state, key, probe[1])
        walk.emit(walk.take(state, step, (key, 'yes')), 'unresolved')
    checker = FutureChecker(fc, d, 0, records)
    checker.history(walk.out[0])
    assert checker.checked['registration_early'] == 1
    # Matching an order must retain its recorded court-date probe for replay,
    # not substitute the emitted motion/action's date.
    assert any(identity[3] == probe for identity in checker.rebuilt_cache)
    with pytest.raises(AssertionError, match='No recorded occurrence'):
        FutureChecker(fc, d, 0, {}).history(walk.out[0])


@pytest.mark.parametrize('native', [0, 145, None])
@pytest.mark.parametrize('later', [(), (('post_trial_motions', '', 'yes'),), (('post_trial_motions', '', 'no'),)])
def test_date_local_question_keeps_native_uniforms_and_has_no_violation(native, later):
    fc, d, state = sample(row=native)
    # Entry response asks before motions. Its key must not acquire a future stage.
    walk = _Walk(fc, d)
    checked_row = 145 if native is None else 0
    walk._population = np.arange(fc.draws.n) == checked_row
    state = _S(steps=state.steps[:2], cls=state.cls)
    probe = ('judgment_response', 'entry', 'none')
    with question_records(walk) as records:
        key = walk.node('judgment_response', 'entry', state.cls, 'first', s=state, probe=probe)
        asked = walk.take(state, probe, (key, 'none'), (key,))
        for step in later:
            asked = asked.add(step, None)
        walk.emit(asked, 'unresolved')
    assert FutureChecker(fc, d, checked_row, records).history(walk.out[0]) == []
    with pytest.raises(AssertionError, match='No recorded occurrence'):
        FutureChecker(fc, d, checked_row, {}).history(walk.out[0])
