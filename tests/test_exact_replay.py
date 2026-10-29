"""The dated walk's agenda (app/exact/agenda.py, slice 1) against `events.Chain.run`, history by history: random real
histories without an approved stay or an agreed settlement, replayed compiled on each draw from the state at the review
date; the chain's state at the end (the event cash by kind, the lock, the petition and its cause, the marks, the
judgment's state, the takes, the offerings, the at-the-market sales, the coupons) and every step's record (day,
cash, owed, collateral) identical to the Python's."""
import numpy as np
import pytest

from app.analysis.events import Chain, plain
from app.exact import agenda, export_notes, oracle, replay
from app.exact import fields as f

DRAWS = (0, 145, 365)
# what each step resets or the stays compute (slice 2), and the dated walk's own block
SKIP = [f.SETTLE_OFFER, f.STAY_OFFER, f.RAISE_OFFER, f.READS, f.COLLATERAL_REQUIRED, f.LOCK_AMOUNT, f.LOCK_DAY,
        *range(f.S_DISTRESS, f.S_WALK)]


def slice1(steps):
    return not any((node == "stay" and plain(b) == "yes") or (node == "settle" and plain(b) == "yes")
                   for node, _, b in steps)


@pytest.mark.parametrize("style", ["uniform", "long"])
def test_replay_matches_the_python(style):
    counts = {"histories": 0, "draws": 0, "steps": 0, "skipped": 0}
    for seed in (2, 5, 13, 21):
        for h in oracle.histories(8, DRAWS, seed=seed, style=style):
            if not slice1(h.path.steps):
                counts["skipped"] += 1
                continue
            fc, d = oracle.context(h.draws)
            py = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
            tr = py.run(h.path.steps)
            ch0 = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
            ch0.instrument_cash()
            for r in range(ch0.n):
                k, ln = replay.constants(ch0, r)
                st = replay.state(ch0, r, ln)
                for step in h.path.steps:
                    agenda.advance(st, k, replay.encode(ch0, step))
                agenda.finish(st, k)
                want = export_notes.notes_state(py, r, replay.OCCASION_IDS)
                where = f"{style} seed {seed} history {counts['histories']} draw {r}"
                S, ws = st.S.copy(), want[0].copy()
                S[SKIP] = ws[SKIP] = 0
                np.testing.assert_array_equal(S, ws, err_msg=where)
                for name, got, exp in zip(("D", "P", "takes", "offers", "atm", "answers", "parts", "coupons"),
                                          (st.D, st.P, st.takes, st.offers, st.atm, st.answers, st.parts, st.coupons),
                                          want[1:], strict=True):
                    np.testing.assert_array_equal(got, exp, err_msg=f"{name} {where}")
                n = len(h.path.steps)
                for col, lst in enumerate((tr.day, tr.cash, tr.owed, tr.collateral)):
                    np.testing.assert_array_equal(st.rec[:n, col], [int(np.asarray(x)[r]) for x in lst],
                                                  err_msg=f"rec {col} {where}")
                counts["draws"] += 1
                counts["steps"] += n
            counts["histories"] += 1
    assert counts["histories"] > 0
    print(style, counts)
