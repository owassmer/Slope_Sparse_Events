"""Dispute model 4.0.0 chains on the Akoustis pre-D record (tests/akoustis_fixture.py): probability composition,
code timing, FRAP tolling, arithmetic removal of 'pay', statutory interest, the notes' defaults, petitions consumed by
the engine, evidence routing and the merits questions' state. No network: judgments are stubs."""

from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pytest
from akoustis_fixture import CLOSE, REVIEW, SETUP, basis, judgment

from app.analysis.engine import prepare, run
from app.analysis.events import Chain, Draws, prejudgment_interest_cents, ruling_amounts
from app.disputes.forecast import (
    Forecaster,
    Judgment,
    composite,
    distributions,
    neutral_map,
    path_probability,
)
from app.disputes.rules import load_model
from app.domain.values import usd

M = load_model()
N = (SETUP.horizon - REVIEW).days


def ix(d: date) -> int:
    return (d - REVIEW).days - 1


@pytest.fixture(scope="module")
def base():
    return basis()


@pytest.fixture(scope="module")
def full(base):
    """The Akoustis chains at D: every path, every node, built once (the pre-pass runs before any judgment)."""
    fc = Forecaster([judgment()], {}, borrower="Akoustis Technologies, Inc.", review=REVIEW, horizon=SETUP.horizon,
                    hydrate=lambda f: {"finding": f.finding_id}, setup=SETUP, basis=base[1])
    return fc, fc.all_paths()["judgment"][""]


def stub(fc: Forecaster, seed: int = 7) -> dict[str, Judgment]:
    """Arbitrary but fixed answers for every node (a stub judge)."""
    rng = np.random.default_rng(seed)
    out = {}
    for n in fc.nodes.values():
        w = rng.random(len(n.branches)) + 0.05
        out[n.key] = Judgment(key=n.key, instance_id=n.instance_id, node=n.node, question_id=n.question_id,
                              event=n.event, assumptions=n.assumptions, window=n.window,
                              distribution=dict(zip(n.branches, (w / w.sum()).tolist(), strict=True)))
    return out


def chain(base, d=None, sens=None) -> Chain:
    return Chain(d or judgment(), SETUP, M, Draws(base[1].cash.shape[0], basis=base[1]), sens)


# 1. Composition ---------------------------------------------------------------------------------------------------

def test_composition_sums_to_one_and_keeps_every_path(full):
    fc, paths = full
    assert 1_000 < len(paths) < 20_000  # thousands: collapsed by interval and amount class
    assert {n.question_id for n in fc.nodes.values()} == {s["residual_question"] for t in M["templates"].values()
                                                          for s in t["nodes"].values()
                                                          if not s.get("stress_only") and "since" not in s}  # 4.0.0 nodes
    js = stub(fc)
    for dist in (distributions(js), distributions(js, neutral_map(js))):
        assert sum(path_probability(p.edges, dist) for p in paths) == pytest.approx(1.0, abs=1e-9)
    key = next(k for k in js if ":execute_pre_ruling" in k)  # a zero answer keeps its paths (weight 0), sum still 1
    dist = distributions(js, {key: {"yes": 0.0, "no": 1.0}})
    probs = [path_probability(p.edges, dist) for p in paths]
    assert sum(probs) == pytest.approx(1.0) and any(p == 0 for p in probs) and len(probs) == len(paths)
    # a composite is the chain rule over its parts: settlement in I1 = offer x accept
    a3, q4 = (next(k for k in js if f":{n}|I1|" in k) for n in ("settlement_offer", "settlement_accept"))
    settled = composite([[(a3, "yes"), (q4, "yes")]])
    assert dist[settled]["yes"] == pytest.approx(js[a3].distribution["yes"] * js[q4].distribution["yes"])


def test_the_ruling_classes_are_the_chain_rule_over_the_merits(full):
    fc, paths = full
    js = stub(fc, seed=3)
    dist = distributions(js)
    rulings = {}
    for p in paths:
        ruling_edges = [e for e in p.edges if e[0].startswith("=") and "ts_liability_jmol" in e[0]]
        for e in ruling_edges:
            rulings[e[0]] = e
    assert sum(dist[k]["yes"] for k in rulings) == pytest.approx(1.0)  # the classes partition the outcomes
    assert {s[2].split(":")[0] for p in paths for s in p.steps if s[0] == "ruling"} == {"none", "retrial", "amt",
                                                                                            "beyond", "beyond_up"}


def test_every_merged_ruling_class_is_cash_and_date_identical_and_jev_gets_its_range(full, base):
    fc, paths = full
    b = base[1]
    merged = {c: sorted(set(m)) for c, m in fc.class_members.items() if len(set(m)) > 1}
    assert {c.split(":")[0] for c in merged} >= {"beyond", "beyond_up"}
    for c, members in merged.items():
        with_c = [p for p in paths if ("ruling", "", c) in p.steps]
        for p in with_c[:: max(1, len(with_c) // 5)][:5]:
            i = p.steps.index(("ruling", "", c))
            ref = Chain(judgment(), SETUP, M, Draws(b.cash.shape[0], basis=b)).run(p.steps)
            for total, fees in members:
                steps = p.steps[:i] + (("ruling", "", f"amt:{total}:{fees}"),) + p.steps[i + 1:]
                tr = Chain(judgment(), SETUP, M, Draws(b.cash.shape[0], basis=b)).run(steps)
                assert (tr.events.cash == ref.events.cash).all() and (tr.events.lock == ref.events.lock).all()
                assert (tr.events.petition == ref.events.petition).all()
                assert all((x == y).all() for x, y in zip(tr.day, ref.day, strict=True))
    for n in fc.nodes.values():
        label = next((x for x in n.context.split("|") if x in fc.class_range), None)
        if label and n.question_id not in fc.no_cash:
            facts = fc.path_facts(n, judgment())
            if facts.get("amount_owed_at_decision"):  # the class's range of judgments, beside the amount owed
                lo, hi = fc.class_range[label]
                assert lo != hi and facts["judgment_after_ruling"] == f"{usd(lo)} to {usd(hi)}"
                assert "p50" in facts["amount_owed_at_decision"] and "basis" in facts["amount_owed_at_decision"]
            if label not in fc.remit_classes:  # the remittitur scenario only where every outcome is remitted
                assert "remittitur_scenario" not in str(facts["components"])


def test_path_facts_pool_only_trajectories_where_the_situation_holds(full):
    """Inside the analysis period, before any petition, and for a question about an unpaid judgment, still owed."""
    fc, _ = full
    row = {"day": np.array([5, 10, 12, N + 3]), "petition": np.array([-1, 3, -1, -1]),
           "owed": np.array([100, 100, 0, 100])}
    a4 = next(n for n in fc.nodes.values() if n.node == "debtor_response")
    floor = next(n for n in fc.nodes.values() if n.node == "petition_cash_floor")
    assert fc.live(a4, row).tolist() == [True, False, False, False]
    assert fc.live(floor, row).tolist() == [True, False, True, False]


def _growing(base):
    """Cash that grows by $100k a day, so the company holds cash above its need when a stay is approved (on the
    fixture's own cash the company has burnt below offer plus need by approval, and no stay takes effect)."""
    return base[0], replace(base[1], cash=base[1].cash + np.arange(base[1].cash.shape[1])[None, :] * 10_000_000)


def test_a_levy_can_come_before_stay_approval_and_none_after_it(full, base):
    fc, paths = full
    b = _growing(base)[1]
    for ctx, lev in (("I1", ("registration_early", "I1", "yes")), ("post", ("enforce", "post", "levy"))):
        stayed = [p for p in paths if ("stay", ctx, "yes") in p.steps and lev in p.steps]
        assert stayed
        before = 0
        for p in stayed[:: max(1, len(stayed) // 8)][:8]:
            c = Chain(judgment(), SETUP, M, Draws(b.cash.shape[0], basis=b))
            c.run(p.steps)
            approved = c.stayed_from < 10**6
            for day, take in c.writs:
                assert (day[take > 0] < c.stayed_from[take > 0]).all()  # no levy on or after approval
                before += int((take[approved] > 0).sum())
        assert before > 0  # some trajectories levy before the drawn approval


def _order(c: Chain) -> np.ndarray:
    """The early registration order (J9) before the ruling: the creditor's motion + briefing + a ruling-lag draw."""
    return c.E0 + M["parameters"]["briefing_days_new_motion"]["value"] + c.dr.lag(M, c.iid, "registration_I1")


def _i1(p) -> bool:
    return any(s[:2] == ("debtor_response", "I1") for s in p.steps)


def test_the_pre_ruling_response_waits_for_the_levy_early_registration_makes_possible(full, base):
    """T1-a steps 3 and 6, T1-d: before the ruling only the levy the J9 order makes possible reaches cash, so A4 (I1)
    follows J9 yes and is dated on the levy; J9 no leaves no pre-ruling A4; no petition precedes the order."""
    fc, paths = full
    b = base[1]
    i1 = [p for p in paths if _i1(p)]
    assert i1 and any(("stay", "I1", "yes") in p.steps for p in i1)  # asked on stay-yes paths too, before approval
    for p in i1:
        j = next(i for i, s in enumerate(p.steps) if s[:2] == ("debtor_response", "I1"))
        assert p.steps[j - 1] == ("registration_early", "I1", "yes")
    assert not any(_i1(p) for p in paths if ("registration_early", "I1", "no") in p.steps)
    filed = [p for p in i1 if ("debtor_response", "I1", "file") in p.steps]
    for p in filed[:: max(1, len(filed) // 6)][:6]:
        c = Chain(judgment(), SETUP, M, Draws(b.cash.shape[0], basis=b))
        pet = c.run(p.steps).events.petition
        on = pet >= 0
        assert on.any() and (pet[on] >= _order(c)[on]).all() and (pet[on] < c.stayed_from[on]).all()


def test_a_payment_or_petition_on_the_levy_day_pre_empts_the_levy(base):
    """The $2.0M mechanics judgment with the post-trial motions pending: paths sum to one, and on the levy day the
    debtor's payment or petition comes first (a levy on day p does not reach cash after a petition on day p)."""
    b = base[1]
    d = judgment(components=(), financing=(), amount=judgment().amount.model_copy(update={"value": 200_000_000}))
    fc = Forecaster([d], {}, borrower="A", review=REVIEW, horizon=SETUP.horizon, hydrate=lambda f: {}, setup=SETUP,
                    basis=b)
    paths = fc.all_paths()[d.instance_id][""]
    js = stub(fc, seed=5)
    for dist in (distributions(js), distributions(js, neutral_map(js))):
        assert sum(path_probability(p.edges, dist) for p in paths) == pytest.approx(1.0, abs=1e-9)
    assert not any(_i1(p) for p in paths if ("registration_early", "I1", "no") in p.steps)
    pre = (("settle", "I1", "no"), ("execute_pre_ruling", "I1", "yes"), ("stay", "I1", "no"),
           ("registration_early", "I1", "yes"))
    lv = Chain(d, SETUP, M, Draws(b.cash.shape[0], basis=b))
    lv.run(pre)
    day, take = lv.writs[0]
    assert (day == _order(lv)).all() and (take > 0).any()  # levy_lag_days base 0: the levy falls on the order date
    for branch in ("pay", "file"):
        assert any(pre + (("debtor_response", "I1", branch),) == p.steps[:5] for p in paths)
        c = Chain(d, SETUP, M, Draws(b.cash.shape[0], basis=b))
        tr = c.run(pre + (("debtor_response", "I1", branch),))
        acted = (c.resolved == day) if branch == "pay" else (tr.events.petition == day)
        assert acted.any() and (c.writs[0][1][acted] == 0).all()  # the levy takes nothing where the debtor acted
        early = (take > 0) & (day < c.F)  # A4 (I1) is dated on the levy where it precedes the ruling
        assert early.any() and (tr.day[-1][early] == day[early]).all()


# 2. Timing is code --------------------------------------------------------------------------------------------------

def test_no_ruling_before_the_briefing_closes_plus_the_fastest_measured_lag(base):
    c = chain(base)
    first = ix(CLOSE) + min(M["parameters"]["ruling_lag_days"]["sample"])
    assert all((r >= first).all() for r in c.ruling.values()) and (c.F >= first).all()
    assert REVIEW + timedelta(days=int(c.F.min()) + 1) >= date(2024, 8, 8)  # never before 8 Aug on any trajectory
    stressed = Chain(judgment(), SETUP, M, Draws(base[1].cash.shape[0], stress=True, basis=base[1]))
    assert (stressed.F == first).all()  # stress places every ruling at the fastest measured lag


def test_frap_tolling_moves_the_post_ruling_windows_with_the_drawn_ruling(base):
    d = judgment()
    fees = next(m for m in d.motions if m.kind == "rule_54_fees")
    late_fees = d.model_copy(update={"motions": tuple(
        m.model_copy(update={"briefing_close": date(2024, 11, 1)}) if m is fees else m for m in d.motions)})
    for dd in (d, late_fees):  # a fee motion never tolls (no Rule 58(e) order)
        c = chain(base, dd)
        tolling = [c.ruling[m.motion_id] for m in dd.motions if m.kind != "rule_54_fees"]
        assert (c.AD == np.max(tolling, axis=0) + 30).all()
        assert (c.F == np.max([c.ruling[m.motion_id] for m in dd.motions if m.kind.startswith("rule_5")
                               and m.kind != "rule_54_fees"], axis=0)).all()
    assert (chain(base, late_fees).AD == chain(base).AD).all()
    c = chain(base)
    beyond = "beyond:3010000000:0"
    days = [c.step(*s) for s in (("execute_pre_ruling", "I1", "no"), ("ruling", "", beyond), ("appeal", "", "no"),
                                 ("settle", "I2", "no"), ("stay", "post", "no"), ("debtor_response", "post", "neither"))]
    assert all((x == c.F).all() for x in days[1:5])  # no increase: enforceable at once
    assert (days[5] >= 10**6).all()  # no levy pending: the company's response to a levy does not arise
    up = chain(base)
    up.step("ruling", "", "beyond:11292377711:1211612330")  # only the increase waits 30 days (L8(a) base)
    assert (up.EF == up.F).all() and (up.EI == up.F + 30).all()
    ripe = up.step("judgment_default", "post", "no")
    inside = ripe < 10**6
    assert (ripe[inside] == up.A[inside] + 60).all()  # 60 days from the last tolling order
    whole = chain(base, sens={"stay_restart_on_increase_days": True})  # sensitivity: the whole amount waits
    whole.step("ruling", "", "beyond:11292377711:1211612330")
    assert (whole.EF == whole.F + 30).all() and (whole.EI == whole.F + 30).all()


def test_an_increase_is_levied_only_once_its_own_stay_ends_and_the_original_at_once(base):
    rich = replace(base[1], cash=base[1].cash + 20_000_000_000)  # every trajectory can cover the whole amount
    c = Chain(judgment(), SETUP, M, Draws(rich.cash.shape[0], basis=rich))
    total = 11_292_377_711
    c.step("ruling", "", f"amt:{total}:0")
    lag = int(M["parameters"]["levy_lag_days"]["value"])
    first, second = c.F + lag, c.EI
    ok = (second < N) & (first < second)
    assert ok.any()
    c.levy(c.F)
    r = c.rows[ok]
    original = -c.ev.cash[r, first[ok]]
    assert (original <= c.entered * 1.05).all() and (original >= c.entered).all()  # the surviving amount, at once
    assert (c.ev.cash[r, second[ok]] == 0).all()  # the later writ remains queued until its date
    c.until(second + 1)
    assert (-c.ev.cash[r, second[ok]] >= total - c.entered).all()  # the increase, once enforceable
    assert (c.taken[ok] >= total).all()


def test_pay_is_removed_only_where_no_trajectory_can_fund_it(full, base):
    fc, _ = full
    a4 = [n for n in fc.nodes.values() if n.node == "debtor_response"]
    assert all("pay" not in n.branches for n in a4 if "|entered|" in n.key or "|beyond" in n.key)
    assert any("pay" in n.branches for n in a4 if "|amt" in n.key)  # the patent-only amount is payable
    rich = replace(base[1], cash=base[1].cash.copy())
    rich.cash[:] += 5_000_000_000  # every trajectory could pay the entered judgment
    fr = Forecaster([judgment()], {}, borrower="A", review=REVIEW, horizon=SETUP.horizon, hydrate=lambda f: {},
                    setup=SETUP, basis=rich)
    s = (("settle", "I1", "no"), ("execute_pre_ruling", "I1", "yes"), ("stay", "I1", "no"),
         ("registration_early", "I1", "yes"))
    probe = ("debtor_response", "I1", "neither")
    assert fr.pay_possible(judgment(), s, probe) and not fc.pay_possible(judgment(), s, probe)



# 3. Amounts and statutory interest ----------------------------------------------------------------------------------

def test_statutory_interest_is_8_percent_simple_on_surviving_compensatory_only():
    d = judgment()
    ue = 3_131_521_500
    days = (date(2024, 5, 20) - date(2021, 10, 4)).days  # from commencement (L1, Beach Mart) to entry
    expect = round(ue * 0.08 * days / 365)
    assert prejudgment_interest_cents(d, ue, M) == expect and abs(expect - 658_200_000) < 500_000  # about $6.58M
    stands = {"liability": "denied", "damages": "stands", "patent": "denied", "trebling": "denied",
              "fees": "denied", "interest": "granted"}
    a = ruling_amounts(d, stands, M)
    assert a["prejudgment_interest"] == expect and a["exemplary"] == 700_000_000  # none on the $7.0M exemplary
    no_ex = d.model_copy(update={"components": tuple(c for c in d.components if c.kind != "exemplary")})
    assert ruling_amounts(no_ex, stands, M)["prejudgment_interest"] == expect
    remit = ruling_amounts(d, {**stands, "damages": "remit", "remittitur": "accept"}, M)
    assert remit["compensatory"] == 2_310_000_000
    assert remit["prejudgment_interest"] == round(2_310_000_000 * 0.08 * days / 365)  # on the surviving amount
    trebled = ruling_amounts(d, {**stands, "trebling": "granted"}, M)
    assert trebled["compensatory"] + trebled["trebling"] == 3 * ue and trebled["exemplary"] == 0  # election (L3)
    assert trebled["prejudgment_interest"] == expect  # on the untrebled amount
    for gone in ({**stands, "damages": "new_trial"}, {**stands, "liability": "granted"}):
        g = ruling_amounts(d, gone, M)  # a new trial or JMOL takes the exemplary award and the interest (L4)
        assert g["compensatory"] == g["exemplary"] == g["prejudgment_interest"] == g["fees"] == 0
        assert g["patent"] == 27_980_800


# 4. The notes -------------------------------------------------------------------------------------------------------

AUG19 = ix(date(2024, 8, 19))  # the Rule 62(a) stay ended 19 Jun; 60 days on


def _post(c, ruling="beyond:3010000000:0"):
    for s in (("ruling", "", ruling), ("appeal", "", "no"), ("settle", "I2", "no"), ("stay", "post", "no")):
        c.step(*s)


def test_the_judgment_default_ripens_under_both_readings_unpaid_unstayed_and_noticed(base):
    # Base (both): on the judgment as entered, 60 days after the Rule 62(a) stay ended; the holders' notice and
    # acceleration bring the petition there.
    c = chain(base)
    c.step("execute_pre_ruling", "I1", "no")
    ripe = c.step("judgment_default", "I1", "yes")
    assert (ripe == AUG19).all() and (c.ev.petition == AUG19).all()
    # Where the holders did not act then, it ripens again 60 days after the last tolling order (A), on the amount
    # that survives the ruling; where they acted, it does not ripen again.
    q = chain(base)
    q.step("execute_pre_ruling", "I1", "no")
    q.step("judgment_default", "I1", "no")
    _post(q)
    ripe = q.step("judgment_default", "post", "yes")
    inside = ripe < N
    assert inside.any() and (ripe[inside] == q.A[inside] + 60).all() and (q.A >= q.F).all()
    assert (q.ev.petition[inside] == ripe[inside]).all()
    acted = chain(base)
    acted.step("execute_pre_ruling", "I1", "no")
    acted.step("judgment_default", "I1", "accelerated")
    _post(acted)
    assert (acted.step("judgment_default", "post", "no") >= 10**6).all()  # no second acceleration
    # Sensitivities: the entered judgment only, or the post-trial ruling only.
    ent = chain(base, sens={"judgment_default_reading": "entered"})
    assert (ent.step("judgment_default", "I1", "no") == AUG19).all()
    _post(ent)
    assert (ent.step("judgment_default", "post", "no") >= 10**6).all()
    post = chain(base, sens={"judgment_default_reading": "post_ruling"})
    assert (post.step("judgment_default", "I1", "no") >= 10**6).all()
    _post(post)
    assert (post.step("judgment_default", "post", "no") < N).any()
    # With no ruling step the entered amount counts (a null never becomes $0); below the threshold, no default.
    none = chain(base, sens={"judgment_default_reading": "post_ruling"})
    assert (none.step("judgment_default", "post", "no") < N).any()
    small = judgment(amount=judgment().amount.model_copy(update={"value": 900_000_000}),
                     components=())  # below the $10.0M threshold
    low = chain(base, small)
    assert (low.step("judgment_default", "I1", "yes") >= 10**6).all() and (low.ev.petition == -1).all()


def test_an_effective_stay_before_the_ripe_date_prevents_the_default(base):
    c = chain(_growing(base))
    c.step("execute_pre_ruling", "I1", "yes")
    c.step("stay", "I1", "yes")  # approval = motion + briefing + a lag draw
    early = c.stayed_from <= AUG19
    assert early.any() and (~early).any()
    ripe = c.step("judgment_default", "I1", "yes")
    assert (ripe[early] >= 10**6).all() and (c.ev.petition[early] == -1).all()
    assert (ripe[~early] == AUG19).all()


def test_the_notes_petition_questions_get_the_facts_of_the_day_each_actor_may_file(base, full):
    """H3 on the 19 Aug acceleration is dated at the earliest day §7.06 lets the holders file (acceleration + 60), the
    day the engine books their petition; A5 at the acceleration. Both pool only trajectories where the holders
    accelerated, and the holders' earliest filing date is a dated trigger."""
    fc, _ = full
    route = M["parameters"]["holder_petition_route"]["request_days"]
    c = chain(base)
    c.step("execute_pre_ruling", "I1", "no")
    c.step("judgment_default", "I1", "holders_file")
    assert (c.ev.petition == AUG19 + route).all()
    tr = chain(base).run((("execute_pre_ruling", "I1", "no"), ("judgment_default", "I1", "accelerated")))
    assert (tr.triggers["holders_petition_earliest"] == AUG19 + route).all()
    assert (chain(base).run((("execute_pre_ruling", "I1", "no"),)).triggers["holders_petition_earliest"] >= 10**6).all()
    for name, at in (("holders_involuntary", AUG19 + route), ("petition_on_notes", AUG19)):
        keys = [k for k, n in fc.nodes.items() if n.node == name and n.context.startswith("judgment_I1")]
        assert keys
        for k in keys:
            for r in fc.facts[k]:
                day = r["day"][r["day"] < N]
                assert day.size and (day == at).all()
                # only where the holders acted (a trigger that never falls is stored as absent: forecast.pack_row)
                assert (r["triggers"].get("judgment_default_ruling", np.full(1, 10**6)) >= 10**6).all()


def test_delisting_is_a_default_on_its_date_and_the_repurchase_date_is_code(base, full):
    fc, paths = full
    for cls, when in (("delisted_suspension", date(2024, 11, 1)), ("delisted_panel", date(2024, 12, 6))):
        c = chain(base)
        c.step("listing", "", cls)  # determination 22 Oct; suspension + 10 days, or the panel + 45 days (base)
        c.step("delisting_notes", cls, "petition_delist")
        assert (c.ev.petition == ix(when)).all()
    # base: the latest repurchase date falls after the horizon, so requiring it books nothing inside it
    assert not any(s[2] == "petition_repurchase" for p in paths for s in p.steps)
    early = chain(base, sens={"repurchase_date": True})
    assert early.repurchase_day(ix(date(2024, 11, 1))) == ix(date(2024, 11, 29))  # 20 business days after notice
    assert chain(base).repurchase_day(ix(date(2024, 11, 1))) > N


# 5. Petitions reach the engine --------------------------------------------------------------------------------------

def test_a_filing_path_sets_the_petition_and_the_engine_stays_the_claim(base, full):
    fc, paths = full
    feed, b = base
    from app.analysis import operating
    from app.analysis.engine import NEED_DAYS

    ops = operating.simulate(feed, N + NEED_DAYS, b.cash.shape[0], 20240819, 1.0)
    line = prepare(SETUP, ops)
    filing = [p for p in paths if ("debtor_response", "post", "file") in p.steps
              and not any(s[0] == "cash_floor" and s[2] == "yes" for s in p.steps)]
    assert filing and all(p.outcome == "petition" for p in filing)
    p = filing[0]
    c = Chain(judgment(), SETUP, M, Draws(b.cash.shape[0], basis=b))
    tr = c.run(p.steps)
    on = tr.events.petition >= 0
    at = tr.day[p.steps.index(("debtor_response", "post", "file"))]  # the levy day, before the levy
    hit = on & (at < N)  # the petition is the earliest one: another filing on the path can come first
    assert (tr.events.petition[hit] == at[hit]).any() and (tr.events.petition[hit] <= at[hit]).all()
    assert (at[hit] >= c.EF[hit]).all()
    t = run(line, feed.available_cents, tr.events)
    assert (t.petition == tr.events.petition).all() and t.stayed[on].mean() > 0
    i1 = next(q for q in paths if ("debtor_response", "I1", "file") in q.steps)
    c = Chain(judgment(), SETUP, M, Draws(b.cash.shape[0], basis=b))
    tr = c.run(i1.steps)
    on = tr.events.petition >= 0  # on the levy day the early registration order makes possible, before the ruling
    assert on.any() and (tr.events.petition[on] == tr.day[-1][on]).all() and (tr.day[-1][on] < c.F[on]).all()
    assert i1.steps[-1] == ("debtor_response", "I1", "file") and i1.outcome == "petition"  # nothing later can move cash



# 6. What Jev is given -----------------------------------------------------------------------------------------------

def _read(d):
    from app.domain.investigation import Decisive, FactorResult, FindingReading

    dec = Decisive(finding_id="f_appeal", source_date="2024-05-22", quote="we intend to appeal")
    readings = (FindingReading(finding_id="f_appeal", source_date="2024-05-22",
                               bears_on={"appeal_intent": 0.9, "debtor_resistance": 0.1}),
                FindingReading(finding_id="f_resist", source_date="2024-05-23",
                               bears_on={"debtor_resistance": 0.9, "appeal_intent": 0.1}))
    factors = (FactorResult(factor_id="appeal_intent", label="The payer's appeal intent", kind="graded",
                            aggregate="latest", distribution={"intends": 1.0}, level_label="intends", decisive=dec),
               FactorResult(factor_id="debtor_resistance", label="The payer's willingness to pay this obligation",
                            kind="graded", aggregate="latest", distribution={"resists": 1.0}, level_label="resists",
                            decisive=dec))
    return d.model_copy(update={"readings": readings, "factors": factors, "finding_ids": ("f_appeal", "f_resist")})


def test_readings_are_routed_evidence_and_merits_questions_carry_no_cash(base, full):
    from app.domain.investigation import AtomicFinding, SourceSpan

    fc0, _ = full
    d = _read(judgment())
    span = SourceSpan(source_id="s", section_id="s#1", item_id="s#1", start=0, end=1, quote="q")
    findings = {f: AtomicFinding(finding_id=f, dependency_id="dep", proposition=f, target="t", spans=(span,),
                                 status="accepted") for f in d.finding_ids}
    fc = Forecaster([d], findings, borrower="Akoustis Technologies, Inc.", review=REVIEW, horizon=SETUP.horizon,
                    hydrate=lambda f: {"finding": f.finding_id}, setup=SETUP, basis=base[1])
    fc.nodes, fc.facts = fc0.nodes, fc0.facts  # the same chains, with this dispute's readings

    def state(node):
        return fc.state(next(n for n in fc.nodes.values() if n.node == node))[0]

    appeal, stay, merits = state("appeal"), state("stay_motion"), state("ts_liability_jmol")
    assert list(appeal["readings"]) == ["The company's appeal intent"]  # A2 gets appeal intent only
    assert list(stay["readings"]) == ["The company's willingness to pay this obligation"]  # A1 gets resistance
    assert merits["readings"] == {} and "projected_available_cash_at_decision_date" not in merits["path_facts"]
    assert "cash" not in str(merits["path_facts"]).lower()  # J1-J6: no borrower cash, only the record's components
    for q in ("forecast_ts_liability_jmol", "forecast_ts_damages_ruling", "forecast_patent_jmol", "forecast_trebling",
              "forecast_fees_awarded", "forecast_prejudgment_interest"):
        from app.agent.jev import registry_question

        text = registry_question(q)["prompt"]["instructions"]
        assert "on the merits or the remedy" not in text and "briefs are sealed" in text  # the standard only
    # the company named by role; the remittitur scenario only where it is the premise; the dated judgment fact
    assert merits["case"]["company"] == "Akoustis Technologies, Inc." and "borrower" not in merits["case"]
    assert "remittitur_scenario" not in str(merits["path_facts"])
    assert "remittitur_scenario" in str(state("ts_damages_ruling")["path_facts"])
    assert "remittitur_scenario" in str(state("remittitur_accepted")["path_facts"])
    assert "Amount fixed by the court" not in state("remittitur_accepted")["readings"]
    # the court's standard for each ruling, and the motion it rules on
    assert [m["motion"] for m in merits["path_facts"]["pending_motions"]] == ["D.I. 607"]
    damages, patent, treble = state("ts_damages_ruling"), state("patent_jmol"), state("trebling")
    assert any("Williamson" in s and "Gumbs" in s for s in damages["standard"])
    assert not any("Lightning Lube" in s for s in damages["standard"])
    assert any("Lightning Lube" in s for s in patent["standard"]) and any("Roebuck" in s for s in patent["standard"])
    assert any("Winant" in s and "Hardy v. Toler" in s for s in treble["standard"])  # both sides of an open question
    a4 = state("debtor_response")
    assert set(a4["path_facts"]["projected_available_cash_at_decision_date"]) == {"p5", "p50"}  # the debtor's cash is a path fact
    assert a4["question"]["branches"] == ["seek_sale_or_financing", "file", "neither"]  # pay removed (arithmetic)


def test_the_injunction_and_settlement_questions_cite_their_own_law():
    nodes = M["templates"]["federal_post_judgment"]["nodes"]
    assert nodes["injunction"]["standard"] == ["ebay_2006", "usc18_1836_b3a", "nc_66_154_a", "frcp_62c"]
    assert "547 U.S. 388" in M["rules"]["ebay_2006"]["citation"] and "62(c)" in M["rules"]["frcp_62c"]["citation"]
    assert all("frcp_62b" not in nodes[n]["standard"] for n in ("injunction", "settlement_offer", "settlement_accept"))
    for n in ("ts_liability_jmol", "ts_damages_ruling", "patent_jmol", "trebling", "fees_awarded", "prejudgment_interest"):
        items = " ".join(nodes[n]["record_items"]).lower()  # a court does not weigh solvency on the merits
        assert not any(w in items for w in ("cash", "going-concern", "bankruptcy", "financing", "liquidity")), n


def test_the_residual_questions_agree_in_number():
    import re

    from app.disputes.forecast import load_registry

    qs = [q for q in load_registry()["questions"] if q.get("node")]
    assert len([q for q in qs if q["version"] == "4.0.0"]) == 27  # 4.1.0 adds its own (tests/test_pending_claim.py)
    third = r"\b(grants|sets|awards|enters|executes|moves|approves|orders|files|enforces|offers|accepts|calls|requests|stays)\b"
    for q in qs:
        assert not re.match(r"^Do(es)? the [^?]*?" + third, q["question"]), q["question"]  # 'Does the court grants'
        assert not re.match(r"^Does the (holders|noteholders|stockholders)\b", q["question"])
        assert not q["question"].startswith("How does the") or " decide:" not in q["question"]
        if q["primitive"] == "noul":  # both answers are complete sentences in agreement with their subject
            for text in q["prompt"]["criteria"].values():
                assert text.endswith(".") and not re.search(r"\b(holders|noteholders|stockholders)\b[^.]* does\b", text)


# 7. Cash conventions ------------------------------------------------------------------------------------------------

def test_coupon_shares_to_capacity_and_legal_spend_stops_on_settlement(base):
    dec16 = ix(date(2024, 12, 16))  # 15 Dec 2024 is a Sunday: paid the next business day
    cash = chain(base).run(()).events.cash  # 3.0M shares at 95% of $0.20 cover $570,000; the rest in cash
    assert (cash[:, dec16] == -75_000_000).all() and (np.delete(cash, dec16, axis=1) == 0).all()
    assert not chain(base, sens={"coupon_cash_share": "all_shares"}).run(()).events.cash.any()  # CHIPS: $0
    cash = chain(base, sens={"coupon_cash_share": "all_cash"}).run(()).events.cash
    assert (cash[:, dec16] == -132_000_000).all() and (np.delete(cash, dec16, axis=1) == 0).all()
    chips = chain(base, sens={"chips_credit_cents": True, "coupon_cash_share": "all_shares"}).run(()).events.cash
    assert (chips.sum(axis=1) == 233_000_000).all()
    c = chain(base)
    c.step("settle", "I1", "yes")
    pd = ix(REVIEW + timedelta(days=30))  # interval start (the review date) + 30 days
    legal = base[1].legal
    paid = c.ev.cash[:, pd] - (-legal[:, pd])
    assert (paid <= 0).all() and (c.ev.cash[:, pd + 1:] == -legal[:, pd + 1:]).all()  # spend added back from pd
    assert (c.ev.cash[:, :pd] == 0).all()


def test_no_separate_coupon_is_paid_on_notes_already_accelerated(base):
    dec16 = ix(date(2024, 12, 16))
    pre = (("execute_pre_ruling", "I1", "no"),)
    kept = chain(base).run(pre + (("judgment_default", "I1", "no"),)).events.cash
    assert (kept[:, dec16] == -75_000_000).all()
    for branch in ("accelerated", "holders_file"):  # accelerated on 19 Aug: the amount due carries the interest
        c = chain(base)
        cash = c.run(pre + (("judgment_default", "I1", branch),)).events.cash
        due = c.marks["notes_due"] <= dec16
        assert due.all() and (cash[:, dec16] == 0).all()
    late = chain(base)  # delisted on 6 Dec (panel): accelerated before the payment day
    cash = late.run((("listing", "", "delisted_panel"), ("delisting_notes", "delisted_panel", "accelerated"))).events.cash
    assert (late.marks["notes_due"] <= dec16).all() and (cash[:, dec16] == 0).all()


def test_legal_spend_stops_on_vacatur_and_continues_on_a_new_trial(base, full):
    fc, paths = full
    legal = base[1].legal
    pre = (("settle", "I1", "no"), ("execute_pre_ruling", "I1", "no"), ("judgment_default", "I1", "no"))
    after = None
    for branch in ("none", "retrial"):
        c = chain(base, sens={"coupon_cash_share": "all_shares"})  # the legal spend alone
        ev = c.run(pre + (("ruling", "", branch),)).events
        after = np.arange(N)[None, :] >= c.F[:, None]
        if branch == "none":  # vacated: the feed's legal outflows are added back from the ruling
            assert (c.resolved == np.where(c.F < N, c.F, 10**6)).all()
            assert (ev.cash[after] == -legal[after]).all() and (ev.cash[~after] == 0).all() and legal[after].any()
        else:  # a new trial: the dispute goes on
            assert (c.resolved >= 10**6).all() and not ev.cash.any()
    assert any(p.outcome == "vacated" for p in paths) and any(p.outcome == "new_trial" for p in paths)
    rt = next(c for c in fc.class_members if c.endswith(":retrial"))  # paying what survives a new trial ends nothing
    c = chain(base)
    c.run(pre + (("ruling", "", rt), ("debtor_response", "post", "pay")))
    assert (c.resolved >= 10**6).all()


def test_neutral_residuals_reproduce_attribution_step_two(base):
    from app.analysis.core import Analysis, EventModel

    feed, b = base
    d = judgment(stage="judgment_entered", motions=(), components=(), financing=(),
                 amount=judgment().amount.model_copy(update={"value": 200_000_000}))
    fc = Forecaster([d], {}, borrower="A", review=REVIEW, horizon=SETUP.horizon, hydrate=lambda f: {}, setup=SETUP,
                    basis=b)
    per = fc.all_paths()
    js = stub(fc, seed=11)
    m = EventModel({d.instance_id: d for d in fc.disputes}, js, per, fc.ordered(), neutral=neutral_map(js))
    a = Analysis(feed, SETUP, m)
    steps = a.attribution()
    assert steps[1]["metrics"] == a.views(neutral_map(js))["event_adjusted"]["metrics"]
    assert steps[2]["metrics"] == a.views()["event_adjusted"]["metrics"]
    assert m.probs().sum() == pytest.approx(1.0) and m.probs(neutral_map(js)).sum() == pytest.approx(1.0)
    # the payable $2.0M judgment keeps 'pay'; the stay locks collateral where cash covers it, released on settlement
    assert any("pay" in n.branches for n in fc.nodes.values() if n.node == "debtor_response")
    stayed = next(p for p in per[d.instance_id][""] if ("settle", "I4", "yes") in p.steps)
    ec = Chain(d, SETUP, M, Draws(b.cash.shape[0], basis=b)).run(stayed.steps).events
    level = np.cumsum(ec.lock, axis=1)
    assert (ec.lock > 0).any() and (level >= 0).all() and (level[:, -1][ec.lock.sum(axis=1) == 0] == 0).all()


def test_settlement_stay_and_contract_date_facts_state_what_the_chain_computes(full):
    """The offer and the reduced security Jev weighs are the chain's own amounts; contract dates are those on or after
    the decision inside the period. A stub row stands in for the chain's per-trajectory arrays."""
    from app.analysis.events import BIG

    fc, _ = full
    n4 = next(n for n in fc.nodes.values() if n.node == "settlement_accept")
    j8 = next(n for n in fc.nodes.values() if n.node == "stay_approved")
    k = len(fc.draws.basis.cash)
    day = np.full(k, 40)
    base = {"day": day, "cash": np.full(k, 9_000_000_00), "owed": np.full(k, 38_000_000_00),
            "collateral": np.zeros(k, dtype=np.int64), "petition": np.full(k, -1)}
    offered = {**base, "settle_offer": np.full(k, 5_000_000_00), "stay_offer": np.full(k, 4_000_000_00),
               "triggers": {"coupon": np.full(k, N - 3), "appeal_deadline": np.full(k, 20),
                            "judgment_default_ruling": np.where(np.arange(k) % 2 == 0, 100, BIG)}}
    declined = {**base, "settle_offer": np.zeros(k, dtype=np.int64), "stay_offer": np.zeros(k, dtype=np.int64),
                "triggers": offered["triggers"]}
    saved = fc.facts
    try:
        fc.facts = {**saved, n4.key: [offered, declined], j8.key: [offered, declined]}
        s = fc.path_facts(n4, judgment())["settlement_offer"]
        assert s["amount"] == {"p5": usd(5_000_000_00), "p50": usd(5_000_000_00)}  # the 'no' branch's zeros left out
        assert "thirty_day_operating_need" in s and s["payment"].startswith("one payment of the full amount")
        dates = fc.path_facts(n4, judgment())["contract_dates"]
        assert len(dates) == 2 and not any("appeal" in x for x in dates)  # the appeal deadline passed before it
        assert fc.path_facts(j8, judgment())["reduced_security_offered"]["p50"] == usd(4_000_000_00)
    finally:
        fc.facts = saved


class _Court(Chain):
    """The chain as it books each court ruling: the stay's approval day with the cash that day before the security is
    locked and the reduced security offered, and the early registration order with the cash that day (the levy
    follows the order by levy_lag_days)."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.seen = []

    def stay_security(self, motion, key, approved):
        approval = motion + int(self.p("briefing_days_new_motion")) + self.dr.lag(self.m, self.iid, key)
        cash = self.cash_at(approval)
        out = super().stay_security(motion, key, approved)
        self.seen.append((("stay_approved", key.split("_")[1]), out, cash, self.stay_offer.copy()))
        return out

    def step(self, node, ctx, branch):
        day = super().step(node, ctx, branch)
        if (node, ctx, branch) in (("registration_early", "I1", "yes"), ("enforce", "post", "levy")):
            order = self.pending_levy - int(self.p("levy_lag_days"))
            self.seen.append((("registration_early", ctx), order, self.cash_at(order), None))
        return day


def test_each_court_ruling_on_a_motion_has_the_facts_of_its_own_day(full, base):
    """J8 and J9 are asked of the court on the day it rules (motion + briefing + the engine's ruling-lag draw): every
    fact row of every stay_approved and registration_early question is, on every draw, the day the engine books the
    approval or the order and the cash that day before the lock or the levy (and, for a stay, the reduced security the
    engine measures that day). The company's stay motion keeps the motion day."""
    fc, paths = full
    b = base[1]
    engine, done = set(), set()
    for p in paths:
        for i, s in enumerate(p.steps):
            j9_post = s == ("enforce", "post", "levy") and ("appeal", "", "yes") in p.steps[:i] and (
                ("registration_early", "I1", "yes") not in p.steps[:i])
            if not (s[0] == "stay" or s == ("registration_early", "I1", "yes") or j9_post) or p.steps[:i + 1] in done:
                continue
            done.add(p.steps[:i + 1])
            c = _Court(judgment(), SETUP, M, Draws(b.cash.shape[0], basis=b))
            c.run(p.steps[:i + 1])
            what, day, cash, offer = c.seen[-1]
            engine.add((what, day.tobytes(), cash.tobytes(), None if offer is None else offer.tobytes()))
    recorded = set()
    for k, n in fc.nodes.items():
        if n.node in ("stay_approved", "registration_early"):
            what = (n.node, n.context.split("|")[0])
            for r in fc.facts[k]:
                offer = r["stay_offer"].tobytes() if n.node == "stay_approved" else None
                recorded.add((what, r["day"].tobytes(), r["cash"].tobytes(), offer))
    assert {e[0] for e in recorded} == {(x, c) for x in ("stay_approved", "registration_early") for c in ("I1", "post")}
    assert recorded == engine
    c = chain(base)
    for n in (n for n in fc.nodes.values() if n.node == "stay_motion"):
        motion = c.E0 if n.context.startswith("I1") else np.maximum(c.F, 0)
        assert all((r["day"] == motion).all() for r in fc.facts[n.key]), n.key
    assert not any(s[0] == "court_order" for p in paths for s in p.steps)  # a probe: it never enters a path


def test_the_settled_share_counts_only_draws_that_paid_a_settlement(full, base):
    """A 'settles' branch books nothing on a draw where the settlement amount is zero; that draw is classed by what
    the engine booked (Unresolved here), so a path's Settled share is the share of its draws with a positive
    settlement paid and no petition in the period, and the Filed shares still sum to its chance of a filing."""
    from app.analysis.page import CAUSE_CLASS, outcome_shares

    _, paths = full
    b = base[1]
    settles = [p for p in paths if any(s[0] == "settle" and s[2] == "yes" for s in p.steps)]
    zero = 0
    for p in settles[:: max(1, len(settles) // 150)]:
        tr = Chain(judgment(), SETUP, M, Draws(b.cash.shape[0], basis=b)).run(p.steps)
        pet = float((tr.cause > 0).mean())
        sh = outcome_shares(p.steps, p.outcome, pet, tr.cause, tr.marks, N)
        paid = (tr.marks["settled"] < N) & (tr.cause == 0)
        assert sh.get("settled", 0.0) == pytest.approx(paid.mean(), abs=1e-12)
        assert sum(sh.get(c, 0.0) for c in CAUSE_CLASS.values()) == pytest.approx(pet, abs=1e-12)
        assert sum(sh.values()) == pytest.approx(1.0, abs=1e-12)
        zero += int(((tr.marks["settled"] >= N) & (tr.cause == 0)).any())
    assert zero  # some 'settles' paths have draws where nothing was paid


def test_every_company_response_question_has_its_date_cash_and_amount_owed(full):
    """The company's response arises only where an amount is still owed on the day (a levy before the ruling can
    already have taken all that survives it), so each one asked carries its decision date, cash and amount owed."""
    fc, _ = full
    a4 = [n for n in fc.nodes.values() if n.node == "debtor_response"]
    assert {n.context.split("|")[0] for n in a4} >= {"I1", "post", "ripe"}
    for n in a4:
        facts = fc.path_facts(n, judgment())
        assert {"decision_date", "projected_available_cash_at_decision_date", "amount_owed_at_decision"} <= set(facts), n.key


# 8. Chain order -----------------------------------------------------------------------------------------------------

def test_the_post_ruling_response_comes_on_the_levy_day_and_again_at_the_ripe_date_after_seeking(full):
    fc, paths = full
    for p in paths:
        for i, s in enumerate(p.steps):
            if s[:2] == ("debtor_response", "post"):
                assert p.steps[i - 1] == ("enforce", "post", "levy")
            if s[:2] == ("debtor_response", "ripe"):
                assert any(x[0] == "debtor_response" and x[2].startswith("seek") for x in p.steps[:i])
    assert any(s[:2] == ("debtor_response", "ripe") for p in paths for s in p.steps)


def test_each_listing_question_has_the_facts_of_its_own_decision_date(full, base):
    fc, _ = full
    dates = chain(base).listing_dates()
    own = {"reverse_split_board": "vote_call", "split_approved": "effective_by", "nasdaq_hearing": "hearing_request",
           "panel_exception": "panel_decision"}
    seen = set()
    for k, n in fc.nodes.items():
        if n.node in own:
            days = np.concatenate([r["day"] for r in fc.facts[k]])
            assert set(days[days < 10**6].tolist()) <= {dates[own[n.node]]}
            seen.add(n.node)
    assert seen == set(own)
