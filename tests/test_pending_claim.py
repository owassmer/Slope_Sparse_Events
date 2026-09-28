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
