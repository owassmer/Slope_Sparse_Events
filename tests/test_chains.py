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
    assert 1_000 < len(paths) < 6_000  # low thousands: collapsed by interval and amount class
    assert {n.question_id for n in fc.nodes.values()} == {q for t in M["templates"].values()
                                                          for q in (s["residual_question"] for s in t["nodes"].values())}
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
            owed = fc.path_facts(n, judgment()).get("amount_owed_at_decision")
            if owed:
                lo, hi = fc.class_range[label]
                assert "p50" not in owed and owed["judgment_after_ruling"]["max"] != owed["judgment_after_ruling"]["min"]


def test_a_levy_can_come_before_stay_approval_and_none_after_it(full, base):
    fc, paths = full
    b = base[1]
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
    assert all((x == c.F).all() for x in days[1:5]) and (days[5] == c.F).all()  # no increase: enforceable at once
    up = chain(base)
    up.step("ruling", "", "beyond:11292377711:1211612330")  # only the increase waits 30 days (L8(a) base)
    assert (up.EF == up.F).all() and (up.EI == up.F + 30).all()
    ripe = up.step("judgment_default", "post", "no")
    inside = ripe < 10**6
    assert (ripe[inside] == up.F[inside] + 60).all()
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
    assert (-c.ev.cash[r, second[ok]] >= total - c.entered).all()  # the increase, once enforceable
    assert (c.taken[ok] >= total).all()


def test_pay_is_removed_only_where_no_trajectory_can_fund_it(full, base):
    fc, _ = full
    a4 = [n for n in fc.nodes.values() if n.node == "debtor_response"]
    assert all("pay" not in n.branches for n in a4 if "|entered|" in n.key or "|beyond" in n.key)
    assert any("pay" in n.branches for n in a4 if "|amt" in n.key)  # the patent-only amount is payable
    rich = replace(base[1], cash=base[1].cash.copy())
    rich.cash[0] += 5_000_000_000  # one trajectory could pay the entered judgment
    fr = Forecaster([judgment()], {}, borrower="A", review=REVIEW, horizon=SETUP.horizon, hydrate=lambda f: {},
                    setup=SETUP, basis=rich)
    s = (("settle", "I1", "no"), ("execute_pre_ruling", "I1", "yes"), ("stay", "I1", "no"))
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

def test_the_judgment_default_fires_only_at_ripeness_unpaid_unstayed_and_noticed(base):
    ripe = ix(date(2024, 8, 19))  # enforceable from 20 Jun (Rule 62(a) ended) + 60 days (§7.01(i))
    c = chain(base)
    c.step("execute_pre_ruling", "I1", "no")
    c.step("judgment_default", "I1", "yes")
    assert (c.ev.petition == ripe).all()
    quiet = chain(base)
    quiet.step("execute_pre_ruling", "I1", "no")
    quiet.step("judgment_default", "I1", "no")  # no notice and acceleration: no default consequence
    assert (quiet.ev.petition == -1).all()
    stayed = chain(base)
    for s in (("execute_pre_ruling", "I1", "yes"), ("stay", "I1", "yes"), ("judgment_default", "I1", "yes")):
        stayed.step(*s)
    early = stayed.stayed_from <= ripe  # stayed before it ripens: no default on that trajectory
    assert early.any() and (~early).any()
    assert (stayed.ev.petition[early] == -1).all() and (stayed.ev.petition[~early] == ripe).all()
    small = judgment(components=(), motions=(), stage="judgment_entered",
                     amount=judgment().amount.model_copy(update={"value": 900_000_000}))  # below the $10.0M threshold
    low = chain(base, small)
    low.step("judgment_default", "post", "yes")
    assert (low.ev.petition == -1).all()


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
    assert on.any() and (tr.events.petition[on] <= c.EF[on]).all()
    t = run(line, feed.available_cents, tr.events)
    assert (t.petition == tr.events.petition).all() and t.stayed[on].mean() > 0
    i1 = next(q for q in paths if ("debtor_response", "I1", "file") in q.steps)
    assert (Chain(judgment(), SETUP, M, Draws(b.cash.shape[0], basis=b)).run(i1.steps).events.petition == 0).all()
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
    assert list(appeal["readings"]) == ["The payer's appeal intent"]  # A2 gets appeal intent only
    assert list(stay["readings"]) == ["The payer's willingness to pay this obligation"]  # A1 gets resistance
    assert merits["readings"] == {} and "available_cash_at_decision" not in merits["path_facts"]
    assert "cash" not in str(merits["path_facts"]).lower()  # J1-J6: no borrower cash, only the record's components
    for q in ("forecast_ts_liability_jmol", "forecast_ts_damages_ruling", "forecast_patent_jmol", "forecast_trebling",
              "forecast_fees_awarded", "forecast_prejudgment_interest"):
        from app.agent.jev import registry_question

        text = registry_question(q)["prompt"]["instructions"]
        assert "no borrower cash is given" in text and "sealed at the review date" in text
    a4 = state("debtor_response")
    assert set(a4["path_facts"]["available_cash_at_decision"]) == {"p5", "p50"}  # the debtor's cash is a path fact
    assert a4["question"]["branches"] == ["seek_sale_or_financing", "file", "neither"]  # pay removed (arithmetic)


def test_the_injunction_and_settlement_questions_cite_their_own_law():
    nodes = M["templates"]["federal_post_judgment"]["nodes"]
    assert nodes["injunction"]["standard"] == ["ebay_2006", "usc18_1836_b3a", "nc_66_154_a", "frcp_62c"]
    assert "547 U.S. 388" in M["rules"]["ebay_2006"]["citation"] and "62(c)" in M["rules"]["frcp_62c"]["citation"]
    assert all("frcp_62b" not in nodes[n]["standard"] for n in ("injunction", "settlement_offer", "settlement_accept"))


# 7. Cash conventions ------------------------------------------------------------------------------------------------

def test_coupon_in_shares_by_default_and_legal_spend_stops_on_settlement(base):
    dec16 = ix(date(2024, 12, 16))  # 15 Dec 2024 is a Sunday: paid the next business day
    assert not chain(base).run(()).events.cash.any()  # shares: no cash; CHIPS: $0 in the horizon
    cash = chain(base, sens={"coupon_cash_share": "all_cash"}).run(()).events.cash
    assert (cash[:, dec16] == -132_000_000).all() and (np.delete(cash, dec16, axis=1) == 0).all()
    chips = chain(base, sens={"chips_credit_cents": True}).run(()).events.cash
    assert (chips.sum(axis=1) == 233_000_000).all()
    c = chain(base)
    c.step("settle", "I1", "yes")
    pd = ix(REVIEW + timedelta(days=30))  # interval start (the review date) + 30 days
    legal = base[1].legal
    paid = c.ev.cash[:, pd] - (-legal[:, pd])
    assert (paid <= 0).all() and (c.ev.cash[:, pd + 1:] == -legal[:, pd + 1:]).all()  # spend added back from pd
    assert (c.ev.cash[:, :pd] == 0).all()


def test_legal_spend_stops_on_vacatur_and_continues_on_a_new_trial(base, full):
    fc, paths = full
    legal = base[1].legal
    pre = (("settle", "I1", "no"), ("execute_pre_ruling", "I1", "no"), ("judgment_default", "I1", "no"))
    after = None
    for branch in ("none", "retrial"):
        c = chain(base)
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
