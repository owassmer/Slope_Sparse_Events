"""Collection and the cash floor are separate settings (spec §16.3). `debit`: Slope's automatic debit collects each
installment in full when available cash covers it, else it fails and is retried; `protect_need`: the borrower keeps
its operating need back (the previous rule). `need_days` sets the cash floor and limits collections only under
`protect_need`."""

import hashlib
import json
from dataclasses import fields, replace
from datetime import date

import numpy as np
import pytest

from app.analysis.engine import prepare, run, run_many
from app.analysis.events import EventCash
from app.analysis.operating import Operating, simulate_for
from app.analysis.setup import DRAWS, Exposure, Setup, controls_json, setup_from_inputs
from app.config import ROOT
from app.finance.bank import load_feed

REVIEW = date(2024, 5, 14)
INPUTS = json.loads((ROOT / "cases" / "akoustis_20240514" / "run_inputs.json").read_text())
DAYS = 180
X = 10_000_000  # a $100k installment
JUN3, JUN30, JUL31 = 19, 46, 77  # day indices (day t is the review date + t + 1)


def digest(tr) -> str:
    h = hashlib.sha256()
    for f in fields(tr):
        if f.name not in ("opening_principal", "failed_debits"):
            h.update(f.name.encode())
            h.update(np.ascontiguousarray(getattr(tr, f.name)).tobytes())
    return h.hexdigest()[:16]


def test_the_case_central_is_debit_with_a_30_day_floor_and_protect_need_is_a_scenario():
    c = setup_from_inputs(INPUTS, REVIEW)
    assert (c.collection, c.need_days) == ("debit", 30)
    assert setup_from_inputs(INPUTS, REVIEW, "protect_need").collection == "protect_need"
    floor = setup_from_inputs(INPUTS, REVIEW, "cash_floor_60_days")
    assert (floor.collection, floor.need_days) == ("debit", 60)
    assert controls_json(c)["collection"] == "debit"
    with pytest.raises(ValueError):
        replace(c, collection="partial")


def test_protect_need_reproduces_the_engine_before_the_split_bit_for_bit():
    """Digests of every Trajectories array from the engine before this change (4bbec3e + the spec merge), on the
    14 May case with its opening exposure: no events, and a $5M drain on day 40 with petitions on day 120 on even
    draws; the central settings, the 60-day floor, the equity injection and the cost plan."""
    pinned = {("central", 30): ["9bb63ec1418367b2", "deb2c65e7cbaac7a"],
              ("central", 60): ["89e45f56ef701d49", "a3eed6065af997d5"],
              ("equity_injection", 30): ["42747e0c2df46518", "8e1fd70b6861bc31"],
              ("cost_plan", 30): ["fcf6206313b74cf3", "f3a6c42528999455"]}
    feed = load_feed("akoustis_20240514")
    for (sc, nd), want in pinned.items():
        s = replace(setup_from_inputs(INPUTS, REVIEW, sc), need_days=nd, collection="protect_need")
        line = prepare(s, simulate_for(feed, s))
        ev = EventCash.zeros(DRAWS, line.days)
        ev.cash[:, 40] = -500_000_000
        ev.petition[::2] = 120
        got = [digest(t) for t in run_many(line, feed.available_cents, [EventCash.zeros(DRAWS, line.days), ev])]
        assert got == want, (sc, nd)


def _ops(flows: list[dict[int, int]]) -> Operating:
    """One row per dict of {day: cents} operating flows; no invoices; a $1.5M limit."""
    total = np.zeros((len(flows), DAYS + 60), dtype=np.int64)
    for i, f in enumerate(flows):
        for t, c in f.items():
            total[i, t] += c
    zeros = np.zeros_like(total)
    history = {(2024, m): 1_000_000_000 for m in range(2, 12)}
    return Operating(total, np.zeros((len(flows), DAYS + 60, 1), dtype=np.int64),
                     {"customer_receipts": zeros, "debt_service": zeros, "legal_fees": zeros}, history)


def _setup(mode: str, need_days: int = 30, installments=((date(2024, 6, 3), X),)) -> Setup:
    ex = Exposure(installments=tuple(installments), principal_cents=sum(c for _, c in installments) * 9 // 10)
    return replace(setup_from_inputs(INPUTS, REVIEW), exposure=ex, collection=mode, need_days=need_days)


# row 0: exactly covered; row 1: one cent short until +1 on day 25; row 2: one cent short throughout;
# row 3: covered, but X of operating outflow on day 40 (a 30-day need of X on the due date)
FLOWS = [{0: X}, {0: X - 1, 25: 1}, {0: X - 1}, {0: X, 40: -X}]


def test_debit_collects_an_installment_in_full_exactly_when_cash_covers_it_and_retries_a_failure_at_month_end():
    tr = run(prepare(s := _setup("debit"), _ops(FLOWS)), 0, EventCash.zeros(4, DAYS))
    c = tr.collections
    assert c[0, JUN3] == X and c[0].sum() == X and tr.cash[0, JUN3] == 0
    assert c[1, JUN3] == 0 and c[1, 25] == 0 and c[1, JUN30] == X and c[1].sum() == X  # failed, retried at month-end
    assert tr.failed_debits[1] == 1
    assert c[2].sum() == 0 and tr.uncollected[2] == X  # never a partial debit
    month_ends = int(prepare(s, _ops(FLOWS)).month_end[JUN3 + 1:].sum())
    assert tr.failed_debits[2] == 1 + month_ends  # the due date and every month-end after it
    assert c[3, JUN3] == X and tr.failed_debits[0] == tr.failed_debits[3] == 0


def test_debit_attempts_each_installment_oldest_first_and_collects_any_that_cash_covers():
    """3 Jun X (oldest) and 3 Jul X/2: with 0.6X on hand the older debit fails and the smaller one succeeds."""
    two = ((date(2024, 6, 3), X), (date(2024, 7, 3), X // 2))
    tr = run(prepare(_setup("debit", installments=two), _ops([{0: 6 * X // 10}])), 0, EventCash.zeros(1, DAYS))
    jul3 = (date(2024, 7, 3) - REVIEW).days - 1
    assert tr.collections[0, :jul3].sum() == 0 and tr.collections[0, jul3] == X // 2
    assert tr.collected[0] == X // 2 and tr.uncollected[0] == X
    tr = run(prepare(_setup("debit", installments=two), _ops([{0: 3 * X // 2}])), 0, EventCash.zeros(1, DAYS))
    assert tr.collections[0, JUN3] == X and tr.collected[0] == 3 * X // 2  # both, each on its own date


def test_under_debit_need_days_changes_no_collection():
    runs = [run(prepare(_setup("debit", nd), _ops(FLOWS)), 0, EventCash.zeros(4, DAYS)) for nd in (0, 30, 60)]
    for tr in runs[1:]:
        for k in ("collections", "cash", "collected", "uncollected", "failed_debits", "outstanding"):
            assert np.array_equal(getattr(tr, k), getattr(runs[0], k)), k
    # the same row under protect_need: the 30-day need (X) keeps the debit back on the due date
    pn = run(prepare(_setup("protect_need", 30), _ops(FLOWS)), 0, EventCash.zeros(4, DAYS))
    assert pn.collections[3, JUN3] == 0 and runs[1].collections[3, JUN3] == X
    feed = load_feed("akoustis_20240514")
    base = setup_from_inputs(INPUTS, REVIEW)
    case = [run(prepare(s, simulate_for(feed, s)), feed.available_cents, EventCash.zeros(DRAWS, DAYS))
            for s in (replace(base, need_days=nd) for nd in (30, 60))]
    assert np.array_equal(case[0].collections, case[1].collections)
    assert np.array_equal(case[0].fundings, case[1].fundings)


def test_under_debit_a_petition_stays_everything_from_its_day():
    ev = EventCash.zeros(4, DAYS)
    ev.petition[:] = 30
    flows = [{**f, 35: 5 * X} for f in FLOWS]  # cash arrives after the petition: nothing is collected from it
    tr = run(prepare(_setup("debit"), _ops(flows)), 0, ev)
    assert tr.collections[:, 30:].sum() == 0 and tr.fundings[:, 30:].sum() == 0
    assert tr.stayed.tolist() == [0, X, X, 0] and tr.uncollected.sum() == 0  # row 1: its 30 Jun retry is stayed
    feed = load_feed("akoustis_20240514")
    s = setup_from_inputs(INPUTS, REVIEW)
    ev = EventCash.zeros(DRAWS, DAYS)
    ev.petition[::2] = 60
    tr = run(prepare(s, simulate_for(feed, s)), feed.available_cents, ev)
    assert tr.collections[::2, 60:].sum() == 0 and tr.fundings[::2, 60:].sum() == 0
    assert np.array_equal(tr.stayed[::2], tr.contractual[::2] - tr.collected[::2])
    assert np.array_equal(tr.collected + tr.stayed + tr.not_yet_due + tr.uncollected, tr.contractual)
