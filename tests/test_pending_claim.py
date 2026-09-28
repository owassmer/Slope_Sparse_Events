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
