"""QUESTIONS_20240514 §2.6: at-the-market proceeds, an offering's shares and the share ledger, against hand
computations from the price series and the record's figures (§5: 'for one month, at-the-market proceeds and the shares
drawn equal a hand computation from §2.6; the share ledger never goes below zero')."""

from __future__ import annotations

import csv
from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

import akoustis_20240514_fixture as fx
import numpy as np

from app.analysis.events import Chain, Draws
from app.config import CASES_DIR

LEDGER = 175_000_000 - 98_669_282 - 9_341_825 - 3_031_625 - 5_000_000  # §2.6 (10-Q of 13 May 2024; resale S-3)


def chain(sens=None):
    b = fx.basis()
    return Chain(fx.pending(), fx.setup(), fx.model(), Draws(b.cash.shape[0], basis=b), sens)


def adv_usd() -> Decimal:
    """Average daily dollar volume, 15 Mar to 14 May 2024: close x volume over the trading days of the case's series."""
    rows = [r for r in csv.DictReader((CASES_DIR / fx.SNAP / "akts_daily_px.csv").open())
            if "2024-03-15" <= r["date"] <= "2024-05-14"]
    return sum((Decimal(r["close"]) * Decimal(r["volume"]) for r in rows), Decimal(0)) / len(rows)


def day(ch, d: date) -> np.ndarray:
    return np.full(ch.n, ch.ix(d), dtype=np.int64)


def test_the_ledger_and_the_volume_are_the_records():
    p = fx.model()["parameters"]
    assert p["share_ledger"]["value"] == LEDGER == 58_957_268
    assert p["atm_pace_bps"]["adv_cents"] == int((adv_usd() * 100).quantize(Decimal(1), ROUND_HALF_UP))


def test_one_month_of_at_the_market_proceeds_and_shares():
    """June 2024, by hand: 20% of the average dollar volume a trading day at the 14 May close ($0.44), whole shares;
    net of 3%; received T+1 (from 28 May). Received in June: the sales of 31 May and of 3-27 June (Juneteenth closed),
    19 sales; traded in June: 3-28 June less 19 June, 19 sales."""
    shares = int((adv_usd() * Decimal("0.20") / Decimal("0.44")).to_integral_value(ROUND_FLOOR))
    net = int((Decimal(shares) * 44 * Decimal("0.97")).to_integral_value(ROUND_FLOOR))  # cents
    ch = chain()
    ch.run((("verdict", "I0", "no_award"),))
    live = ch.ev.petition < 0
    received = ch.atm_to_date(day(ch, date(2024, 6, 30))) - ch.atm_to_date(day(ch, date(2024, 5, 31)))
    traded = ch.atm_shares_to_date(day(ch, date(2024, 6, 30))) - ch.atm_shares_to_date(day(ch, date(2024, 5, 31)))
    assert live.any() and (received[live] == 19 * net).all() and (traded[live] == 19 * shares).all()
    assert shares == 153_213 and net == 6_539_130  # $67,413.89 of volume a day
    # T+2 before 28 May: the 14 May sale arrives on 16 May, the 24 May sale on 29 May (Memorial Day), 28 May's on 29 May
    first = ch.atm_to_date(day(ch, date(2024, 5, 16))) - ch.atm_to_date(day(ch, date(2024, 5, 15)))
    assert (first[live] == net).all()
    both = ch.atm_to_date(day(ch, date(2024, 5, 29))) - ch.atm_to_date(day(ch, date(2024, 5, 28)))
    assert (both[live] == 2 * net).all()


def test_one_offerings_shares_and_the_ledger_when_capacity_binds():
    """The price: the 14 May close less January's discount to its prior close ($0.50 on $0.7046); the shares, the
    January gross ($11.5M) over it. A second offering after the first closed and the at-the-market sales: the shares
    left, its gross at the price and the net bearing the January costs in proportion. The ledger never below zero."""
    price = Decimal("0.44") * Decimal("0.50") / Decimal("0.7046")  # USD
    full = int((Decimal("11500000") / price.quantize(Decimal("0.000001"), ROUND_HALF_UP)).quantize(
        Decimal(1), ROUND_HALF_UP))
    ch = chain()
    steps = (("verdict", "I0", "claimant_theory"), ("judgment_response", "entry", "continue"),
             ("post_trial_motions", "", "no"), ("appeal", "", "no"), ("stay", "post", "no"), ("enforce", "post", "levy"),
             ("judgment_response", "post", "continue"), ("cash_floor", "1", "initiate_offering"),
             ("offering", "floor1", "yes"), ("cash_floor", "2", "initiate_offering"), ("offering", "floor2", "yes"))
    ch.run(steps)
    first, second = ch._offers[0], ch._offers[1] if len(ch._offers) > 1 else None
    r = first["rows"]
    assert r.any() and abs(int(first["shares"][r][0]) - full) <= 1 and (first["net"][r] == 1_040_000_000).all()
    assert (ch.offerings[0][1][r] - ch.offerings[0][0][r] == 5).all()  # launch 24 Jan to close 29 Jan
    atm = ch.atm_shares_to_date(np.full(ch.n, ch.N - 1))
    held = sum(np.where(o["rows"] & o["closed"], o["shares"], 0) for o in ch._offers)
    assert (LEDGER - atm - held >= 0).all()
    if second is not None and second["rows"].any():
        s = second["rows"]
        left = LEDGER - ch.atm_shares_to_date(second["init"])[s] - first["shares"][s] * first["closed"][s]
        assert (second["shares"][s] == np.minimum(full, left)).all()
        binds = second["shares"][s] < full
        gross = second["shares"][s] * 312_234 // 10_000
        assert (second["net"][s][binds] == gross[binds] * 1_040_000_000 // 1_150_000_000).all()
