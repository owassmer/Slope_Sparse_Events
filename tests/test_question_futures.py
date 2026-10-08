"""The checker catches future-labelled keys even when cash/facts already use as_of."""
import akoustis_20240514_fixture as fx
import numpy as np
import pytest
from question_history import question_records
from walk_order import FutureChecker

from app.disputes.forecast import _S, Forecaster, _Walk


@pytest.mark.parametrize('answer', ['no', 'denied', 'yes'])
def test_registration_collateral_excludes_later_stay(answer):
    from app.analysis.events import Chain

    fc, d, _ = sample()
    steps = (
        ('settle', 'I0', 'no'), ('verdict', 'I0', 'award:500000000:0:1000000000'),
        ('judgment_response', 'entry', '@3=initiate_offering'), ('offering', 'entry', 'yes'),
        ('settle', 'Ientry', 'no'), ('post_trial_motions', '', 'yes'), ('settle', 'I1', 'no'),
        ('execute_pre_ruling', 'I1', 'yes'), ('stay', 'I1', 'denied'),
    )

    def chain(history):
        c = Chain(d, fx.setup(), fx.model(), fc.draws)
        c.run(history)
        return c

    earlier = chain(steps)
    later = chain(steps + (('registration_early', 'I1', 'no'),
                          ('post_trial_ruling', '', 'unchanged'), ('appeal', '', 'yes'),
                          ('stay', 'post', answer)))
    day = np.array([117])
    expected = earlier.stay_facts(8)['collateral']
    assert expected[0] == 534_293_846
    np.testing.assert_array_equal(earlier.collateral_before(day), expected)
    np.testing.assert_array_equal(later.collateral_before(day), expected)
    np.testing.assert_array_equal(
        later.question_row(day, 'court_order', 'registration_I1')['collateral'], expected)
    # The later motion is excluded on its own day too, but visible afterward.
    motion = later.stays[12]['motion']
    np.testing.assert_array_equal(later.collateral_before(motion), expected)
    np.testing.assert_array_equal(later.collateral_before(motion + 1), later.stay_facts(12)['collateral'])


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
        walk.emit(state.add(step, (key, 'yes'), late=((key, len(state.steps)),)), 'unresolved')
    checker = FutureChecker(fc, d, 0, records)
    assert checker.history(walk.out[0]) == []
    assert checker.checked['registration_early'] == 1
    # The completed snapshot must still ask at the order, not the motion.
    assert fc.completed_probe(key, walk.out[0].steps, len(state.steps))[-1] == probe
    with pytest.raises(AssertionError, match='No recorded occurrence'):
        FutureChecker(fc, d, 0, {}).history(walk.out[0])


@pytest.mark.parametrize('native', [0, 127, 191, 383])
@pytest.mark.parametrize('ruling', ['unchanged', 'set_aside'])
def test_registration_uses_order_date_not_traversal_prefix(native, ruling):
    fc, d, state = sample('yes', row=native)
    walk = _Walk(fc, d)
    order = walk._trace(state.steps + (('court_order', 'registration_I1', ''),)).day[-1][0]
    ruling_day = walk._trace(state.steps + (('post_trial_ruling', '', ruling),)).day[-1][0]

    def finish(s):
        # These are visited later, but the ruling may precede the order date.
        s = s.add(('post_trial_ruling', '', ruling), None)
        s = s.add(('listing', '', 'compliant'), None)
        walk.emit(s, 'unresolved')

    walk.a4_i1 = walk.ripe_i1 = finish
    with question_records(walk) as records:
        walk.j9_i1(state)
    assert len(walk.out) == 2
    for path in walk.out:
        checker = FutureChecker(fc, d, 0, records)
        assert checker.history(path) == []
        # A judgment already set aside cannot support a live registration question.
        expected = 0 if ruling == 'set_aside' and ruling_day < order else 1
        assert checker.checked.get('registration_early', 0) == expected


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
