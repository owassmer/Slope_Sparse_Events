"""Numeric question identity against the Python context partition, at its limits."""
from types import SimpleNamespace

import numpy as np
import pytest

from app.disputes.forecast import _S, _Walk, situation_class
from app.exact import decode, qrecord, question_keys
from app.exact import fields as f
from app.exact import fields_record as r
from app.exact import fields_walk as w


def tables():
    conditions = tuple(('ruled', 'paid', 'settled', 'stay_moved', 'stayed', 'stay_denied') for _ in w.QNODES)
    masks = np.array([sum(1 << r.MARKS.index(c) for c in cs) for cs in conditions], np.int64)
    return question_keys.Tables('borrower_claim', ('no_award', 'judgment'),
                                (((('borrower_claim:verdict_finding|liability', 'no'),),),
                                 ((('borrower_claim:verdict_finding|liability', 'yes'),
                                   ('borrower_claim:verdict_amount|damages', 'yes')),)),
                                conditions, masks, np.zeros(len(w.QNODES), bool), np.zeros(len(w.QNODES), bool))


def row(node, ctx=0, extra=0):
    return np.array([node, ctx, w.CLS_AWARD, 125, extra, -1, 10, 0], np.int64)


@pytest.mark.parametrize(('node', 'ctx', 'extra', 'expected'), [
    (w.Q_SETTLE_OFFER, 0, 0, 'settlement_offer|I0|award125'),
    (w.Q_SETTLE_ACCEPT, 7, 0, 'settlement_accept|I3|award125'),
    (w.Q_RESPONSE, 1, 2, 'judgment_response|I1|award125|after_offer'),
    (w.Q_RESPONSE, 3, 1, 'judgment_response|ripe|award125|after_none'),
    (w.Q_HOLDERS_ACT, 2, 1, 'holders_act_judgment|ruling|award125|entered_not_acted'),
    (w.Q_PETITION_NOTES, 0, 0, 'petition_on_notes|judgment_I1'),
    (w.Q_HOLDERS_INV, 3, 0, 'holders_involuntary|delisting_delisted_suspension'),
    (w.Q_PETITION_NOTES, 4, 0, 'petition_on_notes|nonpayment'),
    (w.Q_REMITTITUR, 0, 42, 'remittitur_elected|award125|remit42'),
    (w.Q_ENFORCE, 0, 1, 'enforce_after_final|award125|appealed'),
    (w.Q_HEARING, 0, 0, 'hearing_request|determination'),
    (w.Q_FLOOR, 3, 0, 'financing_at_floor|floor3'),
])
def test_keys(node, ctx, extra, expected):
    # Raw identity never carries group or decision-day route noise.
    assert question_keys.keys(row(node, ctx, extra), tables()) == ('borrower_claim:' + expected,)


def test_verdict_union_and_selected_conjunction():
    q, t = row(w.Q_VERDICT), tables()
    assert question_keys.keys(q, t, verdict_class=0) == ('borrower_claim:verdict_finding|liability',)
    assert question_keys.keys(q, t, verdict_class=1) == question_keys.keys(q, t)
    assert len(t.verdicts[1][0]) == 2  # conjunction retained, not multiplied as independent outcomes
    with pytest.raises(ValueError, match='per-question classes'):
        question_keys.situation_class(q, np.zeros(r.NC, np.int64), t)


def test_origin_identity_is_separate_from_current_amount():
    q = row(w.Q_STAY_APPROVED)
    q[w.QCLS_KIND], q[w.QCLS_VALUE] = w.CLS_REDUCED, 42
    assert question_keys.keys(q, tables(), origin_amount='award125') == (
        'borrower_claim:stay_approved|I1|award125',)


def record_row():
    ints = np.zeros(r.NI, np.int64)
    ints[r.DAY], ints[r.PETITION] = 10, -1
    ints[r.BAND_LO], ints[r.BAND_HI] = 100, 150
    ints[r.MARK0:r.TERM0] = f.BIG
    for i in (r.NOTES_DUE_DAY, r.DEFAULT_AVAILABLE, r.DELISTED, r.NONPAYMENT_DAY,
              r.MOTIONS_FILED, r.RULING):
        ints[i] = f.BIG
    ints[r.ENTRY], ints[r.MOTIONS_DEADLINE] = 5, 20
    return ints


@pytest.mark.parametrize('bits', range(64))
@pytest.mark.parametrize('status', range(5))
def test_compiled_context_matches_python(bits, status):
    t = tables()
    q = row(w.Q_SETTLE_OFFER, 8)
    ints = record_row()
    conds = t.conditions[w.Q_SETTLE_OFFER]
    for i, c in enumerate(conds):
        ints[r.MARK0 + r.MARKS.index(c)] = 10 if bits & (1 << i) else f.BIG
    ints[r.STAY_STATUS] = status
    floats = np.zeros(r.NF)
    code = np.empty(r.NC, np.int64)
    qrecord.classify(q, ints, code, t.masks, t.appeals, t.stays)
    decoded = decode.record(ints, floats)
    # _Walk.context_class uses no chain: these are already dated facts.
    walk = object.__new__(_Walk)
    walk.pend, walk.N = True, 100
    walk.fc = SimpleNamespace(spec={'settlement_offer': {'situation': list(conds[:-1])}})
    base = situation_class(decoded, appeal=False)
    expected = walk.context_class(base, decoded, _S(cls='award125'), 'settlement_offer', ('I4', 'award125'))[0]
    assert question_keys.situation_class(q, code, t) == expected


@pytest.mark.parametrize('appeal', [False, True])
@pytest.mark.parametrize('stay', [False, True])
def test_consumption_flags_are_case_data(appeal, stay):
    t = tables()
    q = row(w.Q_EXECUTE)
    t.appeals[w.Q_EXECUTE], t.stays[w.Q_EXECUTE] = appeal, stay
    ints = record_row()
    ints[r.STAY_STATUS] = 2
    code = np.empty(r.NC, np.int64)
    qrecord.classify(q, ints, code, t.masks, t.appeals, t.stays)
    expected = situation_class(decode.record(ints, np.zeros(r.NF)), appeal=appeal, stay=stay)[0]
    assert question_keys.situation_class(q, code, t) == expected


def test_notes_condition_bits_follow_the_question_order():
    t = tables()
    q = row(w.Q_PETITION_NOTES, 0)
    ints = record_row()
    ints[r.MARK0 + r.MARKS.index('stay_moved')] = 9
    code = np.empty(r.NC, np.int64)
    qrecord.classify(q, ints, code, t.masks, t.appeals, t.stays)
    expected = situation_class(decode.record(ints, np.zeros(r.NF)), appeal=False)[0]
    expected += '.t8.ctx' + b'judgment_I1|stay_moved'.hex()
    assert question_keys.situation_class(q, code, t) == expected


def test_nonpayment_record_contains_automatic_due_not_a_petition():
    from app.analysis.events import Chain
    from app.exact import agenda, oracle, replay

    fc, d = oracle.context((0,))
    ch = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
    ch.instrument_cash()
    k, ln = replay.constants(ch, 0)
    st = replay.state(ch, 0, ln)
    scratch = replay.workspace(ch, 0, st).scratch
    before_s, before_d = st.S.copy(), st.D.copy()
    ints, floats = np.empty(r.NI, np.int64), np.empty(r.NF)
    q = row(w.Q_PETITION_NOTES, w.ORIGIN_NONPAYMENT)
    qrecord.question(st, k, scratch, q, ints, floats)
    assert ints[r.NOTES_DUE_DAY] == q[w.QDAY]
    assert r.HOW[ints[r.NOTES_DUE_HOW]] == 'automatic_j'
    assert ints[r.PETITION] == -1
    np.testing.assert_array_equal(st.S, before_s)
    np.testing.assert_array_equal(st.D, before_d)
    issuer = ints.copy()
    q[w.QN] = w.Q_HOLDERS_INV
    qrecord.question(st, k, scratch, q, ints, floats)
    np.testing.assert_array_equal(ints, issuer)
    # Reading the same boundary again cannot consume an agenda item.
    assert st.S[agenda.a.N_STEPS] == 0


def test_direct_court_security_omits_only_projected_owed():
    ints = record_row()
    ints[r.HAS_SECURITY] = 2
    ints[r.SECURITY0:r.SECURITY0 + 7] = 10, 100, 20, 130, 150, 80, 1
    decoded = decode.record(ints, np.zeros(r.NF))
    assert set(decoded['security_terms']) == {'day', 'cash', 'need', 'collateral', 'offer', 'live'}
    ints[r.HAS_SECURITY] = 1
    assert decode.record(ints, np.zeros(r.NF))['security_terms']['owed'][0] == 130
