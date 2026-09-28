"""The 14 May pending-claim event model (dispute model 4.1.0): composition and cash only."""

from __future__ import annotations

import numpy as np
import pytest

import akoustis_20240514_fixture as fx
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
            after = np.arange(ch.N)[None, :] >= day[:, None]
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


def _sample(paths, pred, k=20):
    got = [p for p in paths if pred(p)]
    return got[:: max(1, len(got) // k)][:k]


def test_5_claimant_branch_is_cash_and_date_identical_under_its_enhancements(tree):
    """The claimant's branch is beyond cash under both claimant_enhancements settings: same cash, lock, petition, days."""
    _, _, paths, _ = tree
    for p in _sample(paths, lambda p: ("verdict", "I0", "claimant_theory") in p.steps):
        (a, ta), (b, tb) = run(p.steps), run(p.steps, {"claimant_enhancements": True})
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
    """§16.4: the cash every question's facts read (Chain.cum, whence tr.cash, pay feasibility and tau) equals the loan
    engine's available cash, run forward from the line's opening state on the prefix's event cash, at every step."""
    from app.analysis.engine import run as engine_run
    from app.analysis.events import EventCash, Trace

    _, _, paths, _ = tree
    b, s = fx.basis(), fx.setup()
    assert s.exposure.principal_cents > 0  # the line starts with an outstanding balance
    for p in _sample(paths, lambda p: True, 8):
        ch = chain()
        ch.instrument_cash()
        tr = Trace(ch.ev)
        for step in p.steps:
            ch.advance(tr, *step)
            ev = ch.ev
            eng = engine_run(b.line, b.opening - s.exposure.cash_cents,
                             EventCash(ev.cash.copy(), ev.lock.copy(), ev.capacity.copy(), ev.petition.copy()))
            assert (ch.cum() == eng.cash[:, :ch.N]).all(), step


def test_8_settlement_is_bounded_and_ends_the_claim(tree):
    """A settlement never exceeds cash above the 30-day need on its payment date, nor the amount claimed (I0) or owed;
    paid, it resolves the dispute (claim, lock and legal spend end)."""
    _, _, paths, _ = tree
    for iv in ("I0", "I1"):
        for p in _sample(paths, lambda p: ("settle", iv, "yes") in p.steps, 6):
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
