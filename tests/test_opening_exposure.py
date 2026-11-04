"""A line opened before the review date: the forecast starts from its exposure (Setup.exposure), and installments in
flight are collected, block draws and are stayed exactly as a new draw's (spec §2.2-2.3, §16.2)."""

import hashlib
import json
from dataclasses import fields, replace
from datetime import date, timedelta

import numpy as np

from app.analysis.engine import installment_amounts, prepare, run
from app.analysis.events import EventCash
from app.analysis.operating import Operating, simulate_for
from app.analysis.setup import DRAWS, Exposure, setup_from_inputs
from app.config import ROOT
from app.finance.bank import load_feed

REVIEW = date(2024, 5, 14)
INPUTS = json.loads((ROOT / "cases" / "akoustis_20240514" / "run_inputs.json").read_text())
# these pins are the net engine's (the rule they were made under); the case itself processes cash daily
INPUTS["common_model"]["central"]["cash_processing"] = "net"
SETUP = setup_from_inputs(INPUTS, REVIEW)
DAYS = 180


def digest(tr) -> str:
    h = hashlib.sha256()
    for f in fields(tr):
        if f.name not in ("opening_principal", "failed_debits"):  # failed_debits: added later, not a trajectory
            a = np.ascontiguousarray(getattr(tr, f.name))
            if a.dtype.kind == "f":  # PVs are BLAS matmuls: last bits vary by platform; integer arrays stay bit-exact
                a = np.rint(a) + 0.0  # whole cents (dollar-days), -0.0 folded into 0.0
            h.update(f.name.encode())
            h.update(a.tobytes())
    return h.hexdigest()[:16]


def test_an_empty_exposure_reproduces_the_new_line_bit_for_bit():
    """Digests of every Trajectories array from the engine before the opening exposure existed (7c845fa): the 20 Jun
    case's central run, without events and with a $5M drain on day 40 and petitions on day 120 on even draws."""
    inputs = json.loads((ROOT / "cases" / "akoustis_20240620" / "run_inputs.json").read_text())
    feed, setup = load_feed("akoustis_20240620"), setup_from_inputs(inputs, date(2024, 6, 20))
    assert setup.exposure == Exposure()
    line = prepare(setup, simulate_for(feed, setup))
    ev = EventCash.zeros(DRAWS, line.days)
    ev.cash[:, 40] = -500_000_000
    ev.petition[::2] = 120
    got = [digest(run(line, feed.available_cents, e)) for e in (EventCash.zeros(DRAWS, line.days), ev)]
    assert got == ["06bc807e3c2e9baa", "b886a643e9878b74"]
    s14 = replace(SETUP, exposure=Exposure(), collection="protect_need")  # the rule the pin was made under
    feed14 = load_feed("akoustis_20240514")
    assert digest(run(prepare(s14, simulate_for(feed14, s14)), feed14.available_cents,
                      EventCash.zeros(DRAWS, DAYS))) == "7fb95e406ec33acb"


def _ops(rows: int, invoices: dict[int, int], day0: bool) -> Operating:
    """Hand-built draws: supplier invoices {day: cents} (the day-0 one routable only when `day0`), no other flows,
    a $1.5M limit (15% of $10M a month)."""
    total = np.zeros((rows, DAYS), dtype=np.int64)
    inv = np.zeros((rows, DAYS, 1), dtype=np.int64)
    for t, c in invoices.items():
        total[:, t] -= c
        if t or day0:
            inv[:, t, 0] = -c
    zeros = np.zeros((rows, DAYS), dtype=np.int64)
    history = {(2024, m): 1_000_000_000 for m in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11)}
    return Operating(total, inv, {"customer_receipts": zeros, "debt_service": zeros, "legal_fees": zeros}, history)


def test_installments_in_flight_are_collected_and_stayed_exactly_as_a_new_draws():
    """The same $1M draw, made on day 0 of the forecast (new) or carried in as the opening exposure (in flight: its
    three installments on the same dates, principal $1M, the supplier payment already in the feed). Row 0: ample cash.
    Row 1: cash drained before the first installment (partial collection, arrears, month-end retries, no new draws).
    Row 2: a petition five days after the first due date. Row 3: a petition before it. A $600k invoice on day 10 fails
    the limit test ($1M + $600k > $1.5M) and one on day 40 passes it once the first installment is collected."""
    x = 100_000_000
    inv = {0: x, 10: 60_000_000, 40: 60_000_000}
    new_line = prepare(replace(SETUP, exposure=Exposure()), _ops(4, inv, day0=True))
    parts = new_line.due_idx[0]
    new = run(new_line, 200_000_000, ev := _events(parts))
    amounts = installment_amounts(np.array([x]), SETUP.fee_bps, SETUP.installments)[0]
    sched = tuple((REVIEW + timedelta(days=int(i) + 1), int(c)) for i, c in zip(parts, amounts, strict=True))
    ex = Exposure(installments=sched, principal_cents=x, cash_cents=x)
    old = run(prepare(replace(SETUP, exposure=ex), _ops(4, inv, day0=False)), 200_000_000, ev)

    assert new.drawn[0] == x + 60_000_000 and new.drawn[1] == x  # the day-40 invoice: funded on row 0, arrears on 1
    assert new.collections[1].sum() < new.due[1].sum() and new.petition[2:].tolist() == [int(parts[0]) + 5, 5]
    assert (old.drawn == new.drawn - x).all() and old.opening_principal == x and (old.fees == new.fees).all()
    skip = {"drawn", "fundings", "pv_fundings", "lender_pv", "draw_rows", "draw_days", "draw_amounts",
            "opening_principal"}
    for f in fields(new):
        if f.name not in skip:
            assert np.array_equal(getattr(new, f.name), getattr(old, f.name)), f.name
    later = new.fundings.copy()
    later[:, 0] -= x
    assert np.array_equal(later, old.fundings)
    ident = old.collected + old.stayed + old.not_yet_due + old.uncollected
    assert (ident == old.contractual).all() and old.stayed[2] > 0 and old.preference[2] > 0


def _events(parts) -> EventCash:
    ev = EventCash.zeros(4, DAYS)
    ev.cash[1, int(parts[0]) - 3] = -195_000_000
    ev.petition[2], ev.petition[3] = int(parts[0]) + 5, 5
    return ev


def test_the_14_may_forecast_starts_from_the_lines_state_on_the_review_date():
    feed, ex = load_feed("akoustis_20240514"), SETUP.exposure
    assert ex.principal_cents > 0 and ex.installments
    line = prepare(SETUP, simulate_for(feed, SETUP))
    tr = run(line, feed.available_cents, EventCash.zeros(DRAWS, line.days))
    assert (tr.outstanding[:, 0] >= ex.principal_cents).all()  # nothing falls due on day 0
    for d, c in ex.installments:  # on every trajectory, each opening installment falls due on its date
        assert (tr.due[:, (d - REVIEW).days - 1] >= c).all()
    assert (tr.contractual >= ex.owed_cents).all() and (tr.drawn == tr.fundings.sum(axis=1)).all()
    ident = tr.collected + tr.stayed + tr.not_yet_due + tr.uncollected
    assert (ident == tr.contractual).all()
    rows, days = tr.draw_rows, tr.draw_days  # every new draw passed the limit test with the opening principal counted
    assert (tr.outstanding[rows, days] <= line.limit[rows, days]).all()
    assert tr.collections[:, :(ex.installments[0][0] - REVIEW).days - 1].sum() == 0


def test_a_recorded_setup_carries_the_opening_exposure():
    from app.analysis.build import setup_from_json, setup_json

    assert setup_from_json(json.loads(json.dumps(setup_json(SETUP)))) == SETUP
    bare = replace(SETUP, exposure=Exposure())
    assert "exposure" not in setup_json(bare) and setup_from_json(setup_json(bare)) == bare
