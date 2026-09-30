"""The daily cash processor (QUESTIONS_20240514 §2.2 and §3.3) against figures computed by hand.

The pinned figures come from a separate day-by-day scalar computation of §2.2 and §3.3 that reads only the
trajectory's operating receipts and payments, its event cash by kind and the line's schedule (limit, routed invoices,
due dates, month-ends, the opening exposure). It shares no code with app/analysis/processor.py, and it reads §3.3's
unpaid part arrear by arrear, not through the processor's min(arrears, arrears created in the window) shortcut.
"""

from __future__ import annotations

import akoustis_20240514_fixture as fx

from app.analysis.engine import run
from app.analysis.events import Chain, Draws

# The 14 May fixture: a settlement before the verdict, paid in monthly installments; the stock keeps its listing and the
# company keeps operating at its cash floor and when cash runs out. Under the central equity model the at-the-market
# receipts keep every trajectory paying (no obligation goes unpaid), so the path takes three declared sensitivities
# (scenario.json): the coupon in cash, at-the-market sales at 10% of the volume, and the 15-day nonpayment window.
# Draw 0 first leaves an obligation unpaid on day 151 (a settlement installment the balance cannot cover in full);
# §7.01(j)(v) general nonpayment is met on day 177. The hand computation pays arrears as §2.2 states them: from the
# available balance after the day's obligations, oldest first, a scheduled arrear in full or not at all.
STEPS = (("settle", "I0", "yes"), ("listing", "", "compliant"), ("cash_floor", "1", "neither"), ("cash_out", "", "neither"))
SENS = {"coupon_cash_share": True, "atm_pace_bps": 1000, "nonpayment_window_days": 15}
ROW = 0
FIRST_UNPAID, NONPAYMENT = 151, 177
# arrears by class at the day's end: slope, settlement, notes_interest, judgment, operating (cents); and the cash
HAND = {151: ([0, 57_598_430, 0, 0, 0], 32_004_982), 160: ([0, 0, 0, 0, 122_943_247], 0),
        170: ([0, 0, 0, 0, 75_072_643], 0), 177: ([0, 0, 0, 0, 178_630_568], 0)}


def test_arrears_the_first_unpaid_day_and_the_nonpayment_day_equal_a_hand_computation():
    b, s = fx.basis(), fx.setup()
    ch = Chain(fx.pending(), s, fx.model(), Draws(b.cash.shape[0], basis=b), SENS)
    ev = ch.run(STEPS).events
    tr = run(b.line, b.opening - s.exposure.cash_cents, ev, ch.nonpayment_terms())
    p = tr.processed
    assert (int(p.first_unpaid[ROW]), int(p.nonpayment[ROW])) == (FIRST_UNPAID, NONPAYMENT)
    assert (int(ch.cash_out()[ROW]), int(ch.nonpayment_day()[ROW])) == (FIRST_UNPAID, NONPAYMENT)  # the Chain's reads
    for t, (want, cash) in HAND.items():
        assert p.arrears[ROW, t].tolist() == want, t
        assert tr.cash[ROW, t] == cash, t
    assert (tr.cash >= 0).all() and (ch.cum() == tr.cash).all()  # never negative; the Chain's cash is the engine's


def test_cash_only_matches_full_daily_outputs():
    import numpy as np

    b, s = fx.basis(), fx.setup()
    ch = Chain(fx.pending(), s, fx.model(), Draws(b.cash.shape[0], basis=b), SENS)
    ev = ch.run(STEPS).events
    # Include a petition while arrears remain outstanding, and absence of nonpayment terms.
    for petition in (None, 165, 90, 75):
        if petition is not None:
            ev.petition[:] = petition
        for terms in (None, ch.nonpayment_terms()):
            full = run(b.line, b.opening - s.exposure.cash_cents, ev, terms)
            light = run(b.line, b.opening - s.exposure.cash_cents, ev, terms, cash_only=True)
            expected = (full.cash, full.processed.first_unpaid, full.processed.nonpayment,
                        full.processed.arrears)
            for actual, want in zip(light, expected, strict=True):
                if want is None:
                    assert actual is None
                else:
                    np.testing.assert_array_equal(actual, want)


def test_prefix_reuse_preserves_book_arrears_and_earlier_dated_changes(monkeypatch):
    import numpy as np

    from app.analysis import processor

    b, s = fx.basis(), fx.setup()
    ch = Chain(fx.pending(), s, fx.model(), Draws(b.cash.shape[0], basis=b), SENS)
    ev = ch.run(STEPS).events
    processor._PREFIX_RUNS.clear()
    monkeypatch.setattr(processor, '_PREFIX_BYTES', 0)
    monkeypatch.setenv('SLOPE_DAILY_PREFIX', '1')
    kernel, starts = processor._daily_kernel, []

    def observed(*args):
        starts.append(args[-4])
        return kernel(*args)

    monkeypatch.setattr(processor, '_daily_kernel', observed)
    opening, terms = b.opening - s.exposure.cash_cents, ch.nonpayment_terms()
    for day, expected_start in ((None, 0), (150, b.line.days // 2), (30, 0)):
        if day is not None:
            ev.kinds['settlement'] = ev.kinds['settlement'].copy()
            ev.cash = ev.cash.copy()
            ev.kinds['settlement'][0, day] -= 100_000_000
            ev.cash[0, day] -= 100_000_000
        light = run(b.line, opening, ev, terms, cash_only=True)
        assert starts[-1] == expected_start
        full = run(b.line, opening, ev, terms)
        for actual, expected in zip(light, (full.cash, full.processed.first_unpaid,
                                           full.processed.nonpayment, full.processed.arrears), strict=True):
            np.testing.assert_array_equal(actual, expected)
