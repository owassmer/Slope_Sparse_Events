"""The dated walk (app/exact/tree.py) against the Python walk's own histories: each history of `ChronologicalWalk`
(`oracle.histories`, both styles) followed through the dated walk on each of its draws, its answers given to the
elementary questions at their own dates. Every question the dated walk asks is one the history answers, and every
decision the history answers comes up; the bookings at the end equal the Python's. Allowed, each for its reason:
- a settlement question in the window after the appeal deadline (I3) on a path whose stay pending appeal the court
  approves later: the Python's walk leaves it out because it already knows the approval; at its date the stay is not
  approved, so the dated walk asks it;
- an early registration ordered after a petition: the dated walk does not ask a court question after the petition
  (the Python books the order, which reaches nothing);
- a writ that books nothing leaves a zero take in the Python;
- a stay approved on the day of another event: the court's order is the day's first event (Owen, 29 October)."""
import collections

import numpy as np
import pytest

from app.analysis.events import Chain
from app.exact import export_notes, oracle, pass1, replay, tree
from app.exact import fields as f
from app.exact import fields_agenda as a
from app.exact import fields_walk as w

DRAWS = (0, 145, 365)
SKIP = [f.SETTLE_OFFER, f.STAY_OFFER, f.RAISE_OFFER, f.READS, f.COLLATERAL_REQUIRED, *range(f.S_DISTRESS, f.NI)]


def _nonzero_takes(T, n):
    return [tuple(x) for x in T[:n] if x[1] != 0]


@pytest.mark.parametrize("style", ["uniform", "long"])
def test_the_dated_walk_follows_the_python_histories(style):
    fc0, d0 = oracle.context(DRAWS)  # the walk that generated the histories: its verdict classes
    counts = collections.Counter()
    for seed in (2, 5, 13):
        for h in oracle.histories(8, DRAWS, seed=seed, style=style):
            fc, d = oracle.context(h.draws)
            py = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
            py.run(h.path.steps)
            ch0 = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
            ch0.instrument_cash()
            tk = pass1.case_tables(fc0, d0, ch0)
            for r in range(ch0.n):
                found, st, _, _, (qs, recs, _, cls) = pass1.follow(fc0, d0, ch0, r, tk, h.path.steps, questions=True)
                counts["records"] += len(qs)
                where = f"{style} seed {seed} draw {h.draws[r]} {h.path.steps}"
                for kind, dec, _day, _want in found:
                    if kind == tree.F_ASKED:
                        counts["asked"] += 1
                        continue
                    assert kind == tree.F_EXTRA and dec == w.DEC_SETTLE0 + a.SETTLE_CTX.index("I3") \
                        and ("stay", "post", "yes") in h.path.steps, (kind, dec, where)
                    counts["I3 before the approval"] += 1
                want = export_notes.notes_state(py, r, replay.OCCASION_IDS)
                S, wS = st.S.copy(), want[0].copy()
                wS[f.MARK_STAYED] = py.marks_now()["stayed"][r]
                S[SKIP] = wS[SKIP] = 0
                if _nonzero_takes(st.takes, st.S[f.N_TAKES]) == _nonzero_takes(want[3], want[0][f.N_TAKES]):
                    S[f.N_TAKES] = wS[f.N_TAKES] = 0
                pet = BIGP if (p := int(st.S[f.PETITION])) < 0 else p
                if S[f.EARLY_REGISTRATION] != wS[f.EARLY_REGISTRATION] and wS[f.EARLY_REGISTRATION] >= pet:
                    S[f.EARLY_REGISTRATION] = wS[f.EARLY_REGISTRATION]
                    counts["registration after the petition"] += 1
                same_day = any(st.stays[j, a.T_APPROVED] and int(st.stays[j, a.T_APPROVAL]) == pet
                               for j in range(st.S[a.N_STAYS]))
                if same_day:
                    counts["approval on the petition's day"] += 1
                    continue
                np.testing.assert_array_equal(S, wS, err_msg=where)
                D = st.D.copy()
                D[f.D_STAYS] = 0
                np.testing.assert_array_equal(D, want[1], err_msg=f"D {where}")
                counts["history-draws"] += 1
    print(style, dict(counts))


BIGP = f.BIG
