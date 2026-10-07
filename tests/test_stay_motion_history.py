"""A pending motion exists before its approval or denial, independent of that answer."""
from dataclasses import replace
from unittest.mock import patch

import numpy as np
import pytest
from test_decision_snapshots import AWARD, case, trace  # noqa: F401

from app.analysis.events import BIG, Chain, _same_state, event_trace
from app.disputes.forecast import _S, DisputePath, Dist, _Prefix, _Walk, as_of, path_probability

SAVED_PREFIX = (('settle', 'I0', 'no'), ('verdict', 'I0', 'award:1065000100:1000000000:1130000200'),
                ('judgment_response', 'entry', '@3=none'), ('post_trial_motions', '', 'yes'),
                ('settle', 'I1', 'no'), ('execute_pre_ruling', 'I1', 'no'),
                ('judgment_response', 'ripe', '@2=none'), ('judgment_default', 'I1', 'no'),
                ('post_trial_ruling', '', 'reduced:500000000:0:1000000000'),
                ('cash_floor', '1', '@2=initiate_offering'), ('offering', 'floor1', 'yes'),
                ('settle', 'I2', '@1=no'), ('appeal', '', 'yes'))


def test_status_classification_follows_question_consumers(case):  # noqa: F811
    from test_appeal_state import row

    fc, dispute = case
    facts = row([112, 112])
    facts['sit']['stay_status'] = np.array(['pending', 'denied'])
    checked = 0
    for name in fc.spec:
        key = fc.node(dispute, name)
        node = fc.nodes[key]
        tags = fc.question_class(node, facts)
        if fc.uses_stay_status(node):
            assert tags[0] != tags[1], name
            with pytest.raises(ValueError, match='requires a dated stay-status snapshot'):
                fc.question_class(node, {**facts, 'sit': {k: v for k, v in facts['sit'].items()
                                                         if k != 'stay_status'}})
            checked += 1
        else:
            assert tags[0] == tags[1], name
    assert checked > 0


@pytest.mark.parametrize('phase', ['I1', 'post'])
def test_denied_motion_has_no_approval_financial_effect(case, phase):  # noqa: F811
    prefix = AWARD + (('post_trial_motions', '', 'yes'),)
    if phase == 'post':
        prefix += (('post_trial_ruling', '', 'unchanged'), ('appeal', '', 'yes'))
    no = trace(case, prefix + (('stay', phase, 'no'),))
    denied = trace(case, prefix + (('stay', phase, 'denied'),))
    for name in ('cash', 'lock', 'capacity', 'petition', 'kinds', 'incurred', 'proceeds'):
        assert _same_state(getattr(no.events, name), getattr(denied.events, name)), name
    for name in ('day', 'cash', 'owed', 'collateral', 'groups'):
        assert _same_state(getattr(no, name), getattr(denied, name)), name
    assert (no.marks['stay_moved'] == BIG).all()
    assert (denied.marks['stay_moved'] < BIG).any()
    assert (denied.marks['stayed'] == BIG).all()
    yes = trace(case, prefix + (('stay', phase, 'yes'),))
    # Approval and denial read the same dated court situation before either answer.
    assert _same_state(yes.questions[len(prefix)], denied.questions[len(prefix)])


@pytest.mark.parametrize('phase', ['I1', 'post'])
def test_motion_answers_keep_denial_distinct_from_no_motion(phase):
    walk = _Walk.__new__(_Walk)
    walk.pend = True
    out = {}
    with patch.object(walk, 'take', side_effect=lambda s, step, edge, keys: s.add(step, edge)), \
            patch.object(walk, 'stay_court', side_effect=lambda s, key, ctx: replace(s, late=((key, 0),))):
        walk.stay_answers(_S(), phase, 'motion', 'approval',
                          lambda s: out.update(yes=s), lambda s: out.update(denied=s),
                          lambda s: out.update(no=s))
    assert out['no'].edges == (('motion', 'no'),)
    assert out['no'].late == ()
    assert out['denied'].steps[-1] == ('stay', phase, 'denied')
    assert out['denied'].late == out['yes'].late == (('approval', 0),)
    d = Dist({'motion': {'yes': .7, 'no': .3}, 'approval': {'yes': .4, 'no': .6}})
    assert sum(path_probability(s.edges, d) for s in out.values()) == pytest.approx(1)


def test_intervening_enforcement_composes_before_approval_on_actual_failed_probabilities():
    # Saved draw47: levy128 precedes approval150. The old composite chose the
    # earlier enforcement question using the later approval answer.
    trials = ((.8332420094826863, .3785375106678097, .08078894257888997,
               .0004616397665811767, .3124233070550667),
              (.13958313039509557, .49170030181959906, .7897548938603377,
               .000788277771086627, .07639640137630971))
    old = []
    for motion, approval_without_levy, approval_after_levy, pending, no_motion in trials:
        old.append(motion * approval_after_levy * pending
                   + motion * approval_without_levy * (1-pending)
                   + (1-motion * approval_after_levy) * no_motion
                   + (1-motion * approval_without_levy) * (1-no_motion))
        weights = ((1-motion) * no_motion, (1-motion) * (1-no_motion),
                   motion * pending * approval_after_levy, motion * pending * (1-approval_after_levy),
                   motion * (1-pending) * approval_without_levy,
                   motion * (1-pending) * (1-approval_without_levy))
        np.testing.assert_allclose(sum(weights), 1, atol=1e-14, rtol=0)
    assert old[0] > 1 and old[1] < 1


def test_denied_post_route_keeps_pending_enforcement_and_does_not_enumerate_it_twice():
    walk = _Walk.__new__(_Walk)
    walk.pend = True
    calls = []
    walk.levy_first = lambda s: True
    walk.ripe_post = lambda s: None
    walk.settle = lambda s, phase, then: calls.append(('settle', phase))
    def enforce(s, then, pending=False, i3=False):
        calls.append(('enforce', pending, i3))
        then(s)
    walk.enforce = enforce
    walk.i3(_S(steps=(('stay', 'post', 'denied'),)), pending=True)
    assert calls == [('enforce', True, True)]


def test_saved_preapproval_enforcement_reads_motion_not_future_court_answer(case):  # noqa: F811
    fc, dispute = case
    walk = _Walk(fc, dispute)
    rows, contexts = {}, {}
    for answer in ('no', 'denied', 'yes'):
        steps = SAVED_PREFIX + (('stay', 'post', answer), ('enforce', 'post', 'none'))
        tr = event_trace(dispute, DisputePath(dispute.instance_id, steps, '', ()),
                         fc.setup, fc.m, fc.draws, fc.sens, day_only=True)
        row = rows[answer] = as_of(fc.row_of(_Prefix.of(tr)))
        contexts[answer] = walk.contexts(_S(), (), row, walk.situation_conditions('enforce_after_final'))
    draw = 47  # Saved failed family: enforcement day90, levy128, court150.
    assert rows['yes']['day'][draw] == rows['denied']['day'][draw] == 90
    for key in ('cash', 'owed'):
        assert rows['yes'][key][draw] == rows['denied'][key][draw]
    assert rows['yes']['sit']['stay_status'][draw] == rows['denied']['sit']['stay_status'][draw] == 'pending'
    assert contexts['yes'][draw] == contexts['denied'][draw] != contexts['no'][draw]
    assert rows['denied']['marks']['stay_denied'][draw] == BIG


def test_court_identity_uses_completed_before_court_row_after_intervening_levy(case):  # noqa: F811
    fc, dispute = case
    walk = _Walk(fc, dispute)
    before = _S(steps=SAVED_PREFIX, cls='reduced500000000')
    key = walk.node('stay_approved', 'post', before.cls, s=before, probe=('stay', 'post', 'no'))
    assert fc.canon_get(key, SAVED_PREFIX) is None
    answers = {}
    for enforcement in ('none', 'levy'):
        for answer in ('yes', 'denied'):
            steps = SAVED_PREFIX + (('stay', 'post', answer), ('enforce', 'post', enforcement))
            tr = trace(case, steps)
            row = as_of(tr.questions[len(SAVED_PREFIX)])
            expected = fc.dated_class(dispute, key, steps, row)
            late = fc.record_late(dispute, steps, ((key, len(SAVED_PREFIX)),), tr=tr)
            np.testing.assert_array_equal(late[key], expected)
            entries = walk._classes_of(((key, 'yes'),), steps, None, late)
            assert entries
            answers[enforcement, answer] = expected
            if enforcement == 'none':
                assert '.staypending' in expected[47]
                assert row['day'][47] == 150
            else:
                assert row['owed'][47] == 0
                assert expected[47] == ''
        supported = walk.mask_of(SAVED_PREFIX)
        np.testing.assert_array_equal(answers[enforcement, 'yes'][supported],
                                      answers[enforcement, 'denied'][supported])


def test_earlier_listing_is_a_prerequisite_for_saved_response_options(case):  # noqa: F811
    fc, dispute = case
    walk = _Walk(fc, dispute)
    steps = SAVED_PREFIX[:-1] + (('appeal', '', 'no'), ('stay', 'post', 'no'), ('enforce', 'post', 'levy'))
    probe = ('judgment_response', 'post', 'none')
    before = trace(case, steps + (probe,))
    listed = steps + (('listing', '', 'suspended'),)
    after = trace(case, listed + (probe,))
    assert before.day[-1][36] == after.day[-1][36] == 173
    assert before.groups[len(steps)][36] == 2
    assert after.groups[len(listed)][36] == 0
    selected = np.zeros(fc.draws.n, dtype=bool)
    selected[36] = True
    state = _S(steps=steps, k=2)
    def resume(s):
        pass
    with patch.object(walk, 'mask_of', return_value=selected), patch.object(walk, 'listing') as listing:
        assert walk._first_listing(state, probe, resume)
        listing.assert_called_once_with(state, '', resume, defer_delisting=True)
        listing.reset_mock()
        listed_state = replace(state, steps=listed)
        with patch.object(walk, 'delisting') as holders:
            assert walk._first_listing(listed_state, probe, resume)
            holders.assert_called_once_with(listed_state, 'delisted_suspension', 170, '', resume)
        listing.assert_not_called()
        resolved = replace(state, steps=listed + (('delisting_notes', 'delisted_suspension', 'none'),))
        assert not walk._first_listing(resolved, probe, resume)


def test_listing_prerequisite_uses_motion_date_not_future_security_read(case):  # noqa: F811
    fc, dispute = case
    walk = _Walk(fc, dispute)
    def prefix(day, read):
        return _Prefix(day=[np.array([day])], cash=[], owed=[], collateral=[], petition=np.array([-1]),
                       digest=None, reads=np.array([read]))
    with patch.object(walk, '_trace', side_effect=[prefix(165, 165), prefix(90, 180)]), \
            patch.object(walk, 'listing') as listing:
        assert not walk._first_listing(_S(), ('stay', 'post', 'no'), lambda s: None)
        listing.assert_not_called()


@pytest.mark.parametrize('name,day', [('bid_compliance', 159), ('hearing_request', 167)])
def test_completed_listing_boundary_retains_earlier_response_and_excludes_own_answer(case, name, day):  # noqa: F811
    from app.analysis.events import event_questions
    from tools.question_refresh import question_source

    fc, dispute = case
    prefix = SAVED_PREFIX[:-1] + (('appeal', '', 'no'), ('stay', 'post', 'no'), ('enforce', 'post', 'levy'))
    key = fc.node(dispute, name, 'deadline' if name == 'bid_compliance' else 'determination')
    mask = np.zeros(fc.draws.n, dtype=bool)
    mask[22] = True
    rows = []
    for answer in ('compliant', 'hearing', 'suspended'):
        steps = prefix + (('listing', '', answer), ('judgment_response', 'post', '@2=initiate_offering'),
                          ('offering', 'post', 'yes'))
        i = len(prefix)
        assert question_source(fc.nodes[key], {('listing', ''): i}) == (i, 'dated')
        completed = fc.completed_probe(key, steps, i)
        q, _ = event_questions(dispute, DisputePath(dispute.instance_id, completed, '', ()), fc.setup,
                               fc.m, fc.draws, fc.sens, indices=(-1,), day_only=True,
                               rows=tuple(mask for _ in completed))
        row = as_of(q[-1])
        assert row['day'][22] == day
        assert row['sit']['listing'][22] == 'listed'
        assert any(init[22] < day and close[22] < day and closed[22]
                   for init, close, closed in row['sit']['offerings'])
        got = fc.record_late(dispute, steps, ((key, i),), mask)
        np.testing.assert_array_equal(got[key], fc.dated_class(dispute, key, steps,
                                      {**row, 'day': np.where(mask, row['day'], BIG)}))
        rows.append(row)
    assert _same_state(rows[0], rows[1]) and _same_state(rows[1], rows[2])


def test_completed_holders_declaration_excludes_own_acceleration_but_keeps_other_occasion(case):  # noqa: F811
    from app.analysis.events import event_questions

    fc, dispute = case
    key = fc.node(dispute, 'holders_act_delisting', 'delisted_suspension')
    prefix = AWARD + (('post_trial_motions', '', 'yes'), ('judgment_default', 'I1', 'no'),
                      ('listing', '', 'suspended'))
    def snapshot(steps):
        completed = fc.completed_probe(key, steps, len(prefix))
        q, _ = event_questions(dispute, DisputePath(dispute.instance_id, completed, '', ()), fc.setup,
                               fc.m, fc.draws, fc.sens, indices=(-1,), day_only=True)
        return as_of(q[-1])
    rows = [snapshot(prefix + (('delisting_notes', 'delisted_suspension', b),))
            for b in ('none', 'accelerated', 'petition_delist', 'petition_delist_holders')]
    for row in rows[1:]:
        assert _same_state(row, rows[0])
    live = rows[0]['day'] < fc.days
    assert live.any()
    assert (rows[0]['sit']['notes_due_day'][live] == BIG).all()
    earlier = prefix[:2] + (('judgment_default', 'I1', 'accelerated'),) + prefix[3:]
    row = snapshot(earlier + (('delisting_notes', 'delisted_suspension', 'accelerated'),))
    assert (row['sit']['notes_due_day'] < 170).any()


def test_new_motion_is_pending_after_an_earlier_denial_and_own_denial_is_not_conditioning(case):  # noqa: F811
    fc, dispute = case
    steps = AWARD + (('post_trial_motions', '', 'yes'), ('stay', 'I1', 'denied'),
                     ('post_trial_ruling', '', 'unchanged'), ('appeal', '', 'yes'), ('stay', 'post', 'denied'))
    chain = Chain(dispute, fc.setup, fc.m, fc.draws, fc.sens)
    result = chain.run(steps)
    first, second = list(chain.stays.values())
    day = np.maximum(second['motion'], first['approval'] + 1)
    on = (day < second['approval']) & (day < fc.days) & (chain.owed_at(day) > 0)
    assert on.any()
    assert (chain.c_situation(day)['stay_status'][on] == 'pending').all()
    after = second['approval'] + 1
    on = (after < fc.days) & (chain.owed_at(after) > 0)
    assert on.any()
    assert (chain.c_situation(after)['stay_status'][on] == 'denied').all()
    own = as_of(result.questions[len(steps)-1])
    on = (own['day'] < fc.days) & (own['owed'] > 0)
    assert (own['sit']['stay_status'][on] == 'pending').all()
    prior = np.where((first['approval'] <= own['day']) & (first['approval'] < fc.days), first['approval'], BIG)
    np.testing.assert_array_equal(own['marks']['stay_denied'][on], prior[on])


@pytest.mark.parametrize('court_offset', [0, 15])
def test_settlement_terms_exclude_future_court_but_actual_lock_remains_until_release(case, court_offset):  # noqa: F811
    fc, dispute = case
    results = {}
    for answer in ('yes', 'denied'):
        for agreed in (False, True):
            chain = Chain(dispute, fc.setup, fc.m, fc.draws, fc.sens)
            chain.run(AWARD + (('post_trial_motions', '', 'yes'),
                               ('post_trial_ruling', '', 'unchanged'), ('appeal', '', 'yes'),
                               ('stay', 'post', answer)))
            approval = list(chain.stays.values())[-1]['approval'].copy()
            start = approval - 30 + court_offset
            pricing = start + 30
            chain.settle(start, np.full(chain.n, chain.N - 1), agreed)
            valid = pricing < chain.N
            assert (chain.reads[valid] >= pricing[valid]).all()
            results[answer, agreed] = chain, approval, pricing
    baseline = results['denied', False][0].settle_offer
    assert (baseline > 0).any()
    for chain, _, _ in results.values():
        np.testing.assert_array_equal(chain.settle_offer, baseline)
    accepted, approval, pricing = results['yes', True]
    refused = results['yes', False][0]
    live = (baseline > 0) & (pricing < accepted.N) & (approval >= 0)
    assert live.any()
    accepted_lock = np.cumsum(accepted.ev.lock, axis=1)
    refused_lock = np.cumsum(refused.ev.lock, axis=1)
    rows = np.flatnonzero(live)
    assert (accepted_lock[rows, pricing[live]] == 0).all()
    if court_offset:
        np.testing.assert_array_equal(accepted_lock[rows, approval[live]],
                                      refused_lock[rows, approval[live]])
        assert (accepted_lock[rows, approval[live]] > 0).any()
    else:
        assert (refused_lock[rows, pricing[live]] > 0).any()


def test_inactive_deferred_question_keeps_original_domains_without_conditioning_rows(case):  # noqa: F811
    from test_appeal_state import row

    from app.analysis.events import group_branches

    fc, dispute = case
    walk = _Walk(fc, dispute)
    original = np.resize(np.array([0, 2, 0, 2]), fc.draws.n)
    state = _S(steps=AWARD)
    with patch.object(walk, 'situation', return_value=()):
        key = walk.node('petition_cash_out', s=state, probe=('cash_out', '', 'neither'), groups=original)
    domain = fc.canon_get(key, state.steps).copy()
    active = np.full(fc.draws.n, '', dtype=object)
    active[2:4] = ['#actual.g2', '#actual.g0']
    mask = np.arange(fc.draws.n) < 4
    entries = walk._classes_of(((key, 'neither'),), state.steps, mask, {key: active})
    _, tags, encoded = entries[0]
    decoded = [tags[i] for i in np.frombuffer(encoded, dtype=np.int8)]
    assert decoded == ['#-.g0', '#-.g2', '#actual.g2', '#actual.g0']
    for group in (0, 2):
        inactive_key = f'{key}|#-.g{group}'
        assert fc.nodes[inactive_key].branches == group_branches('petition_cash_out', group)
        assert inactive_key not in fc.facts
    np.testing.assert_array_equal(active[2:4], ['#actual.g2', '#actual.g0'])
    fc._rec_at = state.steps + (('cash_out', '', 'neither'),)
    with patch.object(fc, 'row_of', return_value=row([100] * fc.draws.n)), \
            patch.object(fc, '_split', return_value=active) as split:
        fc.record((key,), object())
    assert split.call_args.args[3] is None  # domain-only tags are never presented as conditioning facts
    np.testing.assert_array_equal(fc.canon_get(key, state.steps), domain)


def test_prospective_settlement_retains_waiting_read_dependencies(case):  # noqa: F811
    from app.analysis.events import Trace

    fc, dispute = case
    chain = Chain(dispute, fc.setup, fc.m, fc.draws, fc.sens)
    chain.run(AWARD + (('post_trial_motions', '', 'yes'), ('post_trial_ruling', '', 'unchanged'),
                       ('appeal', '', 'yes'), ('stay', 'post', 'yes')))
    chain.advance(Trace(chain.ev), 'cash_floor', '1', 'neither')
    approval = list(chain.stays.values())[-1]['approval']
    start, pricing = approval - 15, approval + 15
    before = {k: v.copy() for k, v in chain.__dict__.get('_vfired', {}).items()}
    observed = []
    seen_at = Chain.seen_at

    def read(self, day, levy=False, **kw):
        view = seen_at(self, day, levy, **kw)
        if '_prospective_before' in self.__dict__ and np.array_equal(day, pricing):
            observed.append((self.reads.copy(), {k: v.copy() for k, v in self._vfired.items()}))
        return view

    with patch.object(Chain, 'seen_at', read):
        chain.settle(start, np.full(chain.n, chain.N - 1), False)
    assert len(observed) == 1
    horizon, fired = observed[0]
    assert any((days < BIG).any() for days in fired.values())
    assert (chain.reads >= horizon).all()
    for index, days in fired.items():
        np.testing.assert_array_equal(chain._vfired[index], np.minimum(before.get(index, BIG), days))


def test_distress_order_uses_decision_day_and_preserves_later_cash_out(case):  # noqa: F811
    fc, dispute = case
    walk = _Walk(fc, dispute)
    steps = SAVED_PREFIX[:8] + (('listing', '', 'compliant'),) + SAVED_PREFIX[8:-1] + (
        ('appeal', '', 'no'), ('stay', 'post', 'denied'), ('enforce', 'post', 'levy'))
    state = _S(steps=steps, k=2)
    probe = ('settle', 'I3', 'no')
    before = walk._trace(steps + (probe,))
    assert before.day[-1][5] == 139
    assert before.reads[5] == 169
    with patch.object(walk, 'ask_distress') as ask:
        assert not walk._first_distress(state, probe, lambda s: None)
        ask.assert_not_called()
    later = steps + (probe, ('judgment_response', 'post', '@2=initiate_offering'),
                     ('offering', 'post', 'no'))
    rows = []
    for answer in ('file', 'neither', 'initiate_offering'):
        result = trace(case, later + (('cash_out', '', answer),))
        row = result.questions[len(later)]
        rows.append(row)
        assert row['day'][5] == 147
        assert row['cash'][5] == 36922137
        assert row['groups'][5] == 2
    for row in rows[1:]:
        assert _same_state(row, rows[0])


def test_mixed_distress_order_scopes_and_restores_population(case):  # noqa: F811
    from types import SimpleNamespace

    from tools.notes_resume import Continuation

    fc, dispute = case
    walk = _Walk(fc, dispute)
    n = fc.draws.n
    state = _S(steps=())
    probe, distress = ('settle', 'I3', 'no'), ('cash_out', '', 'neither')
    parent = np.ones(n, dtype=bool)
    parent[-1] = False
    walk._population = parent
    masks = walk._masks
    canonical, classes, at = fc._qcanon, fc._qcls, fc._rec_at
    early = np.arange(n) % 3 == 0
    dates = np.where(early, 9, np.where(np.arange(n) % 3 == 1, 10, 11))
    calls = []

    def traced(steps):
        day = np.full(n, 10) if steps[-1] == probe else dates
        return SimpleNamespace(day=[day], petition=np.full(n, -1), reads=np.full(n, 30))

    def continuation(s):
        population = walk.mask_of(s.steps)
        calls.append(('parent', population.copy()))
        assert 'scope_only' not in fc._qcanon
        assert 'scope_only' not in fc._qcls
        assert all(np.array_equal(m, population) for m in walk._rows((probe,)))

    def ask(s, candidate, outcome, then):
        assert candidate == distress
        calls.append(('distress', walk.mask_of(s.steps).copy()))
        fc._qcanon['scope_only'] = []
        fc._qcls['scope_only'] = []
        fc._rec_at = ('scoped',)

    with patch.object(walk, '_trace', side_effect=traced), \
            patch.object(walk, '_candidates', return_value=[distress]), \
            patch.object(walk, 'ask_distress', side_effect=ask):
        assert walk._first_distress(state, probe, continuation)
    assert [name for name, _ in calls] == ['distress', 'parent']
    np.testing.assert_array_equal(calls[0][1], parent & early)
    np.testing.assert_array_equal(calls[1][1], parent & ~early)
    assert not (calls[0][1] & calls[1][1]).any()
    np.testing.assert_array_equal(calls[0][1] | calls[1][1], parent)
    assert walk._population is parent and walk._masks is masks
    assert fc._qcanon is canonical and fc._qcls is classes and fc._rec_at is at

    def fail():
        fc._qcanon['scope_only'] = []
        raise RuntimeError('interrupted continuation')

    with pytest.raises(RuntimeError, match='interrupted continuation'):
        walk.scoped(parent & early, fail)
    assert walk._population is parent and walk._masks is masks
    assert fc._qcanon is canonical and 'scope_only' not in canonical

    resumed = Continuation(walk, state, lambda w, s: w.mask_of(s.steps).copy(), (), {},
                           {}, {}, None, incoming_mask=parent & early)
    np.testing.assert_array_equal(resumed.run(), parent & early)
    assert walk._population is parent and walk._masks is masks


def test_listing_preemption_stops_before_later_holder_decision(case):  # noqa: F811
    fc, dispute = case
    walk = _Walk(fc, dispute)
    state = _S(steps=SAVED_PREFIX[:8])
    ruling = SAVED_PREFIX[8]
    days = walk._trace(state.steps + (ruling,)).day[-1]
    assert days[443] == 68
    between = 264
    assert days[between] == 160
    population = np.zeros(fc.draws.n, dtype=bool)
    population[[443, between]] = True
    visited = []

    def listing(s, outcome, then, *, defer_delisting=False):
        assert defer_delisting
        visited.append(('listing', walk.mask_of(s.steps).copy()))

    with patch.object(walk, 'listing', side_effect=listing):
        walk.scoped(population, lambda: walk._first_listing(
            state, ruling, lambda s: visited.append(('parent', walk.mask_of(s.steps).copy()))))
    assert [name for name, _ in visited] == ['listing', 'parent']
    assert np.flatnonzero(visited[0][1]).tolist() == [between]
    assert np.flatnonzero(visited[1][1]).tolist() == [443]

    # Use the actual listing branch: compliance is day159, the ruling160,
    # and the holders' decision170. The suspended outcome must resume the
    # ruling without silently consuming the later holders' decision first.
    only_between = np.zeros(fc.draws.n, dtype=bool)
    only_between[between] = True
    resumed = []

    def keep_suspended(s, node, ctx, classes, groups):
        return {'suspended': classes['suspended']}

    with patch.object(walk, 'joined', side_effect=keep_suspended), \
            patch.object(walk, 'delisting') as holders:
        walk.scoped(only_between, lambda: walk._first_listing(state, ruling, resumed.append))
        holders.assert_not_called()
        assert len(resumed) == 1
        suspended = resumed[0]
        assert suspended.steps[-1] == ('listing', '', 'suspended')
        assert walk._trace(suspended.steps + (('delisting_notes', 'delisted_suspension', 'none'),)).day[-1][between] == 170
        assert not walk.scoped(only_between, lambda: walk._first_listing(suspended, ruling, resumed.append))
        floor = ('cash_floor', '1', 'neither')
        assert walk._trace(suspended.steps + (floor,)).day[-1][between] == 168
        assert not walk.scoped(only_between, lambda: walk._first_listing(suspended, floor, resumed.append))
        # When the ordinary tail reaches listing, the unresolved H2 is still
        # present and dispatched exactly once rather than lost by the D6 marker.
        walk.scoped(only_between, lambda: walk.listing(suspended, 'unresolved'))
        holders.assert_called_once_with(suspended, 'delisted_suspension', 170, 'unresolved', None)
