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


def hand_price_usd(owed_usd: np.ndarray) -> np.ndarray:
    """The share price by hand (QUESTIONS §2.6): a Black-Scholes call on the calibrated firm value, strike the notes'
    face plus the amount owed, one year at the 1-year par yield of 14 May 2024 (5.16%), per share outstanding."""
    from test_share_price import NOTES, SHARES, bs_call

    from app.analysis.share_price import model_for
    M = model_for(fx.model()["parameters"])
    return np.array([0.44 if o <= 0 else bs_call(M.V / 100, NOTES + o, 0.0516, M.asset_vol, 1.0) / SHARES
                     for o in np.ravel(owed_usd)])


CLAIMANT = 67_526_412  # USD, the claimant's damages theory entered (fixture judgment)
STEPS_67M = (("verdict", "I0", "claimant_theory"), ("judgment_response", "entry", "continue"),
             ("post_trial_motions", "", "no"), ("appeal", "", "no"), ("stay", "post", "no"),
             ("enforce", "post", "levy"), ("judgment_response", "post", "continue"),
             ("cash_floor", "1", "initiate_offering"), ("offering", "floor1", "yes"),
             ("cash_floor", "2", "initiate_offering"), ("offering", "floor2", "yes"))


def test_one_offerings_shares_and_the_ledger_when_capacity_binds():
    """The price: the share price on the initiation day (the structural price at the judgment less what was levied
    by then) less January's discount to its prior close ($0.50 on $0.7046); the shares, the January gross ($11.5M)
    over it, capped by the shares left; where capacity binds, the gross at the price and the net bearing the January
    costs in proportion. The ledger never below zero."""
    ch = chain()
    ch.run(STEPS_67M)
    first = ch._offers[0]
    r = np.flatnonzero(first["rows"])
    assert r.size and ch.entered == CLAIMANT * 100
    init = first["init"][r]
    levied = sum(np.where(t <= first["init"], a, 0) for t, a in ch.takes)[r] / 100  # USD, on or before initiation
    price = hand_price_usd(CLAIMANT - levied) * 0.50 / 0.7046  # USD
    full = np.rint(11_500_000 / price)
    left = LEDGER - ch.atm_shares_to_date(first["init"])[r]
    want = np.minimum(full, left)
    assert (np.abs(first["shares"][r] - want) <= 1).all()
    binds = want < full
    net = np.where(binds, want * price * 100 * 1_040_000_000 / 1_150_000_000, 1_040_000_000)
    assert (np.abs(first["net"][r] - net) <= 1e-5 * net).all()
    assert (ch.offerings[0][1][r] - init == 5).all()  # launch 24 Jan to close 29 Jan
    atm = ch.atm_shares_to_date(np.full(ch.n, ch.N - 1))
    held = sum(np.where(o["rows"] & o["closed"], o["shares"], 0) for o in ch._offers)
    assert (LEDGER - atm - held >= 0).all()


def test_at_the_market_proceeds_at_the_post_verdict_price():
    """A $67.5M judgment: the 3 Jun 2024 sale (settled T+1 on 4 Jun) sells the same 153,213 shares at the day's share
    price, the structural price at the amount entered (nothing levied yet), net of 3%."""
    ch = chain()
    ch.run(STEPS_67M)
    live = (ch.ev.petition < 0) & (ch.V < day(ch, date(2024, 6, 3)))
    got = ch.atm_to_date(day(ch, date(2024, 6, 4))) - ch.atm_to_date(day(ch, date(2024, 6, 3)))
    cents = float(hand_price_usd(np.array([CLAIMANT]))[0]) * 100
    net = int(Decimal(round(153_213 * cents)) * Decimal("0.97"))  # whole cents of gross, commission floored
    assert live.any() and (got[live] == net).all()
    assert abs(cents - 14.09) < 0.01  # $0.141 at $67.5M (Owen's table)


def test_an_offering_at_entry_prices_at_the_judgment_and_locks_up_the_sales():
    """The lower award ($1,426,412) with an offering at entry: its price is the structural price at that amount less
    January's discount, the January gross over it (capacity does not bind), January's net. The underwriting
    agreement's lock-up (§4(l), no at-the-market exception): no sale from pricing (launch + 1 day) through the 90th
    day after the close, every sale before and after it."""
    ch = chain()
    ch.run((("verdict", "I0", "without_principal_measure"), ("judgment_response", "entry", "initiate_offering"),
            ("offering", "entry", "yes")))
    o = ch._offers[0]
    r = o["rows"] & o["closed"] & (ch.ev.petition < 0)
    assert r.any() and ch.entered == 142_641_200
    price = float(hand_price_usd(np.array([1_426_412]))[0]) * 0.50 / 0.7046  # USD
    assert abs(float(hand_price_usd(np.array([1_426_412]))[0]) - 0.4296) < 1e-4  # Owen's table: $0.430 at $1.43M
    want = 11_500_000 / price  # the engine prices in 1/10,000 cent: within that rounding
    assert (np.abs(o["shares"][r] - want) <= 2e-6 * want).all() and (o["net"][r] == 1_040_000_000).all()
    sale, _, _, _ = ch._atm_schedule()
    for i in np.flatnonzero(r)[:20]:
        locked = (sale >= o["init"][i] + 1) & (sale <= o["close"][i] + 90)
        assert locked.any() and not ch._atm_sold[i][locked].any()
        assert ch._atm_sold[i][~locked & (sale < ch.N)].all()
    # a sale after the lock-up: 153,213 shares at the day's price (the lower award's) less 3%, received T+1
    d0, d1 = day(ch, date(2024, 10, 1)), day(ch, date(2024, 10, 2))
    got = ch.atm_to_date(np.full(ch.n, d1)) - ch.atm_to_date(np.full(ch.n, d0))
    cents = float(hand_price_usd(np.array([1_426_412]))[0]) * 100
    assert (got[r] == int(Decimal(round(153_213 * cents)) * Decimal("0.97"))).all()
