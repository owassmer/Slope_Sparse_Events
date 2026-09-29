"""The 14 May pending-claim event model (dispute model 4.1.0): composition and cash only."""

from __future__ import annotations

import akoustis_20240514_fixture as fx
import numpy as np
import pytest

from app.analysis.events import Chain, Draws


def chain(sens=None, d=None):
    dr = Draws(fx.basis().cash.shape[0], basis=fx.basis())
    return Chain(fx.pending() if d is None else d, fx.setup(), fx.model(), dr, sens)


def run(steps, sens=None):
    ch = chain(sens)
    return ch, ch.run(steps)


# --- item 1: the financing decision at the cash floor --------------------------------------------------------------

@pytest.mark.parametrize("branch,sens,expect", [
    ("no_award", None, 970_000_000), ("without_principal_measure", None, 970_000_000),
    ("claimant_theory", None, 0),
    ("no_award", {"raise_capacity": True}, 500_000_000),
    ("claimant_theory", {"raise_capacity_after_adverse_judgment": True}, 500_000_000)])
def test_raise_books_the_situations_amount_in_equal_daily_amounts(branch, sens, expect):
    """raise_equity books the amount available in the path's situation, in equal daily amounts over 30 days from the
    floor day; nothing where the amount is zero (after the claimant's-theory judgment, base)."""
    base, _ = run((("verdict", "I0", branch), ("cash_floor", "", "continue")), sens)
    ch, tr = run((("verdict", "I0", branch), ("cash_floor", "", "raise_equity")), sens)
    t = tr.day[-1]
    added = tr.events.cash - base.ev.cash
    N = ch.N
    for i in np.flatnonzero((t >= 0) & (t < N))[:40]:
        row = added[i]
        days = np.flatnonzero(row)
        if expect == 0:
            assert days.size == 0
            continue
        assert days[0] == t[i] and days.size == min(30, N - t[i])
        each = expect // 30
        assert (row[days[1:]] == each).all() and row[days[0]] == expect - each * 29
        assert row.sum() == (expect if t[i] + 30 <= N else row[days].sum())
    assert (tr.raise_offer[(t >= 0) & (t < N)] == expect).all()


def test_raise_is_offered_only_where_available():
    """The floor question offers 'raise_equity' only where some trajectory can raise a positive amount."""
    ch, tr = run((("verdict", "I0", "claimant_theory"), ("cash_floor", "", "continue")))
    assert not (tr.raise_offer > 0).any()
    ch, tr = run((("verdict", "I0", "no_award"), ("cash_floor", "", "continue")))
    inside = tr.day[-1] < ch.N
    assert inside.any() and (tr.raise_offer[inside] > 0).all()


CT = ("verdict", "I0", "claimant_theory")
ENTRY = (("judgment_response", "entry", "continue"), ("post_trial_motions", "", "yes"))
FLOOR = ("cash_floor", "", "continue")


@pytest.mark.parametrize("name,steps,standing_until", [
    ("settled before the verdict", (("settle", "I0", "yes"), FLOOR), None),
    ("no award", (("verdict", "I0", "no_award"), FLOOR), None),
    ("claimant's theory, standing", (CT, *ENTRY, ("post_trial_ruling", "", "stands"), FLOOR), "never"),
    ("claimant's theory, set aside", (CT, *ENTRY, ("post_trial_ruling", "", "set_aside"), FLOOR), "F"),
    ("claimant's theory, settled", (CT, *ENTRY, ("settle", "I1", "yes"), FLOOR), "resolved")])
def test_the_raise_is_available_exactly_where_no_adverse_money_judgment_stands(name, steps, standing_until):
    """raise_capacity wherever no adverse money judgment stands on the day of the floor decision (no judgment, a
    settled claim, a judgment set aside after trial); raise_capacity_after_adverse_judgment only while one stands."""
    ch, tr = run(steps)
    t = tr.day[-1]
    inside = (t >= 0) & (t < ch.N) & ((tr.events.petition < 0) | (t < tr.events.petition))
    assert inside.any(), name
    if standing_until is None:
        free = inside
    elif standing_until == "never":
        free = np.zeros_like(inside)
    else:
        end = ch.F if standing_until == "F" else ch.resolved
        free = inside & (t >= end)
        assert free.any(), name
    assert (tr.raise_offer[free] == 970_000_000).all(), name
    assert (tr.raise_offer[inside & ~free] == 0).all(), name


def test_the_20_jun_case_keeps_its_floor_question():
    """Without a case raise_capacity the floor decision is 4.0.0's petition_cash_floor (yes / no)."""
    from app.disputes.rules import load_model

    assert "value" not in load_model()["parameters"].get("raise_capacity", {})
    assert "value" in fx.model()["parameters"]["raise_capacity"]


# --- item 2: the verdict's branches from the jury's verdict form ---------------------------------------------------

def forecaster():
    from app.disputes.forecast import Forecaster

    d = fx.pending().model_copy(update={"status": "interpreted"})
    return Forecaster([d], {}, borrower="B", review=fx.REVIEW, horizon=fx.setup().horizon, hydrate=lambda f: {},
                      model=fx.model(), setup=fx.setup(), basis=fx.basis()), d


def test_verdict_composites_are_disjoint_and_exhaustive():
    """Every pair of conjunctions across the three verdict branches disagrees on some node's answer, and under random
    answers the branches' probabilities sum to 1."""
    from app.disputes.forecast import Dist, composite

    fc, d = forecaster()
    classes = fc.verdict_classes(d)
    assert set(classes) == {"no_award", "without_principal_measure", "claimant_theory"}
    conj = [dict(c) for parts in classes.values() for c in parts]
    for i, a in enumerate(conj):
        for b in conj[i + 1:]:
            assert any(k in b and b[k] != v for k, v in a.items())
    nodes = {k for c in conj for k in c}
    assert all(fc.nodes[k].node in ("verdict_finding", "verdict_measure") for k in nodes)
    assert all(fc.nodes[k].branches == ("yes", "no") for k in nodes)
    rng = np.random.default_rng(7)
    for _ in range(50):
        dist = Dist({k: dict(zip(("yes", "no"), rng.dirichlet((1, 1)), strict=True)) for k in nodes})
        assert abs(sum(dist[composite(p)]["yes"] for p in classes.values()) - 1) < 1e-12


def test_verdict_questions_ask_no_amount_and_no_cash():
    """Each verdict node is one jury decision on a quoted form question; its state carries no cash facts."""
    fc, d = forecaster()
    fc.verdict_classes(d)
    for n in fc.nodes.values():
        st, _, _ = fc.state(n)
        assert st["question"]["actor"] == "jury" and st["question"]["form_question"].startswith("Question No.")
        assert not {"projected_available_cash_at_decision_date", "amount_owed_at_decision"} & set(st["path_facts"])


# --- §7.9 tests 1-9 on the 14 May tree (no Jev) --------------------------------------------------------------------

ENFORCEMENT = {"execute_pre_ruling", "stay", "registration_early", "judgment_response", "enforce", "judgment_default",
               "post_trial_motions", "post_trial_ruling"}


@pytest.fixture(scope="module")
def tree():
    fc, d = forecaster()
    return fc, d, fc.paths(d), fc.bank_paths()


def test_1_every_path_family_sums_to_one(tree):
    """Composition: under random Dirichlet answers for every node, the dispute's and the bank view's path
    probabilities each sum to 1 (every composite is disjoint and exhaustive)."""
    from app.disputes.forecast import Dist, path_probability

    fc, _, paths, bank = tree
    rng = np.random.default_rng(11)
    for _ in range(5):
        dist = Dist({k: dict(zip(n.branches, rng.dirichlet(np.ones(len(n.branches))), strict=True))
                     for k, n in {**fc.nodes, **fc.bank_nodes}.items()})
        for family in (paths, bank):
            assert abs(sum(path_probability(p.edges, dist) for p in family) - 1) < 1e-9


def test_2_a_missing_judgment_never_activates_enforcement(tree):
    """After no_award or set_aside no enforcement, stay, registration, judgment-default or response step exists, and
    the engine books no levy or lock from then on."""
    fc, d, paths, _ = tree
    seen = 0
    for p in paths:
        cut = next((i for i, s in enumerate(p.steps) if s in (("verdict", "I0", "no_award"),
                                                                ("post_trial_ruling", "", "set_aside"))), None)
        if cut is None:
            continue
        assert not {s[0] for s in p.steps[cut + 1:]} & ENFORCEMENT, p.steps
        if seen < 25:
            ch = chain()
            ch.run(p.steps)
            day = ch.V if p.steps[cut][0] == "verdict" else ch.F
            pet = np.where(ch.ev.petition < 0, 10**6, ch.ev.petition)
            # from the day the path has no money judgment; where a petition came first, the estate holds what is locked
            after = (np.arange(ch.N)[None, :] >= day[:, None]) & (day < pet)[:, None]
            held = np.cumsum(ch.ev.lock, axis=1)  # cash locked as stay security at each day's end
            assert not (held * after).any()  # nothing locked from the day the path has no money judgment
            if p.steps[cut][0] == "verdict":
                assert (ch.taken == 0).all() and (ch.owed_at(np.full(ch.n, ch.N - 1)) == 0).all()
        seen += 1
    assert seen > 0


def test_3_clocks_move_with_the_modeled_verdict():
    from app.analysis.events import business_days_after

    ch, tr = run((("verdict", "I0", "claimant_theory"), ("judgment_response", "entry", "continue"),
                  ("post_trial_motions", "", "yes")))
    window = {ch.ix(__import__("datetime").date.fromisoformat(x)) for x in fx.model()["parameters"]["verdict_window"]["days"]}
    assert set(np.unique(ch.V)) <= window and len(set(np.unique(ch.V))) > 1
    for v, e in zip(ch.V, ch.E_ix, strict=True):
        assert e == ch.ix(business_days_after(fx.REVIEW + __import__("datetime").timedelta(days=int(v) + 1), 1))
    assert (ch.e_ix == ch.E_ix + 31).all() and (tr.day[-1] == ch.E_ix + 28).all()
    assert (ch.F >= ch.E_ix + 28 + 21).all()


def test_4_branch_amounts():
    from app.analysis.events import verdict_amount

    m, d = fx.model(), fx.pending()
    assert verdict_amount(d, m, "claimant_theory") == 6_752_641_200  # duplicates and the barred UDTPA claim out
    assert verdict_amount(d, m, "without_principal_measure") == 142_641_200
    assert verdict_amount(d, m, "without_principal_measure", {"lower_award_amount": True}) == 999_999_900
    for sens in (None, {"lower_award_amount": True}):  # the lower branch never ripens §7.01(i)
        ch, tr = run((("verdict", "I0", "without_principal_measure"), ("judgment_response", "entry", "continue"),
                      ("post_trial_motions", "", "no")), sens)
        for ctx in ("I1", "post"):
            assert not ch.judgment_default(ctx)[1].any()


def _unknown(d, *ids):
    """The dispute with the named components' amounts unknown (the agent quoted none)."""
    return d.model_copy(update={"components": tuple(
        c.model_copy(update={"amount_cents": None, "unknown": True}) if c.component_id in ids else c
        for c in d.components)})


def test_an_unknown_component_amount_stays_unknown():
    """Critical rule: an unknown amount never becomes 0. The lower branch takes the case's declared bound, labelled as
    the bound; without a declared bound the analysis refuses and names the component; the claimant's branch, which
    declares no bound, refuses too. The enhancement kinds are the bounded claimant_enhancements term, never summed."""
    import copy

    from app.analysis.events import Chain, Draws, UnknownAmount, verdict_basis
    from app.domain.investigation import Component

    m, d = fx.model(), fx.pending()
    assert verdict_basis(d, m, "without_principal_measure") == (142_641_200, "record")
    gap = _unknown(d, "patent", "advertising")
    assert verdict_basis(gap, m, "without_principal_measure") == (142_641_200, "bound")
    assert verdict_basis(gap, m, "without_principal_measure", {"lower_award_amount": True}) == (999_999_900,
                                                                                                "declared")
    ch = Chain(gap, fx.setup(), m, Draws(fx.basis().cash.shape[0], basis=fx.basis()), None)
    ch.run((("verdict", "I0", "without_principal_measure"),))
    assert ch.entered == 142_641_200  # the judgment the lower branch books is the bound, never 0
    bare = copy.deepcopy(m)
    del bare["parameters"]["lower_award_amount"]["bound"]
    with pytest.raises(UnknownAmount, match="advertising"):
        verdict_basis(gap, bare, "without_principal_measure")
    with pytest.raises(UnknownAmount, match="patent"):
        verdict_basis(gap, m, "claimant_theory")
    extra = d.model_copy(update={"components": d.components + (
        Component(component_id="exemplary", label="exemplary damages", kind="exemplary", status="requested",
                  unknown=True, claim="trade_secrets", theory="claimant"),)})
    assert verdict_basis(extra, m, "claimant_theory") == (6_752_641_200, "record")


def test_the_claimants_sums_exclude_the_defense_theory():
    """A component marked as the defense's theory is never part of the claimant's sums, known or unknown."""
    from app.analysis.events import verdict_basis
    from app.domain.investigation import Component

    m, d = fx.model(), fx.pending()
    for amount in (50_000_000, None):
        extra = d.model_copy(update={"components": d.components + (
            Component(component_id="defense_measure", label="the defense's measure", kind="compensatory",
                      status="requested", amount_cents=amount, unknown=amount is None, claim="trade_secrets",
                      theory="defense"),)})
        assert verdict_basis(extra, m, "claimant_theory") == (6_752_641_200, "record")
        assert verdict_basis(extra, m, "without_principal_measure") == (142_641_200, "record")


@pytest.mark.parametrize("collection", ["debit", "protect_need"])
def test_the_page_states_the_collection_rule_of_the_setup(collection):
    """The page's cash sentence follows Setup.collection: an automatic debit (14 May central), or collections only from
    cash above the cash floor (the sensitivity); never the other mode's rule."""
    from dataclasses import replace

    from app.analysis.page import mechanism

    s = replace(fx.setup(), collection=collection)
    spec = next(t["nodes"]["settlement_offer"] for t in fx.model()["templates"].values()
                if "settlement_offer" in t.get("nodes", {}))
    text = " ".join(mechanism("settlement_offer", spec, fx.model(), s))
    assert ("debits each installment in full" in text) == (collection == "debit")
    assert ("only from cash above the 30-day operating need" in text) == (collection == "protect_need")


def _sample(paths, pred, k=20):
    got = [p for p in paths if pred(p)]
    return got[:: max(1, len(got) // k)][:k]


def test_5_claimant_branch_is_cash_and_date_identical_under_its_enhancements(tree):
    """The claimant's branch is beyond cash under both claimant_enhancements settings: same cash, lock, petition, days."""
    _, _, paths, _ = tree
    for p in _sample(paths, lambda p: ("verdict", "I0", "claimant_theory") in p.steps):
        (_, ta), (_, tb) = run(p.steps), run(p.steps, {"claimant_enhancements": True})
        assert (ta.events.cash == tb.events.cash).all() and (ta.events.lock == tb.events.lock).all()
        assert (ta.events.petition == tb.events.petition).all()
        assert all((x == y).all() for x, y in zip(ta.day, tb.day, strict=True))


def test_6_coupon_base_shares_sensitivity_cash():
    from datetime import date

    base, _ = run(())
    cash, _ = run((), {"coupon_cash_share": True})
    day = base.ix(date(2024, 6, 17))
    assert (base.ev.cash[:, day] == 0).all()
    assert (cash.ev.cash[:, day] == -132_000_000).all() and (cash.ev.cash.sum(axis=1) == -132_000_000).all()
    filed, tr = run((("verdict", "I0", "claimant_theory"), ("judgment_response", "entry", "file")),
                    {"coupon_cash_share": True})
    early = (tr.events.petition >= 0) & (tr.events.petition <= day)
    assert early.all() and (tr.events.cash[:, day] == 0).all()  # a petition before it: no coupon


def test_7_cash_facts_equal_the_engine_on_every_trajectory(tree):
    """§16.4, in date order: on every trajectory where a step's decision falls inside the period before any petition,
    nothing walked after it books cash or security on an earlier day, and the cash its facts read equals the loan
    engine's available cash that day, run forward from the line's opening state on the whole path's event cash
    (bookings on the decision day itself, which the step may precede, aside). Paths where the cash floor precedes a
    later-walked listing question are always checked."""
    from app.analysis.engine import run as engine_run
    from app.analysis.events import BIG, EventCash

    class Rec(Chain):
        def step(self, node, ctx, branch):  # the walk's own step (probes are plain Chain copies)
            if not getattr(self, "waits", lambda n, c: False)(node, ctx):
                self.snaps[len(self.snaps)] = (self.ev.cash.copy(), self.ev.lock.copy(), None)
            return super().step(node, ctx, branch)

        def decide_waiting(self, i, node, branch, t):  # a step booked on its own day, after later-walked steps
            self.fired.append((i, t.copy(), self.ev.cash.copy(), self.ev.lock.copy()))
            return super().decide_waiting(i, node, branch, t)

    _, _, paths, _ = tree
    b, s = fx.basis(), fx.setup()
    assert s.exposure.principal_cents > 0  # the line starts with an outstanding balance
    floor_first = _sample(paths, lambda p: ("listing", "kept", "listed") in p.steps and any(
        x[0] == "cash_floor" and x[2] != "file" for x in p.steps), 4)
    assert floor_first
    for p in floor_first + _sample(paths, lambda p: True, 12):
        ch = Rec(fx.pending(), s, fx.model(), Draws(b.cash.shape[0], basis=b), None)
        ch.snaps, ch.fired = {}, []
        tr = ch.run(p.steps)
        ev = tr.events
        eng = engine_run(b.line, b.opening - s.exposure.cash_cents,
                         EventCash(ev.cash.copy(), ev.lock.copy(), ev.capacity.copy(), ev.petition.copy(), ev.kinds,
                                   ev.incurred))
        assert (ch.cum() == eng.cash[:, :ch.N]).all()
        waits = getattr(ch, "waits", lambda n, c: False)
        booked = iter(ch.snaps.values())
        at = {j: next(booked)[:2] for j, x in enumerate(p.steps) if not waits(x[0], x[1])}
        for j, t, c, lk in ch.fired:  # a waiting step books on its trajectories in one or more passes
            rows = (t < BIG)[:, None]
            c0, l0 = at.get(j, (c, lk))
            at[j] = (np.where(rows, c, c0), np.where(rows, lk, l0))
        pet = np.where(ev.petition < 0, BIG, ev.petition)
        for j, (cash0, lock0) in at.items():
            day = tr.day[j]
            m = (day >= 0) & (day < ch.N) & (day < pet)
            later = (ev.cash - cash0) - (ev.lock - lock0)
            before = ((later != 0) & (np.arange(ch.N)[None, :] < day[:, None])).any(axis=1)
            assert not (before & m).any(), (p.steps[j], int((before & m).sum()))
            same = later[ch.rows, np.clip(day, 0, ch.N - 1)] != 0
            ok = m & ~same
            assert (tr.cash[j][ok] == eng.cash[ch.rows, np.clip(day, 0, ch.N - 1)][ok]).all(), p.steps[j]


def test_7b_the_floor_books_the_same_cash_wherever_it_is_walked(tree):
    """The cash floor and cash exhaustion are state-triggered: each books on its own day on every trajectory, so
    walking them first instead of where the tree asks them leaves every trajectory's event cash, security and petition
    unchanged (paths with a stay, a settlement or a levy after the floor question included)."""
    _, _, paths, _ = tree
    fl = ("cash_floor", "cash_out")
    later = _sample(paths, lambda p: any(x[0] in ("stay", "settle", "enforce", "registration_early")
                                         for x in p.steps[next((i for i, x in enumerate(p.steps) if x[0] in fl),
                                                               len(p.steps)):]), 20)
    assert later
    for p in later + _sample(paths, lambda p: True, 10):
        front = tuple(x for x in p.steps if x[0] in fl) + tuple(x for x in p.steps if x[0] not in fl)
        (_, a), (_, b) = run(p.steps), run(front)
        assert (a.events.cash == b.events.cash).all() and (a.events.lock == b.events.lock).all(), p.steps
        assert (a.events.petition == b.events.petition).all(), p.steps


def test_7c_the_raise_is_offered_wherever_the_whole_path_makes_it_available(tree):
    """Only impossibility removes a branch: a floor question asked without 'raise_equity' is one where no equity is
    available at the floor on any trajectory of any path through it, including a set-aside, payment or settlement
    walked after the question and dated before the floor."""
    _, _, paths, _ = tree
    seen = 0
    for p in paths:
        at = [i for i, (k, _) in enumerate(p.edges) if "financing_at_floor|noraise" in k]
        if not at:
            continue
        i = next(j for j, x in enumerate(p.steps) if x[0] == "cash_floor")
        ch, tr = run(p.steps)
        info = tr.late[i]
        pet = np.where(info["petition"] < 0, 10**6, info["petition"])
        assert not (((tr.day[i] < ch.N) & (tr.day[i] < pet) & (info["raise_offer"] > 0)).any()), p.steps
        seen += 1
    assert seen


def test_7d_the_stay_questions_state_the_collateral_the_engine_locks(tree):
    """⚑ bond collateral: the stay questions are told the engine's own figure on the approval day (the amount owed plus
    §1961 interest at the pending rate over bond_forward_interest_years, times the collateral share), the amount a
    covered stay locks, not the amount owed alone."""
    from app.disputes.forecast import usd

    fc, d, paths, _ = tree
    nodes = [n for n in fc.nodes.values() if n.node in ("stay_motion", "stay_approved")]
    assert nodes
    for n in nodes:
        rows = fc.facts[n.key]
        masks = [fc.live(n, r) for r in rows]
        if not any(m.any() for m in masks):
            continue
        coll = np.concatenate([r["collateral"][m] for r, m in zip(rows, masks, strict=True)])
        owed = np.concatenate([r["owed"][m] for r, m in zip(rows, masks, strict=True)])
        assert fc.path_facts(n, d)["bond_collateral_required"] == usd(int(np.quantile(coll, 0.5))), n.key
        assert np.quantile(coll, 0.5) > np.quantile(owed, 0.5), n.key  # the bond carries the interest
    covered = next(p for p in paths if ("stay", "I1", "yes") in p.steps)
    i = covered.steps.index(("stay", "I1", "yes"))
    ch, tr = run(covered.steps[: i + 1])
    locked = np.cumsum(ch.ev.lock, axis=1).max(axis=1)
    full = locked == tr.collateral[i]
    assert full.any() and (tr.collateral[i][full] > 0).all()  # where cash covers it, the lock is that figure


def test_7e_owed_facts_count_only_what_is_taken_before_the_decision(tree):
    """⚑ amount owed, in date order: each step's owed fact equals the amount owed on its day on the whole path,
    counting only levies and payments dated before that day (a levy dated after a decision, walked before it, is not
    in it); and a pre-ruling judgment default does not ripen on a judgment set aside before its ripe date."""
    from app.analysis.events import BIG

    class Rec(Chain):
        def _take(self, day):
            t0 = self.taken.copy()
            super()._take(day)
            self.log.append((np.asarray(day).copy(), self.taken - t0))

        def respond(self, booking, day, cause="enforcement"):
            t0 = self.taken.copy()
            super().respond(booking, day, cause)
            self.log.append((np.asarray(day).copy(), self.taken - t0))

    _, _, paths, _ = tree
    b = fx.basis()
    levied = _sample(paths, lambda p: ("registration_early", "I1", "yes") in p.steps and any(
        x[0] == "post_trial_ruling" for x in p.steps), 8)
    aside = _sample(paths, lambda p: ("post_trial_ruling", "", "set_aside") in p.steps and any(
        x[0] == "judgment_default" and x[1] == "I1" for x in p.steps), 8)
    assert levied and aside
    for p in levied + aside:
        ch = Rec(fx.pending(), fx.setup(), fx.model(), Draws(b.cash.shape[0], basis=b), None)
        ch.log = []
        tr = ch.run(p.steps)
        pet = np.where(tr.events.petition < 0, BIG, tr.events.petition)
        takes = ch.takes
        for j, x in enumerate(p.steps):
            day = tr.day[j]
            m = (day >= 0) & (day < ch.N) & (day < pet)
            before = sum((np.where(t < day, a, 0) for t, a in ch.log), np.zeros(ch.n, dtype=np.int64))
            same = np.zeros(ch.n, dtype=bool)
            for t, a in ch.log:
                same |= (t == day) & (a != 0)
            ch.takes = [(np.full(ch.n, -1), before)]
            ref = ch.owed_at(day)
            ch.takes = takes
            ok = m & ~same
            assert (tr.owed[j][ok] == ref[ok]).all(), x
        if ("post_trial_ruling", "", "set_aside") in p.steps:
            j = next(i for i, x in enumerate(p.steps) if x[0] == "judgment_default" and x[1] == "I1")
            assert not ((tr.day[j] < BIG) & (tr.day[j] >= ch.F)).any()  # no default ripens after the set-aside


def test_7f_one_dispute_end_a_satisfying_levy_ends_it_and_legal_spend_never_returns(tree):
    """Fix 4: a levy or payment that satisfies the judgment ends the dispute (`resolve`, as a payment does): legal spend
    stops that day, nothing is owed and no later step arises on it; and after a petition an ended dispute's legal
    spend stays stopped (the petition zeroes the estate's other event cash only)."""
    from app.analysis.events import BIG

    _, _, paths, _ = tree
    b = fx.basis()
    levy = _sample(paths, lambda p: ("verdict", "I0", "without_principal_measure") in p.steps
                   and ("enforce", "post", "levy") in p.steps, 6)
    assert levy
    hit = 0
    for p in levy:
        ch, tr = run(p.steps)
        for day, take in ch.writs:
            day = np.broadcast_to(day, (ch.n,))
            full = (take > 0) & (ch.owed_at(day + 1) == 0) & (day < ch.N)
            assert (ch.resolved[full] <= day[full]).all(), p.steps
            hit += int(full.sum())
        t = np.arange(ch.N)[None, :]
        ended = (t >= ch.resolved[:, None]) & (b.legal[:, :ch.N] != 0)
        pet = np.where(ch.ev.petition < 0, BIG, ch.ev.petition)[:, None]
        assert (tr.events.cash[ended & (t >= pet)] == -b.legal[:, :ch.N][ended & (t >= pet)]).all()
    assert hit
    after = _sample(paths, lambda p: ("settle", "I0", "yes") in p.steps and any(
        x[0] in ("cash_floor", "cash_out") and x[2] in ("file", "yes") for x in p.steps), 4)
    assert after
    for p in after:
        ch, tr = run(p.steps)
        pet = ch.ev.petition
        rows = (pet >= 0) & (ch.resolved < pet)
        assert rows.any()
        for r in np.flatnonzero(rows)[:20]:
            tail = slice(int(pet[r]), ch.N)
            assert (tr.events.cash[r, tail] == -b.legal[r, tail]).all()  # the add-back, and nothing else


def test_7g_delisting_defaults_the_notes_whether_or_not_the_dispute_ended(tree):
    """The notes' Event of Default and repurchase on delisting do not depend on the lawsuit: after a settlement (the
    dispute ended) the delisting route still arises and can accelerate the notes, before any petition."""
    _, _, paths, _ = tree
    settled = [p for p in paths if ("settle", "I0", "yes") in p.steps
               and ("listing", "kept", "delisted_suspension") in p.steps]
    assert settled
    assert any(x[0] == "delisting_notes" for p in settled for x in p.steps)
    p = next(p for p in settled if any(x[0] == "delisting_notes" and x[2] == "accelerated" for x in p.steps))
    ch, tr = run(p.steps)
    j = next(i for i, x in enumerate(p.steps) if x[0] == "delisting_notes")
    inside = tr.day[j] < ch.N
    assert inside.any() and (ch.resolved[inside] < tr.day[j][inside]).all()  # ended before it, yet it arises
    assert (ch.marks["notes_due"][inside] < 10**6).all()


def test_7h_the_ordinary_view_is_the_forecast_whose_dispute_ends_on_the_review_date(tree):
    """Fix 3 (spec §16.1): with no dispute events, the ordinary view's cash equals the full forecast's cash on the same
    floor and listing steps where the dispute ends at no cost on the review date: legal spend stops from then and
    nothing else differs (operations, coupon, the floor decisions, the listing chain and the notes' delisting route)."""
    from app.analysis.events import bank_trace

    fc, d, _, bank = tree
    assert any(x[0] == "listing" for p in bank for x in p.steps)  # the listing chain is in both views
    assert any(n.node == "listing_kept" for n in fc.bank_nodes.values())
    b = fx.basis()
    for p in bank:
        ch = chain()
        ch.instrument_cash()
        ch.resolve(np.zeros(ch.n, dtype=np.int64), np.ones(ch.n, dtype=bool))  # the dispute ends at no cost
        from app.analysis.events import Trace

        tr = Trace(ch.ev)
        for step in p.steps:
            ch.advance(tr, *step)
        full = ch.finish(tr)
        bt = bank_trace(fx.notes(), p.steps, fx.setup(), fx.model(), Draws(b.cash.shape[0], basis=b), None)
        assert (full.events.cash == bt.events.cash).all(), p.steps
        assert (full.events.petition == bt.events.petition).all(), p.steps
    t = np.arange(ch.N)[None, :]
    assert (bt.events.cash[(b.legal[:, :ch.N] != 0) & (t < np.where(bt.events.petition < 0, ch.N,
                                                                         bt.events.petition)[:, None])] != 0).all()


def test_7i_the_ordinary_view_asks_the_forecasts_questions_on_the_same_record(tree):
    """Spec §16.1: each ordinary-view question is the forecast's question of that node type in the matching situation,
    built on the same case, record items, evidence and standard; only the path facts, the context (no dispute-branch
    conditions) and the one condition that the event has no cash effect differ."""
    from app.disputes.forecast import bank_state

    fc, _, _, _ = tree
    both = {n.node for n in fc.bank_nodes.values()} & {n.node for n in fc.nodes.values()}
    assert {"financing_at_floor", "petition_cash_out", "listing_kept", "holders_act_delisting"} <= both
    checked = set()
    for n in fc.bank_nodes.values():
        tags = set(c for c in n.context.split("|")[1:] if c)
        matches = [m for m in fc.nodes.values() if m.node == n.node and m.branches == n.branches
                   and tags <= set(m.context.split("|"))]
        assert matches, n.key
        ours = bank_state(fc, n)
        for m in matches:
            theirs, _, _ = fc.state(m)
            assert ours["case"] == theirs["case"] and ours["case"]["company"] == "B", (n.key, m.key)
            extra = [a for a in ours["assumptions"] if a not in theirs["assumptions"]]
            assert len(extra) == 1 and "given no cash effect" in extra[0], (n.key, extra)
            strip = lambda st, extra=extra: {**{k: v for k, v in st.items() if k != "path_facts"},  # noqa: E731
                                            "question": {k: v for k, v in st["question"].items() if k != "context"},
                                            "assumptions": [a for a in st["assumptions"] if a not in extra]}
            assert strip(ours) == strip(theirs), (n.key, m.key)
        assert not {"claimed", "settled", "seeking", "motions_pending"} & tags  # no dispute-branch condition
        checked.add(n.node)
    assert checked == {n.node for n in fc.bank_nodes.values()}


def test_7j_the_ordinary_view_carries_the_ordinary_obligations_facts_and_no_dispute_fact(tree):
    """Spec §16.3 (orchestrator, 28 Sep 2026): for each node type asked in both views, where the situation holds in
    both, the ordinary view's path facts carry every fact the forecast's state takes from the ordinary obligations
    (the instrument's terms, the raise available, the operating figures; the instrument's dated triggers, dated on
    the ordinary view's own path) and none that exists only because of the dispute (the builder's `facts_by_source`
    names each fact's source). One fact contract per node type (design 14 May §7.12): the ordinary view's keys are
    exactly the forecast's ordinary keys for that node type (`Forecaster.ordinary_facts` builds both)."""
    from app.analysis.events import INSTRUMENT_TRIGGERS
    from app.disputes.forecast import TRIGGER_PHRASES, bank_state

    fc, d, _, _ = tree
    instrument = {TRIGGER_PHRASES[t].split("{")[0] for t in INSTRUMENT_TRIGGERS}
    terms = fc.obligation_facts(fc.instrument())
    assert terms, "the case's notes are an ordinary obligation"
    checked = set()
    for n in fc.bank_nodes.values():
        ours = bank_state(fc, n)["path_facts"]
        if "decision_date" not in ours:
            continue
        tags = set(c for c in n.context.split("|")[1:] if c)
        for m in (m for m in fc.nodes.values() if m.node == n.node and m.branches == n.branches
                  and tags <= set(m.context.split("|"))):
            own, common = fc.facts_by_source(m, d)
            if "decision_date" not in common:
                continue
            for k, v in common.items():
                if k == "contract_dates":
                    continue
                assert k in ours, (n.key, m.key, k)
                if k in terms:
                    assert ours[k] == v, (n.key, m.key, k)
            for k in own:
                if k == "contract_dates":
                    assert not set(own[k]) & set(ours.get(k, {})), (n.key, m.key)
                else:
                    assert k not in ours, (n.key, m.key, k)
            assert all(any(lab.startswith(p) for p in instrument) for lab in ours.get("contract_dates", {})), n.key
            # one fact contract per node type: the same keys as the forecast's ordinary half, no more and no fewer
            # (the dated triggers vary by path: a date before the decision or after the period is not stated)
            assert set(ours) - {"contract_dates"} == set(common) - {"contract_dates"}, (
                n.key, m.key, sorted(set(ours) ^ set(common)))
            checked.add(n.node)
    assert {"financing_at_floor", "listing_kept", "holders_act_delisting", "petition_on_notes"} <= checked, checked


def test_8_settlement_is_bounded_and_ends_the_claim(tree):
    """A settlement never exceeds cash above the 30-day need on its payment date, nor the amount claimed (I0) or owed;
    paid, it resolves the dispute (claim, lock and legal spend end)."""
    _, _, paths, _ = tree
    for iv in ("I0", "I1"):
        for p in _sample(paths, lambda p, iv=iv: ("settle", iv, "yes") in p.steps, 6):
            i = p.steps.index(("settle", iv, "yes"))
            ch0 = chain()
            ch0.run(p.steps[:i])
            ch = chain()
            ch.run(p.steps[: i + 1])
            pd = np.full(ch.n, 29) if iv == "I0" else ch.E_ix + 30
            inside = pd < ch.N
            above = np.maximum(ch0.cum()[ch.rows, np.clip(pd, 0, ch.N - 1)] - fx.basis().need[ch.rows, np.clip(pd, 0, ch.N - 1)], 0)
            cap = np.full(ch.n, ch.claimed()) if iv == "I0" else ch0.owed_at(pd)
            so = ch.settle_offer
            assert (so[inside] <= np.minimum(above, cap)[inside]).all() and (so[~inside] == 0).all()
            paid = so > 0
            assert paid.any() and (ch.resolved[paid] == pd[paid]).all()
            assert (np.cumsum(ch.ev.lock, axis=1)[paid, -1] == 0).all()


def test_settlement_terms_are_the_bound_in_twelve_monthly_installments(tree):
    """The case's terms (Owen, 28 Sep 2026): the settlement amount (cash above the 30-day need on the settlement date,
    capped) paid in 12 equal monthly installments from the settlement date, those after the horizon outside it; the
    claim released on the settlement date. The lump sum is the sensitivity. Jev's settlement questions are told the
    total and the monthly schedule, and acceptance is asked on those terms."""
    from app.analysis.events import settlement_terms
    from app.finance.calendar import add_months

    assert settlement_terms(fx.model()) == ("installments", 12)
    assert settlement_terms(fx.model(), {"settlement_payment": True}) == ("lump_sum", 1)
    steps = (("settle", "I0", "yes"),)
    base, _ = run(())
    ch, tr = run(steps)
    lump, _ = run(steps, {"settlement_payment": True})
    so, pd = ch.settle_offer, 29
    paid = so > 0
    assert paid.any() and (lump.settle_offer == so).all() and (ch.resolved[paid] == pd).all()
    days = [(add_months(fx.REVIEW + __import__("datetime").timedelta(days=pd + 1), i) - fx.REVIEW).days - 1
            for i in range(12)]
    inside = [d for d in days if d < ch.N]
    assert 0 < len(inside) < 12  # payments due after the horizon fall outside it
    paid_cash = (tr.events.cash - base.ev.cash)
    legal = fx.basis().legal
    for i in np.flatnonzero(paid)[:20]:
        for k, d in enumerate(inside):
            part = so[i] // 12 + (so[i] % 12 if k == 11 else 0)
            assert paid_cash[i, d] == -part - legal[i, d]  # the installment, and legal spend stops from the release
    lump_cash = lump.ev.cash - base.ev.cash
    assert (lump_cash[paid, pd] == -so[paid] - legal[paid, pd]).all()
    fc, d, _, _ = tree
    keys = [k for k in fc.nodes if fc.nodes[k].node in ("settlement_offer", "settlement_accept") and "|I0|" in k]
    assert keys
    for k in keys:
        facts = fc.path_facts(fc.nodes[k], d)["settlement_offer"]
        assert "12 equal monthly installments" in facts["payment"] and "monthly_installment" in facts
        if fc.nodes[k].node == "settlement_accept":
            assert any("12 equal monthly installments" in a for a in fc.nodes[k].assumptions)


def test_questions_marked_cash_receive_cash_facts(tree):
    """Every question outside no_cash, the I0 settlement questions included (nothing is owed before the verdict),
    receives the cash at its decision date, unless its answer cancels on every path (a holders' petition after the
    period, merged into the class where nobody files: its reach is zero)."""
    from app.disputes.forecast import Dist, path_probability

    fc, _, paths, _ = tree
    rng = np.random.default_rng(3)
    base = {k: dict(zip(n.branches, rng.dirichlet(np.ones(len(n.branches))), strict=True)) for k, n in fc.nodes.items()}
    for k, n in fc.nodes.items():
        if n.question_id in fc.no_cash or "projected_available_cash_at_decision_date" in fc.state(n)[0]["path_facts"]:
            continue
        probs = [[path_probability(p.edges, Dist({**base, k: {b: float(b == x) for b in n.branches}})) for p in paths]
                 for x in n.branches]
        assert np.allclose(probs[0], probs[1]), k


def test_every_reading_event_has_its_question():
    """DisputeProfile.read asks event_<ev> for every event in readings.events; a missing question fails every
    instantiate_dispute (4.1.0's trial_pending had none)."""
    from app.agent.jev import registry_question
    from app.disputes.rules import load_model

    for ev in load_model()["readings"]["events"]:
        q = registry_question(f"event_{ev}")
        assert q["profile"] == "dispute_interpretation" and q["primitive"] == "noul"
