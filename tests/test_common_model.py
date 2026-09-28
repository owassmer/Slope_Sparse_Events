"""The common financial model's scenario controls (spec §16.3): each changes cash on exactly its stated dates, and the
central setting reproduces the previous simulation exactly."""

import json
from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from app.analysis import operating
from app.analysis.build import setup_from_json, setup_json
from app.analysis.engine import needs, prepare
from app.analysis.events import Basis
from app.analysis.setup import DRAWS, SEED, Financing, setup_from_inputs
from app.config import ROOT
from app.finance.bank import load_feed

SNAP, REVIEW = "akoustis_20240514", date(2024, 5, 14)
INPUTS = json.loads((ROOT / "cases" / SNAP / "run_inputs.json").read_text())
FEED = load_feed(SNAP)
DAYS = 180


def _sim(setup):
    ops = operating.simulate_for(FEED, setup)
    return ops, Basis.of(ops, prepare(setup, ops).need, FEED.available_cents)


@pytest.fixture(scope="module")
def central():
    return _sim(setup_from_inputs(INPUTS, REVIEW))


@pytest.mark.parametrize("snap,review", [(SNAP, REVIEW), ("akoustis_20240620", date(2024, 6, 20))])
def test_the_central_setting_is_the_plain_continuation(snap, review):
    inputs = json.loads((ROOT / "cases" / snap / "run_inputs.json").read_text())
    setup, feed = setup_from_inputs(inputs, review), load_feed(snap)
    assert (setup.need_days, setup.financing, setup.cost_plan) == (30, (), None)
    ops, plain = operating.simulate_for(feed, setup), operating.simulate(feed, DAYS + 30, DRAWS, SEED)
    assert ops.financing is None
    assert (ops.total == plain.total).all() and (ops.invoices == plain.invoices).all()
    assert all((ops.by_category[k] == plain.by_category[k]).all() for k in plain.by_category)
    assert (prepare(setup, ops).need == needs(plain, DAYS, 30)).all()


def test_equity_moves_cash_by_its_amount_from_its_day_and_leaves_the_need_alone(central):
    ops0, b0 = central
    setup = setup_from_inputs(INPUTS, REVIEW, "equity_injection")
    (f,) = setup.financing
    assert (f.on, f.amount_cents, f.kind) == (date(2024, 6, 14), 500_000_000, "equity")
    ops, b = _sim(setup)
    t = (f.on - REVIEW).days - 1
    diff = b.cash - b0.cash
    assert (diff[:, :t] == 0).all() and (diff[:, t:] == f.amount_cents).all()
    assert (b.need == b0.need).all() and (b.legal == b0.legal).all()


def test_debt_books_its_proceeds_and_its_service_on_their_dates(central):
    ops0, _ = central
    loan = Financing(on=date(2024, 6, 3), amount_cents=500_000_000, kind="debt",
                     service=((date(2024, 7, 1), 5_000_000), (date(2024, 8, 1), 5_000_000)))
    setup = replace(setup_from_inputs(INPUTS, REVIEW), financing=(loan,))
    ops = operating.simulate_for(FEED, setup)
    day = {d: (d - REVIEW).days - 1 for d in (loan.on, *(d for d, _ in loan.service))}
    diff = ops.total - ops0.total
    expect = np.zeros(diff.shape[1], dtype=np.int64)
    expect[day[loan.on]] = loan.amount_cents
    for d, c in loan.service:
        expect[day[d]] -= c
    assert (diff == expect[None, :]).all()
    ds = ops.by_category["debt_service"] - ops0.by_category["debt_service"]
    assert (ds == np.minimum(expect, 0)[None, :]).all()  # the service is debt service: the limit rule nets it
    with pytest.raises(ValueError):
        Financing(on=date(2024, 6, 3), amount_cents=1, kind="equity", service=((date(2024, 7, 1), 1),))


def test_the_tax_credit_receipts_land_on_their_dates_outside_the_need(central):
    ops0, b0 = central
    setup = setup_from_inputs(INPUTS, REVIEW, "chips_itc_low")
    assert {f.kind for f in setup.financing} == {"receipt"}
    assert sum(f.amount_cents for f in setup.financing) == 280_000_000 * 180 // 365  # the low end, over the horizon
    ops, b = _sim(setup)
    expect = np.zeros(ops.total.shape[1], dtype=np.int64)
    for f in setup.financing:
        expect[(f.on - REVIEW).days - 1] += f.amount_cents
    assert (ops.total - ops0.total == expect[None, :]).all()
    assert (b.need == b0.need).all()
    with pytest.raises(ValueError):
        Financing(on=date(2024, 6, 3), amount_cents=1, kind="receipt", service=((date(2024, 7, 1), 1),))


def test_the_cost_plan_cuts_outflows_from_its_start_and_nothing_else(central):
    ops0, _ = central
    setup = setup_from_inputs(INPUTS, REVIEW, "cost_plan")
    plan = setup.cost_plan
    assert plan is not None and plan.start == date(2024, 5, 15) and plan.share_bps == 1659
    ops = operating.simulate_for(FEED, setup)
    t0 = (plan.start - REVIEW).days - 1
    assert (ops.total[:, :t0] == ops0.total[:, :t0]).all()
    for k in ("customer_receipts", "legal_fees", "debt_service"):  # receipts, the legal proxy, debt: untouched
        assert (ops.by_category[k] == ops0.by_category[k]).all()
    # The saving is the share of every other outflow (payroll, vendors, capital, the invoices), each day, to the
    # rounding of one cent per stream and invoice slot.
    out0 = ops0.total - sum(ops0.by_category.values())
    assert (out0 <= 0).all()  # this feed's other flows are all outflows
    diff = (ops.total - ops0.total)[:, t0:]
    slots = 1 + (ops0.invoices != 0).sum(axis=2)[:, t0:]
    assert (np.abs(diff + plan.share_bps / 10_000 * out0[:, t0:]) <= slots).all()
    assert (np.abs(ops.invoices) <= np.abs(ops0.invoices)).all()


def test_the_cash_floor_is_one_setting_everything_reads(central):
    ops0, b0 = central
    setup = setup_from_inputs(INPUTS, REVIEW, "cash_floor_60_days")
    assert setup.need_days == 60
    ops, b = _sim(setup)
    assert (ops.total[:, :DAYS + 30] == ops0.total).all()  # the same operating flows, looked at further ahead
    assert (b.cash == b0.cash).all()
    assert (b.need == needs(ops, DAYS, 60)[:, :DAYS]).all() and (b.need >= b0.need).all() and (b.need > b0.need).any()


def test_the_controls_survive_the_recorded_setup():
    for name in ("central", "equity_injection", "cost_plan", "chips_itc_low", "cash_floor_60_days"):
        setup = setup_from_inputs(INPUTS, REVIEW, name)
        assert setup_from_json(json.loads(json.dumps(setup_json(setup)))) == setup
