"""The compiled walk's scheduler (app/exact/walk.py) against the Python walk's own histories: random real histories of
`ChronologicalWalk` (`oracle.histories`, both styles), each followed on each of its draws through the compiled
frontier. At every point the frontier's earliest decision is the history's next step, or one that does not arise
there; every step of the history is reached, in its order; and what the walk books along the way is exactly what the
Python books for the history (the state at the end and every step's record)."""
import numpy as np
import pytest

from app.analysis.events import Chain
from app.exact import agenda, export_notes, oracle, replay, walk
from app.exact import fields as f
from app.exact import fields_agenda as a

DRAWS = (0, 145, 365)
SKIP = [f.SETTLE_OFFER, f.STAY_OFFER, f.RAISE_OFFER, f.READS, f.COLLATERAL_REQUIRED, *range(f.S_DISTRESS, f.NI)]


@pytest.mark.parametrize("style", ["uniform", "long"])
def test_the_frontier_follows_the_python_walk(style):
    counts = {"histories": 0, "draws": 0, "steps": 0, "absent": 0, "deterministic": 0}
    for seed in (2, 5, 13):
        for h in oracle.histories(8, DRAWS, seed=seed, style=style):
            fc, d = oracle.context(h.draws)
            py = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
            tr = py.run(h.path.steps)
            ch0 = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
            ch0.instrument_cash()
            steps = np.array([replay.encode(ch0, s) for s in h.path.steps], dtype=np.int64)
            asked = np.array([not s[2].startswith("@-1") for s in h.path.steps], dtype=np.bool_)
            wk = walk.WK(listing=ch0.fin is not None and ch0.fin.listing_deadline is not None)
            for r in range(ch0.n):
                k, ln = replay.constants(ch0, r)
                st = replay.state(ch0, r, ln)
                ws = replay.workspace(ch0, r, st)
                trace = np.zeros((512, 4), np.int64)
                taken, t = walk.follow(st, k, wk, ws, steps, asked, trace)
                where = f"{style} seed {seed} history {counts['histories']} draw {r} {h.path.steps}"
                assert taken == len(steps), f"steps from {taken} never reached: {where}; trace {trace[:t].tolist()}"
                agenda.finish(st, k)
                want = export_notes.notes_state(py, r, replay.OCCASION_IDS)
                S, wS = st.S.copy(), want[0].copy()
                wS[f.MARK_STAYED] = py.marks_now()["stayed"][r]
                S[SKIP] = wS[SKIP] = 0
                same_day = any(st.stays[j, a.T_APPROVED] and int(st.stays[j, a.T_APPROVAL]) in
                               {int(x) for x in st.rec[:st.S[a.N_STEPS], 0]} | {int(st.S[f.PETITION]), int(st.S[f.F])}
                               for j in range(st.S[a.N_STAYS]))
                if not same_day:  # the court's order first on its day (Owen, 29 October): the Python sizes on the day
                    np.testing.assert_array_equal(S, wS, err_msg=where)
                    D = st.D.copy()
                    D[f.D_STAYS] = 0
                    np.testing.assert_array_equal(D, want[1], err_msg=f"D {where}")
                    for col, lst in enumerate((tr.day, tr.cash, tr.owed, tr.collateral)):
                        np.testing.assert_array_equal(st.rec[:len(steps), col], [int(np.asarray(x)[r]) for x in lst],
                                                      err_msg=f"rec {col} {where}")
                kinds = trace[:t, 0]
                counts["absent"] += int(((kinds == 1) & (trace[:t, 3] < 0)).sum())
                counts["deterministic"] += int((kinds > 1).sum())
                counts["draws"] += 1
                counts["steps"] += len(steps)
            counts["histories"] += 1
    print(style, counts)
