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

# The 14 May fixture: a settlement before the verdict, paid in monthly installments; the company keeps operating at its
# cash floor and when cash runs out, and stays listed. Draw 0 first leaves an obligation unpaid on day 132 (an
# operating outflow); Slope's debit then fails on its due dates, and settlement installments go unpaid from day 147
# on. §7.01(j)(v) general nonpayment is met on day 162 (30 days of arrears, unpaid share above a quarter).
STEPS = (("settle", "I0", "yes"), ("cash_floor", "", "continue"), ("cash_out", "", "no"), ("listing", "kept", "listed"))
ROW = 0
FIRST_UNPAID, NONPAYMENT = 132, 162
# arrears by class at the day's end: slope, settlement, notes_interest, judgment, operating (cents); cash is nil
HAND = {132: [0, 0, 0, 0, 68_011_178], 147: [3_040_951, 0, 0, 0, 162_205_434],
        162: [7_322_024, 51_876_728, 0, 0, 275_046_989], 177: [5_426_261, 51_876_728, 0, 0, 367_661_593]}


def test_arrears_the_first_unpaid_day_and_the_nonpayment_day_equal_a_hand_computation():
    b, s = fx.basis(), fx.setup()
    ch = Chain(fx.pending(), s, fx.model(), Draws(b.cash.shape[0], basis=b), None)
    ev = ch.run(STEPS).events
    tr = run(b.line, b.opening - s.exposure.cash_cents, ev, ch.nonpayment_terms())
    p = tr.processed
    assert (int(p.first_unpaid[ROW]), int(p.nonpayment[ROW])) == (FIRST_UNPAID, NONPAYMENT)
    assert (int(ch.cash_out()[ROW]), int(ch.nonpayment_day()[ROW])) == (FIRST_UNPAID, NONPAYMENT)  # the Chain's reads
    for t, want in HAND.items():
        assert p.arrears[ROW, t].tolist() == want, t
        assert tr.cash[ROW, t] == 0
    assert (tr.cash >= 0).all() and (ch.cum() == tr.cash).all()  # never negative; the Chain's cash is the engine's
