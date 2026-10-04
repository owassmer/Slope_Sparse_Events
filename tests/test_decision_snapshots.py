"""Jev's conditioning precedes the answer; reading it cannot change cash flows."""
from copy import deepcopy

import akoustis_20240514_fixture as fx
import numpy as np
import pytest
from test_notes_decision import STEPS

from app.analysis.events import BIG, Chain, Trace, _same_state, event_trace
from app.disputes.forecast import DisputePath, Forecaster
from app.disputes.state14 import Group, Situation


@pytest.fixture
def case():
    d = fx.pending(instance_id="dispute_002")
    fc = Forecaster([d], {}, borrower="B", review=fx.REVIEW, horizon=fx.setup().horizon,
                    hydrate=lambda f: {}, model=fx.model(), setup=fx.setup(), basis=fx.basis())
    return fc, d


AWARD = (("verdict", "I0", "award:2397555350:2260000400:2535110300"),)


def trace(case, steps, light=False, sens=None):
    fc, d = case
    return event_trace(d, DisputePath(instance_id=d.instance_id, steps=steps, outcome="", edges=()),
                       fc.setup, fc.m, fc.draws, sens or fc.sens, light=light)


def same_question(a, b):
    assert _same_state(a, b)


@pytest.mark.parametrize("node,ctx,prefix,answers", [
    ("judgment_response", "entry", AWARD, ("initiate_offering", "neither")),
    ("cash_floor", "1", AWARD, ("initiate_offering", "neither")),
    ("cash_out", "", STEPS[:13], ("file", "neither")),
    ("offering", "entry", AWARD + (("judgment_response", "entry", "initiate_offering"),), ("yes", "no")),
    ("appeal", "", AWARD + (("post_trial_motions", "", "yes"),
                             ("post_trial_ruling", "", "unchanged")), ("yes", "no")),
])
def test_answers_do_not_change_their_own_conditioning(case, node, ctx, prefix, answers):
    rows = [trace(case, prefix + ((node, ctx, answer),)).questions[len(prefix)] for answer in answers]
    same_question(*rows)
    row = rows[0]
    live = row["day"] < case[0].days
    assert live.any()
    if node in ("judgment_response", "cash_floor"):
        assert not row["sit"]["offering_pending"][live].any()
    if node == "cash_floor":
        need = case[0].draws.basis.need[np.arange(len(live))[live], row["day"][live]]
        assert (row["sit"]["cash_end"][live] < need).all()
    if node == "offering":
        assert (row["sit"]["offering_terms"]["shares"][live] > 0).all()
        assert (row["sit"]["offering_terms"]["net"][live] > 0).all()
    if node == "cash_out":
        assert (sum(row["sit"]["arrears"].values())[live] > 0).all()


def test_stay_approval_does_not_describe_its_own_approved_stay(case):
    prefix = AWARD + (("post_trial_motions", "", "yes"),)
    yes = trace(case, prefix + (("stay", "I1", "yes"),))
    no = trace(case, prefix + (("stay", "I1", "no"),))
    a, b = yes.questions[len(prefix)], no.questions[len(prefix)]
    same_question(a, b)
    live = a["day"] < case[0].days
    assert live.any()
    assert (a["sit"]["stayed_from"][live] > a["day"][live]).all()
    assert not np.isin(a["sit"]["standing"][live], ["stayed"]).any()


@pytest.mark.parametrize("sensitive", [False, True])
def test_delisting_deadline_uses_this_trigger_and_selected_lag(case, sensitive):
    fc, _ = case
    steps = AWARD + (("listing", "", "suspended"), ("delisting_notes", "delisted_suspension", "accelerated"))
    sens = {"holder_notice_lag_days": sensitive}
    tr = trace(case, steps, sens=sens)
    r = tr.questions[len(steps) - 1]
    from app.analysis.events import pval
    lag = int(pval(fc.m, "holder_notice_lag_days", sensitive))
    live = r["day"] < fc.days
    assert live.any()
    np.testing.assert_array_equal(r["sit"]["declaration_deadline"][live], r["day"][live] + lag)
    assert (r["sit"]["notes_due_day"][live] > r["day"][live]).all()


def test_snapshot_capture_leaves_all_financial_outputs_unchanged(case):
    for steps in (STEPS, AWARD + (("post_trial_motions", "", "yes"), ("stay", "I1", "yes"),
                                 ("cash_floor", "1", "initiate_offering"), ("offering", "floor1", "yes"))):
        a, b = trace(case, steps), trace(case, steps, light=True)
        for name in ("cash", "lock", "capacity", "petition", "kinds", "incurred", "proceeds"):
            assert _same_state(getattr(a.events, name), getattr(b.events, name)), name
        for name in ("day", "cash", "owed", "collateral", "groups"):
            assert _same_state(getattr(a, name), getattr(b, name)), name


def test_waiting_snapshot_keeps_each_firing_draw_and_copies_its_values(case):
    fc, d = case
    ch = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
    ch.capture_questions = True
    ch.instrument_cash()
    ch.advance(Trace(ch.ev), *AWARD[0])
    day = np.full(ch.n, 120, dtype=np.int64)
    first = np.arange(ch.n) % 2 == 0
    ch.capture_waiting(1, "cash_floor", "1", day, first)
    saved = deepcopy(ch.questions[1])
    ch.mark("paid", day)
    ch.capture_waiting(1, "cash_floor", "1", day + 1, ~first)
    np.testing.assert_array_equal(ch.questions[1]["day"], np.where(first, day, day + 1))
    np.testing.assert_array_equal(ch.questions[1]["marks"]["paid"][first], saved["marks"]["paid"][first])


def test_later_offering_does_not_appear_in_an_already_captured_draw():
    from app.analysis.events import _merge_question
    first = {"sit": {"offerings": []}}
    later = {"sit": {"offerings": [(np.array([20, 20]), np.array([25, 25]), np.array([False, False]))]}}
    merged = _merge_question(first, later, np.array([False, True]))
    init, close, _ = merged["sit"]["offerings"][0]
    np.testing.assert_array_equal(init, [BIG, 20])
    np.testing.assert_array_equal(close, [BIG, 25])


def test_arrears_elapsed_time_filters_absent_draws_and_does_not_claim_continuity(case):
    fc, d = case
    r = {"day": np.array([100, 100]), "cash": np.array([10, 10]), "owed": np.array([10, 10]),
         "sit": {"first_unpaid": np.array([BIG, 90]), "arrears": {"operating": np.array([0, 0])}}}
    s = Situation(fc, None, d, Group.of([r], [np.ones(2, dtype=bool)]), [], {})
    text = s.days_since_first_nonpayment()
    assert "10" in text and "no recorded nonpayment" in text
    assert "-" not in text and "continuous" not in text
    assert "every obligation" not in s.arrears()


@pytest.mark.parametrize("node,context,steps", [
    ("financing_at_floor", "floor1", AWARD + (("cash_floor", "1", "initiate_offering"),)),
    ("petition_cash_out", "", STEPS[:13] + (("cash_out", "", "file"),)),
    ("settlement_offer", "I1", AWARD + (("post_trial_motions", "", "yes"), ("settle", "I1", "yes"))),
    ("holders_act_delisting", "delisted_suspension", AWARD + (("listing", "", "suspended"),
                                                            ("delisting_notes", "delisted_suspension", "accelerated"))),
])
def test_corrected_records_render_complete_questions(case, node, context, steps):
    from app.disputes.forecast import _Prefix
    fc, d = case
    key = fc.node(d, node, context)
    tr = trace(case, steps)
    records = fc.record((key,), _Prefix.of(tr, digest=False))
    assert records
    for k, _ in records:
        st, _, _ = fc.state(fc.nodes[k])
        text = st["question"]["text"]
        assert "{" not in text and "UNBUILT" not in text
        if node == "financing_at_floor":
            assert "projected cash after scheduled payments" in text
            assert "cash_before_scheduled_payments" in st["situation"]
        if node == "settlement_offer":
            assert "settlement_pricing_cash" in st["situation"]


def test_ordinary_view_carries_the_same_complete_before_answer_record(case):
    from app.analysis.events import bank_trace
    from app.disputes.forecast import _OrdinaryWalk, _Prefix, ordinary_state
    fc, d = case
    walk = _OrdinaryWalk(fc)
    steps = (("listing", "", "suspended"), ("delisting_notes", "delisted_suspension", "accelerated"))
    tr = bank_trace(d.financing[0], steps, fc.setup, fc.m, fc.draws, fc.sens)
    key = walk.node("holders_act_delisting", "delisted_suspension")
    walk.row(key, _Prefix.of(tr, digest=False), -1)
    stored = fc.bank_facts[key][0][6]
    assert stored is not None and stored["marks"]
    live = stored["day"] < fc.days
    assert live.any()
    assert (stored["sit"]["notes_due_day"][live] > stored["day"][live]).all()
    state, _, _ = ordinary_state(fc, fc.bank_nodes[key])
    assert "not applicable" not in state["question"]["text"]
    assert any("stock was delisted on" in x for x in state["assumed_events"])


def test_motion_retains_its_cash_and_separate_approval_security_terms(case):
    fc, d = case
    steps = AWARD + (("post_trial_motions", "", "yes"), ("stay", "I1", "no"))
    tr = event_trace(d, DisputePath(d.instance_id, steps, "", ()), fc.setup, fc.m, fc.draws, fc.sens,
                     day_only=True)
    row = tr.questions[len(steps)-1]
    terms = row['security_terms']
    on = row['day'] < fc.days
    assert on.any()
    assert (terms['day'][on] > row['day'][on]).all()
    np.testing.assert_array_equal(terms['need'], fc.draws.basis.need[
        np.arange(len(on)), np.clip(terms['day'], 0, fc.days-1)])
    expected_offer = np.where(terms['live'] & (terms['cash']-terms['need'] < terms['collateral']),
                              np.maximum(terms['cash']-terms['need'], 0), 0)
    np.testing.assert_array_equal(terms['offer'], expected_offer)
    from app.disputes.forecast import lazy_row, pack_row, unpack_row
    blob = pack_row(row)
    assert _same_state(unpack_row(blob)['security_terms'], dict(lazy_row(blob)['security_terms']))
