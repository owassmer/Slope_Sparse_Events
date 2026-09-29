"""What the step-9 page reads from the analysis (core.Reduction): each equity channel's proceeds per path, and the
daily arrears by class. Two hand paths of tests/test_equity_20240514.py, no tree."""

from __future__ import annotations

from datetime import date, timedelta

import akoustis_20240514_fixture as fx
import numpy as np
import pytest
from test_equity_20240514 import CLAIMANT, LEDGER, hand_price_usd, interest_usd

from app.analysis.core import ARREARS_KEYS, Analysis, EventModel
from app.analysis.engine import run_many
from app.analysis.events import Chain, Draws
from app.disputes.forecast import DisputePath
from app.finance.bank import load_feed

NO_AWARD = (("verdict", "I0", "no_award"),)
OFFERING = (("verdict", "I0", "claimant_theory"), ("judgment_response", "entry", "continue"),
            ("post_trial_motions", "", "no"), ("appeal", "", "no"), ("stay", "post", "no"), ("enforce", "post", "levy"),
            ("judgment_response", "post", "continue"), ("cash_floor", "1", "initiate_offering"),
            ("offering", "floor1", "yes"))
NET_SALE = 6_539_130  # 153,213 shares at $0.44 less 3% (test_equity_20240514)


@pytest.fixture(scope="module")
def analysis():
    d = fx.pending()
    combos = [(DisputePath(d.instance_id, s, "x", ()),) for s in (NO_AWARD, OFFERING)]
    return Analysis(load_feed(fx.SNAP), fx.setup(), EventModel({d.instance_id: d}, {}, {}, [], combos=combos),
                    dispute_model=fx.model()), d


def _sales_settled_by(horizon: date) -> int:
    """By hand from the 2024 calendar: trading days from 14 May, settled T+2 (T+1 from 28 May) by the horizon."""
    closed = {date(2024, 5, 27), date(2024, 6, 19), date(2024, 7, 4), date(2024, 9, 2), date(2024, 11, 28)}
    banks = closed | {date(2024, 10, 14), date(2024, 11, 11)}
    n, x = 0, date(2024, 5, 14)
    while x <= horizon:
        if x.weekday() < 5 and x not in closed:
            s, k = x, 0
            while k < (2 if x < date(2024, 5, 28) else 1):
                s += timedelta(days=1)
                k += s.weekday() < 5 and s not in banks
            n += s <= horizon
        x += timedelta(days=1)
    return n


def test_the_proceeds_by_channel_are_the_hand_figures(analysis):
    """No award, nothing stops the sales: every sale settled by the horizon (124 by 10 Nov), none from an offering.
    The offering path: the net at the initiation day's share price where the offering closed (capacity binds), and the two channels are the path's receipts."""
    a, d = analysis
    m = a.r.means
    assert _sales_settled_by(fx.setup().horizon) == 124
    assert m["atm_proceeds"][0] == 124 * NET_SALE and m["offering_proceeds"][0] == 0
    ch = Chain(d, a.setup, a.m, Draws(a._draws.n, basis=a._draws.basis), None)
    ch.run(OFFERING)
    o = ch._offers[0]  # the share price at initiation binds the capacity: the net by hand (test_equity_20240514)
    levied = sum(np.where(t <= o["init"], x, 0) for t, x in ch.takes) / 100
    owed = CLAIMANT + interest_usd(CLAIMANT, o["init"] - ch.E_ix) - levied  # §1961 from the entry
    price = hand_price_usd(owed) * 0.50 / 0.7046
    shares = np.minimum(np.rint(11_500_000 / price), LEDGER - ch.atm_shares_to_date(o["init"]))
    net = np.where(o["closed"], np.minimum(shares * price * 100 * 1_040_000_000 / 1_150_000_000, 1_040_000_000), 0)
    assert abs(m["offering_proceeds"][1] - net.mean()) <= 1e-5 * net.mean()
    for s in (NO_AWARD, OFFERING):
        ev = Chain(d, a.setup, a.m, Draws(a._draws.n, basis=a._draws.basis), None).run(s).events
        assert (ev.proceeds["atm_proceeds"] + ev.proceeds["offering_proceeds"] == ev.kinds["inflow"].sum(axis=1)).all()


def test_the_daily_arrears_are_the_processors_summed_over_draws(analysis):
    a, d = analysis
    total = 0
    for i, s in enumerate((NO_AWARD, OFFERING)):
        ev = Chain(d, a.setup, a.m, Draws(a._draws.n, basis=a._draws.basis), None).run(s).events
        arr = run_many(a.line, a.opening, [ev])[0].processed.arrears.sum(axis=0)  # [days, classes]
        for j, k in enumerate(ARREARS_KEYS):
            assert (a.r.per_day[k][i] == arr[:, j]).all(), k
        total += int(arr.sum())
    assert total > 0  # the check reaches arrears


def test_the_reweighted_arrears_are_the_renormalised_weighted_sums(analysis):
    """page.chart_view renormalises the path weights (3:7 reads as 0.3/0.7) and weights each path's daily sums."""
    from app.analysis.page import chart_view

    a, _ = analysis
    view = chart_view(a.r.for_reweight(), np.array([3.0, 7.0]), a.months)  # renormalised
    last = {k: v[-1] for k, v in view["arrears"].items()}
    expect = {k.removeprefix("arrears_"): (0.3 * a.r.per_day[k][0, -1] + 0.7 * a.r.per_day[k][1, -1]) / a.r.draws
              for k in ARREARS_KEYS}
    assert all(abs(last[k] - expect[k]) <= 0.5 for k in last)
