"""Slope's reusable line, the collection rule and the petition (Model Extensions Spec §2, tests §12), on the Akoustis
connected-bank feed. Financial correctness only: limit, draw gating, installment arithmetic, the contract identity,
the preference window, the need rule and the one-time exit of a routed invoice from the borrower's outflows."""

import json
from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from app.analysis import operating
from app.analysis.engine import NEED_DAYS, installment_amounts, prepare, run, with_petition
from app.analysis.events import EventCash
from app.analysis.operating import Operating
from app.analysis.setup import DRAWS, SEED, setup_from_inputs
from app.config import ROOT
from app.finance.bank import load_feed
from app.finance.slope_products import SlopeOffer

SNAP = "akoustis_20240620"
REVIEW = date(2024, 6, 20)
INPUTS = json.loads((ROOT / "cases" / SNAP / "run_inputs.json").read_text())
SETUP = setup_from_inputs(INPUTS, REVIEW)
DAYS = 180


@pytest.fixture(scope="module")
def akoustis():
    feed = load_feed(SNAP)
    ops = operating.simulate(feed, DAYS + NEED_DAYS, DRAWS, SEED)
    return feed, ops, prepare(SETUP, ops)


@pytest.fixture(scope="module")
def stressed(akoustis):
    """A $15M cash drain on day 40 (so installments go overdue) and a petition on day 120 on even draws."""
    feed, _, line = akoustis
    ev = EventCash.zeros(DRAWS, DAYS)
    ev.cash[:, 40] = -1_500_000_000
    ev.petition[::2] = 120
    return run(line, feed.available_cents, ev)


def tiny(invoice_cents: int, rows: int, flows: dict[int, int] | None = None) -> Operating:
    """Hand-built operating draws: one supplier invoice on day 0, optional other flows, a $1.5M limit at review."""
    total = np.zeros((rows, DAYS), dtype=np.int64)
    inv = np.zeros((rows, DAYS, 1), dtype=np.int64)
    inv[:, 0, 0] = -invoice_cents
    total[:, 0] = -invoice_cents
    for t, c in (flows or {}).items():
        total[:, t] += c
    zeros = np.zeros((rows, DAYS), dtype=np.int64)
    history = {(2024, m): 1_000_000_000 for m in (3, 4, 5)}  # 15% of $10M a month
    return Operating(total, inv, {"customer_receipts": zeros, "debt_service": zeros, "legal_fees": zeros}, history)


def test_the_limit_is_slopes_rule_on_each_trajectorys_trailing_months(akoustis):
    _, ops, line = akoustis
    assert INPUTS["financing_plan"]["line"]["limit_cents"] == 35_717_237 and (line.limit[:, 0] == 35_717_237).all()
    top = prepare(replace(SETUP, limit_multiplier=33 / 15), ops)
    assert (top.limit[:, 0] == (267_919_355 + 223_212_698 + 223_212_699) * 3300 // 30_000).all()
    # 1 Aug (day 41): May from the feed, June from the feed to 20 June plus the simulation, July simulated
    net = ops.by_category["customer_receipts"] + ops.by_category["debt_service"]
    june = ops.history[(2024, 6)] + net[:, :10].sum(axis=1)
    july = net[:, 10:41].sum(axis=1)
    expected = np.maximum((ops.history[(2024, 5)] + june + july) * 1500 // 30_000, 0)
    assert (line.limit[:, 41] == expected).all() and (line.limit[:, 40] != line.limit[:, 41]).any()


def test_draws_respect_the_limit_arrears_and_the_petition(akoustis, stressed):
    _, _, line = akoustis
    tr = stressed
    drew = tr.fundings > 0
    assert (tr.outstanding[drew] <= line.limit[drew]).all()
    owed = np.cumsum(tr.due, axis=1) - np.cumsum(tr.collections, axis=1)
    assert (owed > 0).any() and not (drew & (owed > 0)).any()  # arrears occur, and no draw is made while they last
    assert not tr.fundings[::2, 120:].any() and not tr.collections[::2, 120:].any()
    assert tr.fundings[1::2, 120:].any() or tr.collections[1::2, 120:].any()  # the line runs on without a petition


def test_installments_and_the_contract_identity(stressed):
    tr = stressed
    for a in (1, 2, 99, 30_973_624, 35_717_237, 12_345_679):  # the reference arithmetic, cent for cent
        offer = SlopeOffer("x", "x", a, SETUP.fee_bps, 90, 3, "x")
        assert installment_amounts(np.array([a]), SETUP.fee_bps, 3)[0].tolist() == \
            [p.amount_cents for p in offer.schedule(REVIEW)]
    totals = np.zeros(DRAWS, dtype=np.int64)
    np.add.at(totals, tr.draw_rows, [SlopeOffer("x", "x", int(a), SETUP.fee_bps, 90, 3, "x").total_cents
                                     for a in tr.draw_amounts])
    assert (totals == tr.contractual).all() and (np.bincount(tr.draw_rows, tr.draw_amounts, DRAWS) == tr.drawn).all()
    per_draw = np.bincount(tr.draw_rows, minlength=DRAWS)  # Σ fundings x 1.037 = Σ installments, to a half cent a draw
    assert (np.abs(tr.contractual * 10_000 - tr.drawn * 10_370) <= 5_000 * per_draw).all()
    parts = (tr.collected, tr.stayed, tr.not_yet_due, tr.uncollected)
    assert (sum(parts) == tr.contractual).all() and all((p >= 0).all() for p in parts)
    odd, even = slice(1, None, 2), slice(0, None, 2)
    assert (tr.stayed[odd] == 0).all() and (tr.not_yet_due[even] == 0).all() and (tr.stayed[even] > 0).any()
    assert (tr.due[odd].sum(axis=1) + tr.not_yet_due[odd] == tr.contractual[odd]).all()
    assert (tr.uncollected[odd] > 0).any() and (tr.not_yet_due[odd] > 0).any()


def test_the_preference_window_is_exactly_the_90_days_before_the_petition():
    # $300k at no fee on day 0: three $100k installments, all collected on their due dates (cash is ample)
    line = prepare(replace(SETUP, fee_bps=0), tiny(30_000_000, 3))
    d1, d2, _ = line.due_idx[0]
    ev = with_petition(EventCash.zeros(3, DAYS), np.array([d1 + 90, d1 + 91, d2]))
    tr = run(line, 1_000_000_000, ev)
    assert tr.drawn.tolist() == [30_000_000] * 3
    assert tr.preference.tolist() == [30_000_000, 20_000_000, 10_000_000]  # d1 in, d1 out, d2 (the petition day) out
    assert tr.stayed.tolist() == [0, 0, 20_000_000] and tr.collected.tolist() == [30_000_000, 30_000_000, 10_000_000]


def test_the_need_rule_reproduces_the_previous_collections_when_need_is_zero(akoustis):
    # Opening $1.0M; Slope pays a $900k invoice (no fee); a $950k payment leaves $50k, so the first $300k installment
    # collects $50k and nothing more. A $20k dip 5-40 days after the first due date is need; with it, $30k.
    st = replace(SETUP, fee_bps=0)
    hit = EventCash.zeros(2, DAYS)
    hit.cash[1, 0] = -95_000_000
    for flows, need_days, first in (({}, NEED_DAYS, 5_000_000), ({}, 0, 5_000_000)):
        tr = run(prepare(st, tiny(90_000_000, 2, flows), need_days), 100_000_000, hit)
        d1 = prepare(st, tiny(90_000_000, 2)).due_idx[0, 0]
        assert tr.collections[:, d1].tolist() == [30_000_000, first]
        assert tr.uncollected.tolist() == [0, 90_000_000 - first]
    d1 = prepare(st, tiny(90_000_000, 2)).due_idx[0, 0]
    dip = {d1 + 5: -2_000_000, d1 + 40: 2_000_000}
    assert run(prepare(st, tiny(90_000_000, 2, dip)), 100_000_000, hit).collections[:, d1].tolist() == \
        [30_000_000, 3_000_000]
    assert run(prepare(st, tiny(90_000_000, 2, dip), 0), 100_000_000, hit).collections[:, d1].tolist() == \
        [30_000_000, 5_000_000]
    # On the Akoustis draws with need 0: the engine's collections are exactly the previous rule's, replayed on the
    # same contractual schedule and fundings (collect min(owed, max(available, 0)) on due dates and at month-ends).
    feed, ops, _ = akoustis
    line0 = prepare(SETUP, ops, need_days=0)
    ev = EventCash.zeros(DRAWS, DAYS)
    ev.cash[:, 40] = -1_500_000_000
    tr = run(line0, feed.available_cents, ev)
    before = feed.available_cents + np.cumsum(ops.total[:, :DAYS] + ev.cash + tr.fundings, axis=1)
    owed, taken, old = np.zeros(DRAWS, np.int64), np.zeros(DRAWS, np.int64), np.zeros((DRAWS, DAYS), np.int64)
    for t in range(DAYS):
        owed += tr.due[:, t]
        if tr.due[:, t].any() or line0.month_end[t]:
            take = np.minimum(owed, np.maximum(before[:, t] - taken, 0))
            old[:, t], owed, taken = take, owed - take, taken + take
    assert (old == tr.collections).all() and (tr.uncollected > 0).any()


def test_a_routed_invoice_leaves_the_borrowers_outflows_exactly_once(akoustis):
    feed, ops, line = akoustis
    on = run(line, feed.available_cents, EventCash.zeros(DRAWS, DAYS))
    off = run(prepare(replace(SETUP, line_usage=0.0), ops), feed.available_cents, EventCash.zeros(DRAWS, DAYS))
    assert not off.fundings.any() and on.drawn.min() > 0
    assert (on.cash - off.cash == np.cumsum(on.fundings - on.collections, axis=1)).all()
    for r, t, a in zip(on.draw_rows[:200], on.draw_days[:200], on.draw_amounts[:200], strict=True):
        assert a in line.routes[r, t]  # each draw is one whole simulated invoice
    half = prepare(replace(SETUP, line_usage=0.5), ops)
    assert 0.3 < (half.routes > 0).sum() / (line.routes > 0).sum() < 0.7


def test_petitions_combine_to_the_earliest():
    a, b = EventCash.zeros(4, 3), EventCash.zeros(4, 3)
    a.petition[:] = [-1, 5, -1, 9]
    b.petition[:] = [-1, -1, 7, 3]
    assert (a + b).petition.tolist() == [-1, 5, 7, 3] and EventCash.zeros(2, 3).petition.tolist() == [-1, -1]
