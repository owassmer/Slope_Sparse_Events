"""Real walk-g1 history: notes occasion is not a motions-pending interval."""
import pytest
from question_history import question_records
from test_question_futures import sample

from app.analysis.walk_checks import Checker, FutureChecker
from app.disputes.forecast import _S, _Walk

STEPS = (
    ('settle', 'I0', 'no'),
    ('verdict', 'I0', 'award:2397555350:2260000400:2535110300'),
    ('judgment_response', 'entry', '@2=initiate_offering'), ('offering', 'entry', 'yes'),
    ('settle', 'Ientry', 'no'), ('post_trial_motions', '', 'no'),
    ('settle', 'I2', 'no'), ('appeal', '', 'no'), ('stay', 'post', 'no'),
    ('settle', 'I3', 'no'), ('enforce', 'post', 'levy'),
    ('cash_floor', '1', '@0=neither'), ('cash_out', '', '@0=neither'),
    ('nonpayment', '', 'due'), ('judgment_response', 'ripe', '@0=none'),
)


@pytest.mark.parametrize('context,label,passes', [
    ('I1', 'award2397555350', True),
    ('I2', 'award2397555350', False),
    ('I1', 'award9999999999', False),
])
def test_entered_judgment_default_without_motions(context, label, passes):
    fc, d, _ = sample()
    walk = _Walk(fc, d)
    state = _S(steps=STEPS, cls='award2397555350')
    probe = ('judgment_default', 'I1', 'no')
    with question_records(walk) as records:
        key = walk.node('holders_act_judgment', context, label, s=state, probe=probe)
        walk.emit(walk.take(state, probe, (key, 'no'), (key,)), 'unresolved')
    checker = FutureChecker(fc, d, 0, records)
    bad = checker.history(walk.out[0])
    assert (not bad) == passes
    assert checker.checked['holders_act_judgment'] == 1
    if bad:
        assert 'key' in bad[0]['changed']
    else:
        from app.disputes.forecast import pack_row, unpack_row

        corrupted = {}
        for identity, blobs in records.items():
            changed = []
            for blob in blobs:
                row = unpack_row(blob)
                row['cash'] = row['cash'] + 1
                changed.append(pack_row(row))
            corrupted[identity] = changed
        bad = FutureChecker(fc, d, 0, corrupted).history(walk.out[0])
        assert bad and 'facts' in bad[0]['changed']


def test_levy_response_really_is_skipped_not_a_permitted_same_day_tie():
    fc, d, _ = sample()
    state = _S(steps=STEPS[:12] + (('cash_out', '', '@0=file'),))
    bad = Checker(fc, d, 0, 0).history(state)
    assert bad == [dict(kind='skipped', decision=['judgment_response', 'post'], day=39,
                        before=['cash_floor', '1', '@0=neither'], at=39)]
    with_response = _S(steps=STEPS[:11] + (('judgment_response', 'post', '@0=none'),)
                       + state.steps[11:])
    assert Checker(fc, d, 0, 0).history(with_response) == []


def test_failure_categories_survive_example_caps_and_merge():
    from app.analysis.tree_history_checks import HistoryChecks, empty, merge

    checks = HistoryChecks.__new__(HistoryChecks)
    checks.state = empty()
    checks.state['no_skip']['examples'] = [{}] * 50
    for row in range(60):
        checks.verdict(None, row, 'no_skip', [{'kind': 'skipped',
                       'decision': ['judgment_response', 'post'],
                       'before': ['cash_floor', '1', '@0=neither']}])
    # An entirely different failure after both reporting caps must remain visible.
    checks.verdict(None, 61, 'no_skip', [{'kind': 'replay_error', 'error': 'broken'}])
    combined = merge(empty(), checks.state)
    assert sorted(combined['no_skip']['violation_counts'].values()) == [1, 60]
    assert len(combined['no_skip']['examples']) == 50
    assert combined['no_skip']['categorized_history_draws'] == 61
