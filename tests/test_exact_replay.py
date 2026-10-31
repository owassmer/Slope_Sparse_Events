"""The dated walk's agenda (app/exact/agenda.py) against `events.Chain.run`, history by history: random real histories
replayed compiled on each draw from the state at the review date; the chain's state at the end (the event cash by kind,
the lock with every stay's security, the petition and its cause, the marks, the judgment's state, the stays' effects,
the takes, the offerings, the at-the-market sales, the settlement's parts, the coupons) and every step's record (day,
cash, owed, collateral) identical to the Python's."""
import numpy as np
import pytest

from app.analysis.events import Chain
from app.exact import agenda, export_notes, oracle, replay
from app.exact import fields as f

DRAWS = (0, 145, 365)
# what each step resets for its own record, and the dated walk's own block
SKIP = [f.SETTLE_OFFER, f.STAY_OFFER, f.RAISE_OFFER, f.READS, f.COLLATERAL_REQUIRED, *range(f.S_DISTRESS, f.S_WALK)]


def same_day(st, py, r):
    """An approved stay's approval day is also the day of another event on the draw (a step, the petition, the
    ruling): the court's order is that day's first event (Owen, 29 October), while the Python sizes the security on
    the whole state, that day's other events included."""
    days = {int(x) for x in st.rec[:st.S[agenda.a.N_STEPS], 0]} | {int(st.S[f.PETITION]), int(st.S[f.F])}
    for j in range(st.S[agenda.a.N_STAYS]):
        row = st.stays[j]
        if row[agenda.a.T_APPROVED] and int(row[agenda.a.T_APPROVAL]) in days:
            return True
    return False


@pytest.mark.parametrize("style", ["uniform", "long"])
def test_replay_matches_the_python(style):
    counts = {"histories": 0, "draws": 0, "steps": 0}
    for seed in (2, 5, 13, 21):
        for h in oracle.histories(8, DRAWS, seed=seed, style=style):
            fc, d = oracle.context(h.draws)
            py = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
            tr = py.run(h.path.steps)
            ch0 = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
            ch0.instrument_cash()
            for r in range(ch0.n):
                k, ln = replay.constants(ch0, r)
                st = replay.state(ch0, r, ln)
                ws = replay.workspace(ch0, r, st)
                for step in h.path.steps:
                    agenda.advance(st, k, replay.encode(ch0, step), ws)
                agenda.finish(st, k)
                if same_day(st, py, r):  # the class Owen ruled on (29 October): the compiled order is the rule
                    counts["same-day approval"] = counts.get("same-day approval", 0) + 1
                    continue
                want = export_notes.notes_state(py, r, replay.OCCASION_IDS)
                where = f"{style} seed {seed} history {counts['histories']} draw {r} {h.path.steps}"
                S, wS = st.S.copy(), want[0].copy()
                wS[f.MARK_STAYED] = py.marks_now()["stayed"][r]  # computed under daily processing
                S[SKIP] = wS[SKIP] = 0
                np.testing.assert_array_equal(S, wS, err_msg=where)
                D, wD = st.D.copy(), want[1].copy()
                np.testing.assert_array_equal(D[f.D_LOCK] + D[agenda.D_STAY_LOCK], py.lock_cash()[r],
                                              err_msg=f"lock {where}")
                D[agenda.D_STAY_LOCK] = 0
                np.testing.assert_array_equal(D, wD, err_msg=f"D {where}")
                for name, got, exp in zip(("P", "takes", "offers", "atm", "answers", "parts", "coupons"),
                                          (st.P, st.takes, st.offers, st.atm, st.answers,
                                           st.parts[:st.S[agenda.a.N_PARTS]], st.coupons), want[2:], strict=True):
                    np.testing.assert_array_equal(got, exp, err_msg=f"{name} {where}")
                n = len(h.path.steps)
                for col, lst in enumerate((tr.day, tr.cash, tr.owed, tr.collateral)):
                    np.testing.assert_array_equal(st.rec[:n, col], [int(np.asarray(x)[r]) for x in lst],
                                                  err_msg=f"rec {col} {where}")
                counts["draws"] += 1
                counts["steps"] += n
            counts["histories"] += 1
            for node, _, b in h.path.steps:
                if node in ("stay", "settle") and b.endswith("yes"):
                    counts[f"{node} yes"] = counts.get(f"{node} yes", 0) + 1
    assert counts["histories"] > 0
    print(style, counts)
