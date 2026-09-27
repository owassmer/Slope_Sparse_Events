"""The two views: bank data versus bank data plus the researched record. Both carry the same distress decisions and
common borrower inputs on the same operating draws; research facts enter the augmented view only. No network:
judgments are stubs."""

import json
from dataclasses import replace
from datetime import date

import numpy as np
import pytest
from akoustis_fixture import REVIEW, SETUP, SNAP, basis, judgment

from app.analysis import operating
from app.analysis.core import Analysis, EventModel
from app.analysis.events import BANK, Chain, Draws, bank_trace, coupon_terms
from app.analysis.setup import DRAWS, SEED
from app.disputes.akoustis_pre_d import NOTES
from app.disputes.forecast import Forecaster, Judgment, bank_state
from app.disputes.rules import load_model
from app.finance.bank import load_feed

BORROWER = "Akoustis Technologies, Inc."


@pytest.fixture(scope="module")
def base():
    return basis()


def forecaster(base, disputes=()):
    return Forecaster(list(disputes), {}, borrower=BORROWER, review=REVIEW, horizon=SETUP.horizon,
                      hydrate=lambda f: {}, setup=SETUP, basis=base[1])


def stub(nodes, rng) -> dict[str, Judgment]:
    out = {}
    for n in nodes.values():
        w = rng.dirichlet(np.ones(len(n.branches)))
        out[n.key] = Judgment(key=n.key, instance_id=n.instance_id, node=n.node, question_id=n.question_id,
                              event=n.event, assumptions=n.assumptions, window=n.window,
                              distribution=dict(zip(n.branches, w.tolist(), strict=True)))
    return out


def bank_model(base, rng, disputes=()) -> tuple[Forecaster, EventModel]:
    fc = forecaster(base, disputes)
    paths = fc.bank_paths()
    return fc, EventModel({}, {}, {}, [], bank_paths=paths, bank_judgments=stub(fc.bank_nodes, rng))


def test_the_bank_view_decides_at_the_cash_floor_and_its_probabilities_sum_to_one(base):
    rng = np.random.default_rng(11)
    fc, m = bank_model(base, rng)
    assert {n.node for n in fc.bank_nodes.values()} >= {"petition_cash_floor"}
    assert all(n.context.split("|")[0] == BANK for n in fc.bank_nodes.values())
    for _ in range(5):
        m = EventModel({}, {}, {}, [], bank_paths=m.bank_paths, bank_judgments=stub(fc.bank_nodes, rng))
        assert m.bank_probs().sum() == pytest.approx(1.0, abs=1e-12)
    # every operating draw falls below its 30-day need inside the period on the bank data alone
    t = fc.bank_trace((("cash_floor", "", "no"),)).day[-1]
    assert (t < (SETUP.horizon - REVIEW).days).all()


def test_with_no_dispute_the_augmented_view_is_the_bank_view(base):
    _, m = bank_model(base, np.random.default_rng(5))
    a = Analysis(load_feed(SNAP), SETUP, m)
    v = a.views()
    assert v["bank_only"] == v["event_adjusted"]
    assert 0 < v["bank_only"]["metrics"]["petition_p"] <= 1
    steps = a.attribution()
    assert steps[0]["metrics"] == steps[2]["metrics"]


def test_both_views_share_the_operating_draws(base):
    _, m = bank_model(base, np.random.default_rng(2))
    a = Analysis(load_feed(SNAP), SETUP, m)
    assert a.bank_r.limit is a.r.limit is a.line.limit  # one line, one set of draws
    i = next(i for i, c in enumerate(m.bank_combos) if c[0].outcome == "operating" and not any(
        s[2] == "yes" for s in c[0].steps))
    # no instrument and no petition: the operating path is the operating flows alone
    assert np.array_equal(a.bank_r.per_day["cash"][i], a.bank.cash.sum(axis=0))


def test_the_bank_questions_carry_bank_facts_only(base):
    fc, _ = bank_model(base, np.random.default_rng(1), disputes=[judgment()])
    for n in fc.bank_nodes.values():
        st = bank_state(fc, n)
        assert set(st["case"]) == {"as_of", "borrower"}
        assert set(st["path_facts"]) == {"decision_date", "cash_balance_at_decision",
                                         "operating_need_30_days_at_decision"}
        assert not st["evidence"] and not st["record_items"] and not st["readings"] and not st["standard"]
        text = json.dumps(st).lower()
        for word in ("judgment", "qorvo", "notes", "nasdaq", "listing", "default", "indenture"):
            assert word not in text, (n.key, word)


# The notes coupon: a common borrower input -------------------------------------------------------------------------

QUOTES = ["On June 9, 2022, Akoustis Technologies, Inc. (the \u201cCompany\u201d) issued $44.0 million aggregate principal "
          "amount of its 6.0% Convertible Senior Notes due 2027 (the \u201cNotes\u201d)",
          "The Notes bear interest at a rate of 6.0% per year until maturity on June 15, 2027",
          "is payable semi-annually in arrears on June 15 and December 15 of each year, beginning on December 15, 2022"]
DEC16 = (date(2024, 12, 16) - REVIEW).days - 1


def test_the_coupon_is_arithmetic_on_its_quote_and_an_unstated_coupon_stays_unknown(base):
    bare = NOTES.model_copy(update={"coupon_cents": None, "interest_dates": ()})
    got = coupon_terms(bare, QUOTES, REVIEW, SETUP.horizon)
    assert got.coupon_cents == 4_400_000_000 * 6 // 100 // 2 == 132_000_000
    assert got.interest_dates == (date(2024, 12, 15),)
    assert coupon_terms(bare, QUOTES[:1], REVIEW, SETUP.horizon).coupon_cents is None  # no rate period: unknown
    wrong = bare.model_copy(update={"principal_cents": 4_500_000_000})
    assert coupon_terms(wrong, QUOTES, REVIEW, SETUP.horizon).coupon_cents is None  # the quote must match
    with pytest.raises(ValueError):  # a null never becomes a $0 obligation
        bank_trace(bare, (), SETUP, load_model(), Draws(DRAWS, basis=base[1]))


def test_both_views_pay_the_same_coupon_and_none_after_a_petition(base):
    m, dr = load_model(), Draws(DRAWS, basis=base[1])
    bank = bank_trace(NOTES, (), SETUP, m, dr).events.cash
    aug = Chain(judgment(), SETUP, m, dr).run(()).events.cash
    assert (bank[:, DEC16] == -75_000_000).all() and (aug[:, DEC16] == bank[:, DEC16]).all()
    filed = bank_trace(NOTES, (("cash_floor", "", "yes"),), SETUP, m, dr)
    pet = filed.events.petition
    assert ((pet >= 0) & (pet <= DEC16)).any() and (filed.events.cash[(pet >= 0) & (pet <= DEC16), DEC16] == 0).all()


def test_the_bootstrap_does_not_replay_the_june_coupon():
    feed = load_feed(SNAP)
    assert any(t["category"] == "debt_service" and t["date"] == "2024-06-17" for t in feed.transactions)
    ops = operating.simulate(feed, (SETUP.horizon - REVIEW).days + 30, DRAWS, SEED)
    assert not ops.by_category["debt_service"].any()  # the dated coupon is event cash, booked once


# Stay security (Rule 62(b)) -----------------------------------------------------------------------------------------

def _stay(b, d, sens=None):
    c = Chain(d, SETUP, load_model(), Draws(DRAWS, basis=b), sens)
    c.step("execute_pre_ruling", "I1", "yes")
    c.step("stay", "I1", "yes")
    return c


def test_a_stay_locks_the_collateral_where_cash_covers_it_and_else_the_cash_above_need(base):
    b = base[1]
    small = judgment(stage="judgment_entered", motions=(), components=(), financing=(),
                     amount=judgment().amount.model_copy(update={"value": 200_000_000}))
    c = _stay(b, small)  # $2.0M: where cash at approval covers the collateral, the collateral is locked
    covers = (c.stayed_from < c.N) & (c.stay_offer == 0)
    assert covers.sum() > DRAWS // 2 and (c.lock_amount[covers] == c.collateral_required[covers]).all()
    assert (c.ev.lock.sum(axis=1)[covers] == c.collateral_required[covers]).all()
    c = _stay(b, judgment())  # $38.6M: the company's offer, its cash above its 30-day need on the motion day
    t = np.full(DRAWS, c.E0)
    offer = np.maximum(b.cash[np.arange(DRAWS), t] - b.need[np.arange(DRAWS), t], 0)
    assert (c.stay_offer == offer).all() and (offer > 0).all()
    inside = c.stayed_from < c.N
    rows = np.arange(DRAWS)[inside]
    assert inside.any() and (c.ev.lock[rows, c.stayed_from[inside]] == c.lock_amount[inside]).all()
    assert (c.lock_amount <= offer).all() and (c.lock_amount > 0).all()


def test_a_stay_on_zero_offered_security_is_effective_only_in_the_noncash_sensitivity(base):
    poor = replace(base[1], cash=base[1].cash - 2_000_000_000)  # no cash above need on the motion day
    c = _stay(poor, judgment())
    assert (c.stay_offer == 0).all() and (c.stayed_from >= 10**6).all() and not c.ev.lock.any()
    n = _stay(poor, judgment(), {"stay_security": "noncash"})
    assert (n.stayed_from < n.N).any() and not n.ev.lock.any()


# Settlement ---------------------------------------------------------------------------------------------------------

def test_a_settlement_exists_only_where_its_amount_is_positive(base):
    poor = replace(base[1], cash=base[1].cash - 2_000_000_000)  # nothing above need
    c = Chain(judgment(), SETUP, load_model(), Draws(DRAWS, basis=poor))
    c.step("settle", "I1", "yes")
    assert (c.settle_offer == 0).all() and (c.resolved >= 10**6).all() and (c.marks["settled"] >= 10**6).all()
    rich = Chain(judgment(), SETUP, load_model(), Draws(DRAWS, basis=base[1]))
    rich.step("settle", "I1", "yes")
    pd = (REVIEW - REVIEW).days + 29  # the review date + 30 days
    ok = rich.settle_offer > 0
    assert ok.any() and (rich.resolved[ok] == pd).all()
    assert (rich.ev.cash[ok, pd] == -rich.settle_offer[ok] - base[1].legal[ok, pd]).all()  # spend stops from pd


def test_monthly_installments_stop_at_a_petition_and_release_the_claim_on_the_last(base):
    b = replace(base[1], legal=np.zeros_like(base[1].legal))  # the installments alone
    m, sens = load_model(), {"settlement_monthly": True, "coupon_cash_share": "all_shares"}
    c = Chain(judgment(), SETUP, m, Draws(DRAWS, basis=b), sens)
    tr = c.run((("settle", "I1", "yes"),))
    pd = 29  # the review date + 30 days
    k = (c.N - pd + 29) // 30
    ok = c.settle_offer > 0
    assert ok.any() and (c.resolved[ok] == pd + 30 * (k - 1)).all()  # released when the last one is paid
    assert (-tr.events.cash[ok].sum(axis=1) == c.settle_offer[ok]).all()
    assert not np.delete(tr.events.cash, pd + 30 * np.arange(k), axis=1).any()
    ft = Chain(judgment(), SETUP, m, Draws(DRAWS, basis=b), sens).run(
        (("settle", "I1", "yes"), ("cash_floor", "", "yes")))
    pet = ft.events.petition
    cut = ok & (pet > pd) & (pet < pd + 30 * (k - 1))
    assert cut.any()
    after = np.arange(c.N)[None, :] >= pet[:, None]
    assert not ft.events.cash[cut & after.any(axis=1)][after[cut & after.any(axis=1)]].any()


def test_the_i3_settlement_starts_at_the_later_of_enforceability_and_the_appeal_deadline(base):
    c = Chain(judgment(), SETUP, load_model(), Draws(DRAWS, basis=base[1]))
    c.step("ruling", "", "beyond:3010000000:0")
    day = c.step("settle", "I3", "no")
    start = np.maximum(c.EF, c.AD)
    inside = start < c.N
    assert (day[inside] == start[inside]).all() and (c.AD > c.EF).all()
