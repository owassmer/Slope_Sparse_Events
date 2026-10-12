"""Recording-boundary excerpts from real walk-f34 histories (native draws retained)."""
import json
from pathlib import Path

import numpy as np
import pytest

from app.analysis.tree_history_checks import HistoryChecks
from app.analysis.walk_checks import FutureChecker
from app.disputes.forecast import DisputePath, Node, pack_row

FIXTURES = sorted(Path(__file__).with_name('fixtures').glob('walk_f34_*.json'))


def load(path):
    data = json.loads(path.read_text())

    def decode(x):
        if isinstance(x, dict):
            if set(x) == {'array', 'dtype'}:
                return np.array(x['array'], dtype=x['dtype'])
            return {k: decode(v) for k, v in x.items()}
        if isinstance(x, list):
            return [decode(v) for v in x]
        return x

    fc = HistoryChecks.forecaster()
    fc.draws = fc.draws.sub(np.arange(fc.draws.n) == data['native_draw'])
    fc.nodes = {k: Node(**{**n, 'assumptions': tuple(n['assumptions']), 'branches': tuple(n['branches'])})
                for k, n in data['nodes'].items()}
    key = data['key']
    if '.g' in key:
        fc.grouped.add(key.split('|#')[0])
    d = next(d for d in fc.disputes if d.instance_id == 'dispute_002')
    steps = tuple(map(tuple, data['steps']))
    p = DisputePath(d.instance_id, steps, '', ((key, 'no'),))
    identity = key, tuple(map(tuple, data['prefix'])), steps if data['completed'] else None
    return fc, d, p, identity, decode(data['facts'])


@pytest.mark.parametrize('path', FIXTURES, ids=lambda p: p.stem)
def test_real_recording_boundary(path):
    fc, d, p, identity, facts = load(path)
    checker = FutureChecker(fc, d, 0, {identity: [pack_row(facts)]})
    bad = checker.history(p)
    if '0002_168' in path.name:
        # A genuine leak: the ripe response borrows the later offering's
        # proceeds when sizing collateral on a still later stay approval date.
        assert len(bad) == 1
        assert bad[0]['recorded']['facts']['collateral'] == 294_844_679
        # $12.93M was the old approval-day projection with the later answers
        # removed. The pending requirement now uses the question's day itself,
        # before both the later levy and the offering-funded payment.
        assert bad[0]['rebuilt']['facts']['collateral'] == 1_804_744_761
    else:
        # These saved records also used approval-day collateral. Keep them
        # untouched: under date-local sizing their only stale field is collateral.
        assert len(bad) == 1
    assert bad[0]['changed'] == ['facts']
    original = bad[0]['recorded']['facts']
    rebuilt = bad[0]['rebuilt']['facts']
    assert FutureChecker.equivalent(
        {k: v for k, v in original.items() if k != 'collateral'},
        {k: v for k, v in rebuilt.items() if k != 'collateral'})


def test_real_history_rerecorded_and_later_sizing_rejected():
    from app.analysis.events import Chain

    path = next(p for p in FIXTURES if '0002_168' in p.name)
    fc, d, p, identity, old = load(path)
    key, prefix, _ = identity
    records = []
    fc.record_late(d, p.steps, ((key.split('|#')[0], len(prefix)),),
                   keep=lambda k, pre, row: records.append((k, pre, row)))
    assert len(records) == 1
    k, pre, row = records[0]
    assert k == key
    assert row['day'].tolist() == [98]
    assert row['collateral'].tolist() == [1_804_744_761]
    checker = FutureChecker(fc, d, 0, {(k, pre, p.steps): [pack_row(row)]})
    assert checker.history(p) == []
    assert checker.checked == {'judgment_response': 1}

    # The actual approval still locks on day 133 using its then-current balance.
    ch = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
    ch.run(p.steps)
    assert ch.stays[13]['approval'].tolist() == [133]
    assert ch.stay_facts(13)['collateral'].tolist() == [294_844_679]
    for history in (p.steps[:16], p.steps[:17], p.steps):
        view = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
        view.run(history)
        assert view.collateral_before(np.array([98])).tolist() == [1_804_744_761]

    planted = {**row, 'collateral': ch.stay_facts(13)['collateral']}
    bad = FutureChecker(fc, d, 0, {(k, pre, p.steps): [pack_row(planted)]}).history(p)
    assert len(bad) == 1
    assert bad[0]['changed'] == ['facts']
    assert bad[0]['rebuilt']['facts']['collateral'] == 1_804_744_761


@pytest.mark.parametrize('answer', ['yes', 'denied', 'no'])
def test_pending_requirement_does_not_read_a_later_sizing_memo(answer):
    from app.analysis.events import Chain

    path = next(p for p in FIXTURES if '0002_168' in p.name)
    fc, d, p, _, _ = load(path)
    steps = p.steps[:13] + (('stay', 'post', answer),) + p.steps[14:]
    ch = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
    ch.run(steps)
    # Approved security is computed live; a denied/probe stay is memoised.
    # Neither approval-day source belongs to the earlier question.
    assert ch.stay_facts(13)['day'].tolist() == [133]
    assert ch.collateral_before(np.array([98])).tolist() == [1_804_744_761]
    # Once that date has passed, the historical approval basis remains available.
    np.testing.assert_array_equal(ch.collateral_before(np.array([134])),
                                  ch.bond_collateral(np.array([133])))


def test_planted_future_cash_in_real_record_is_not_rounding():
    path = next(p for p in FIXTURES if 'holders_act_judgment' in p.name)
    fc, d, p, identity, facts = load(path)
    # Pretend a later receipt was already in cash on the question date.
    facts['cash'] = facts['cash'] + 1
    bad = FutureChecker(fc, d, 0, {identity: [pack_row(facts)]}).history(p)
    assert len(bad) == 1
    assert bad[0]['recorded']['facts']['cash'] == bad[0]['rebuilt']['facts']['cash'] + 1


def test_pending_log_normalization_keeps_future_initiations_and_outcomes():
    path = next(p for p in FIXTURES if '240_offering_closes' in p.name)
    fc, d, _, identity, facts = load(path)
    checker = FutureChecker(fc, d, 0, {identity: [pack_row(facts)]})
    assert checker.facts(facts)['sit']['offerings'] == [[7, 12, False]]
    # The own pending entry may be absent from a stopped replay. Neither a
    # later initiation nor an outcome of a not-yet-closed offer may disappear.
    facts['sit']['offerings'][-1] = [np.array([100]), np.array([105]), np.array([False])]
    assert checker.facts(facts)['sit']['offerings'][-1] == [100, 105, False]
    facts['sit']['offerings'][-1] = [np.array([99]), np.array([104]), np.array([True])]
    assert checker.facts(facts)['sit']['offerings'][-1] == [99, 104, True]


def test_only_price_roundoff_is_tolerated():
    same = FutureChecker.equivalent
    assert same({'sit': {'share_price': 41.44267266064611}},
                {'sit': {'share_price': 41.4426726606461}})
    assert not same({'sit': {'share_price': 41.44267266064611}},
                    {'sit': {'share_price': 41.442673}})
    assert not same({'cash': 100}, {'cash': 101})
    assert not same({'cash': None}, {'cash': 0})
