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



def test_later_listing_answer_does_not_reweight_earlier_notes_facts(case):
    fc, d, w, _ = case
    key = fc.node(d, 'petition_on_notes', 'judgment_ruling')
    mask = w.mask_of(STEPS)
    first = notes.record(fc, d, STEPS, key, mask)
    assert (first != '').any()
    seen = set(fc._late_seen)
    # Both listing answers occur after this issuer decision. They must not
    # change its class or count its history twice in the representative facts.
    hearing = STEPS[:-1] + (('listing', '', 'hearing'),)
    second = notes.record(fc, d, hearing, key, mask)
    np.testing.assert_array_equal(first, second)
    assert fc._late_seen == seen


def test_strong_join_retains_distinct_pending_filing_continuations(case):
    _fc, _d, w, _key = case
    s = _S(steps=STEPS[:ORIGIN], cls='reduced1695000300')
    classes = {'yes': [[('issuer', 'yes')]], 'holders_file': [[('holders', 'yes')]]}
    result = w.joined(s, 'judgment_default', 'ruling', classes,
                      (('yes', 'holders_file'),))
    assert set(result) == {'yes', 'holders_file'}

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


def test_completed_notes_classes_conserve_probability_on_each_draw(case, monkeypatch):
    from app.disputes.forecast import expand_classes, path_mask, path_probability

    fc, d, w, _ = case
    s = _S(steps=STEPS[:ORIGIN], cls='reduced1695000300')
    monkeypatch.setattr(w, 'first', lambda *args: False)
    forks = []
    w.notes_petition(s, 'ruling', forks.append)
    for fork in forks:
        w.emit(replace(fork, steps=fork.steps + STEPS[ORIGIN + 1:]), 'unresolved')
    paths = expand_classes(w.out, fc.nodes, fc.draws.n)
    rng = np.random.default_rng(21)
    expected = w.mask_of(STEPS)
    for _ in range(10):
        dist = Dist({k: dict(zip(n.branches, rng.dirichlet(np.ones(len(n.branches))), strict=True))
                     for k, n in fc.nodes.items()})
        cover = np.zeros(fc.draws.n)
        for path in paths:
            mask = path_mask(path, fc.draws.n)
            cover += path_probability(path.edges, dist) * (1 if mask is None else mask)
        np.testing.assert_allclose(cover, expected.astype(float), atol=1e-12)


def test_ripe_filing_continues_to_an_earlier_ruling(case, monkeypatch):
    fc, d, w, _ = case
    prefix = STEPS[:14] + (('judgment_response', 'ripe', '@2=file'),)
    before = fc.trace(d, prefix, full=True, real=True)
    after = fc.trace(d, prefix + (('post_trial_ruling', '', 'set_aside'),), full=True, real=True)
    mask = w.mask_of(prefix)
    assert (before.petition[mask] >= 0).all()
    assert ((after.petition < 0) & mask).sum() == 8
    continued = []
    monkeypatch.setattr(w, 'first', lambda *args: False)
    monkeypatch.setattr(w, 'notes_petition', lambda s, *args: continued.append(s))
    monkeypatch.setattr(w, 'offer', lambda s, phase, then: then(s))
    monkeypatch.setattr(w, 'tail', lambda *args: pytest.fail('ripe filing skipped its ruling'))
    w.ripe_i1(_S(steps=STEPS[:14], cls='award2397555350', a4='seek'))
    filed = next(s for s in continued if s.steps[-1][:2] == ('judgment_response', 'ripe')
                 and s.steps[-1][2].endswith('=file'))
    assert w.mask_of(filed.steps)[mask].all()


def test_unfired_judgment_question_cannot_borrow_a_later_delisting_default(case):
    fc, d, _w, key = case
    steps = (('verdict', 'I0', 'no_award'), ('judgment_default', 'ruling', 'accelerated'),
             ('listing', '', 'suspended'), ('delisting_notes', 'delisted_suspension', 'accelerated'))
    generic = fc.trace(d, steps + (('notes_due_date', 'issuer', ''),), real=True)
    assert (generic.day[-1] < fc.days).any()
    row, _ = notes.decision_row(fc, d, steps, 1, 'issuer')
    assert not (row['day'] < fc.days).any()
    assert not fc.live(fc.nodes[key], row).any()


def test_directly_dated_delisting_origin_has_valid_before_filing_facts(case):
    fc, d, _w, _key = case
    steps = (('verdict', 'I0', 'no_award'), ('listing', '', 'suspended'),
             ('delisting_notes', 'delisted_suspension', 'petition_delist'))
    row, _ = notes.decision_row(fc, d, steps, 2, 'issuer')
    key = fc.node(d, 'petition_on_notes', 'delisting_delisted_suspension')
    live = fc.live(fc.nodes[key], row)
    assert live.any()
    np.testing.assert_array_equal(row['day'][live], row['sit']['notes_due_day'][live])
    assert (row['petition'][live] < 0).all()


def test_no_award_notes_context_does_not_invent_pending_motions(case):
    fc, d, _w, _key = case
    steps = (('verdict', 'I0', 'no_award'), ('listing', '', 'suspended'),
             ('delisting_notes', 'delisted_suspension', 'accelerated'))
    key = fc.node(d, 'petition_on_notes', 'delisting_delisted_suspension')
    cls = notes.record(fc, d, steps, key)
    for tag in set(cls) - {''}:
        state, _, _ = fc.state(fc.nodes[fc.class_key(key, tag)])
        assert 'motions are pending' not in ' '.join(state['assumed_events'])


def test_active_draw_replay_matches_full_replay_cash_and_dates(case):
    fc, d, w, _key = case
    mask = w.mask_of(STEPS)
    for actor in ('issuer', 'holders'):
        full, _ = notes.decision_row(fc, d, STEPS, ORIGIN, actor)
        selected, _ = notes.decision_row(fc, d, STEPS, ORIGIN, actor, mask)
        for key in ('day', 'cash', 'owed', 'petition', 'collateral'):
            np.testing.assert_array_equal(selected[key][mask], full[key][mask], err_msg=key)
        for section in ('sit', 'marks', 'triggers'):
            for key, value in full[section].items():
                if isinstance(value, np.ndarray) and value.shape == (fc.draws.n,):
                    np.testing.assert_array_equal(selected[section][key][mask], value[mask], err_msg=key)


def test_saved_history_rebuild_preserves_cash_path_and_replaces_notes_classes(case, tmp_path, monkeypatch):
    import gzip
    import pickle

    from app.disputes.forecast import class_entry, pack_mask
    from tools import notes_repair

    fc, d, w, key = case
    mask = w.mask_of(STEPS)
    stale = class_entry(key, np.full(fc.draws.n, '', dtype=object), mask)
    p = DisputePath(instance_id=d.instance_id, steps=STEPS, outcome='settled', edges=((key, 'no'),),
                    mask=pack_mask(mask), classes=(stale,))
    ekey, equivalence, watches, cond = (12, 0, 3, 9), (b'original cash key',), (), ()
    source = tmp_path / 'part0.pkl'
    source.write_bytes(pickle.dumps({'events': [(ekey, 'path', (p, equivalence, watches), cond)]}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(notes_repair, '_FC', fc)
    monkeypatch.setattr(notes_repair, '_CTL', {'classed': set()})
    report = notes_repair.rebuild(source)
    assert report['paths'] == 1 and report['rows'] > 0 and report['complete']
    folder = tmp_path / 'var/notes-repair/rebuilt'
    with gzip.open(folder / 'part0.pkl.gz', 'rb') as stream:
        at, (fixed, eq, watch), rows, conditional = pickle.load(stream)
    assert (at, eq, watch, conditional) == (ekey, equivalence, watches, cond)
    assert (fixed.steps, fixed.edges, fixed.mask, fixed.outcome) == (p.steps, p.edges, p.mask, p.outcome)
    assert fixed.classes != p.classes
    with gzip.open(folder / 'part0-nodes.pkl.gz', 'rb') as stream:
        meta = pickle.load(stream)
    assert key in meta['classed']
    assert all(k in meta['nodes'] for k, _late, _blob in rows)


def test_equal_finished_prefixes_can_hide_distinct_holder_continuations(case):
    fc, d, w, _ = case
    # Original raw event (10494, 0, 5020, 753) merged these alternatives.
    steps = (
        ('settle', 'I0', 'no'),
        ('verdict', 'I0', 'award:2672665250:2535110300:2810220200'),
        ('judgment_response', 'entry', '@2=initiate_offering'),
        ('offering', 'entry', 'no'),
        ('post_trial_motions', '', 'yes'),
        ('settle', 'I1', 'no'),
        ('execute_pre_ruling', 'I1', 'yes'),
        ('cash_floor', '1', '@2=file'),
        ('stay', 'I1', 'yes'),
        ('registration_early', 'I1', 'yes'),
        ('judgment_response', 'ripe', '@2=initiate_offering'),
        ('offering', 'ripe', 'no'),
        ('judgment_default', 'I1', 'no'),
        ('post_trial_ruling', '', 'reduced:2397555350:2260000400:2535110300'),
        ('judgment_default', 'ruling', 'accelerated'),
        ('settle', 'I2', 'yes'),
        ('listing', '', 'compliant'),
    )
    origin = 14
    filed = steps[:origin] + (('judgment_default', 'ruling', 'holders_file'),) + steps[origin + 1:]
    assert w._trace(steps[:origin + 1], True).digest == w._trace(filed[:origin + 1], True).digest
    mask = w.mask_of(steps)
    np.testing.assert_array_equal(mask, w.mask_of(filed))
    quiet = fc.trace(d, steps, full=True, real=True)
    holders = fc.trace(d, filed, full=True, real=True)
    changed = np.flatnonzero(mask & (quiet.petition != holders.petition))
    assert changed.tolist() == [122, 193, 408]
    np.testing.assert_array_equal(quiet.petition[changed], [168, 164, 164])
    np.testing.assert_array_equal(holders.petition[changed], [163, 163, 163])
