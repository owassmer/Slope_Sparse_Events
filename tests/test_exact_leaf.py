"""A history's figures at its leaf in the dated walk (app/exact/leaf.py) against the analysis's: the Python walk's
histories followed through the dated walk on each of their draws, the leaf's per-day sums and scalars equal to what
`core.Reduction.add` records from `engine.run` on the history's `event_trace` events (the analysis's run), draw by
draw. Where the dated walk books a history differently by design (tests/test_exact_tree.py's classes), the draw is
set apart."""
import collections

import numpy as np
import pytest

from app.analysis import core
from app.analysis.engine import run
from app.analysis.events import Chain, event_trace
from app.analysis.page import CAUSE_CLASS, draw_classes
from app.exact import fields as f
from app.exact import fields_agenda as a
from app.exact import leaf, oracle, pass1, pass2, tree

DRAWS = (0, 145, 365)


def _expected(t, r, setup, days, facility):
    """`core.Reduction.add`'s per-day rows and `_scalars` for draw r of trajectories t."""
    s = core._scalars(t)
    pet = int(t.petition[r])
    idx = np.arange(days)
    by = (pet >= 0) & (pet <= idx)
    window = (pet >= 0) & (idx >= pet - 90) & (idx < pet)
    cum_due, cum_coll = np.cumsum(t.due[r]), np.cumsum(t.collections[r])
    day = {"cash": t.cash[r], "backup": t.cash[r] + np.maximum(facility - t.capacity[r], 0), "collected": cum_coll,
           "due_cum": cum_due, "fundings": t.fundings[r], "collections": t.collections[r],
           "outstanding": t.outstanding[r], "locked": t.locked[r], "capacity": t.capacity[r],
           "petitioned": by.astype(float), "frozen": by * t.stayed[r], "frozen_due": (cum_due - cum_coll) * by,
           "past_due": (cum_due - cum_coll) * ~by, "clawback": t.collections[r] * window}
    return day, {k: float(v[r]) for k, v in s.items()}


@pytest.mark.parametrize("style", ["uniform", "long"])
def test_leaf_figures_match_the_analysis(style):
    fc0, d0 = oracle.context(DRAWS)
    counts = collections.Counter()
    for seed in (2, 5):
        for h in oracle.histories(8, DRAWS, seed=seed, style=style):
            fc, d = oracle.context(h.draws)
            ch0 = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
            ch0.instrument_cash()
            tk = pass1.case_tables(fc0, d0, ch0)
            tr = event_trace(d, h.path, fc.setup, fc.m, fc.draws, fc.sens)
            ev = tr.events
            classes = draw_classes(h.path.steps, tr.marks, ch0.N) if tr.marks else None
            line = ch0.basis.line
            t = run(line, ch0.basis.opening - ch0.s.exposure.cash_cents, ev)
            b = pass2.bins(ch0, np.zeros(ch0.N), np.zeros(ch0.N))
            lk = pass2.constants(ch0, b)
            for r in range(ch0.n):
                found, st, _, _ = pass1.follow(fc0, d0, ch0, r, tk, h.path.steps)
                if any(k_ != tree.F_ASKED for k_, *_ in found) or any(
                        st.stays[j, a.T_APPROVED] and st.stays[j, a.T_APPROVAL] == st.S[f.PETITION]
                        for j in range(st.S[a.N_STAYS])):
                    counts["set apart"] += 1
                    continue
                acc = pass2.accumulators(ch0, b)
                F = np.zeros(leaf.nf(pass2.months_of(ch0)))
                leaf.accumulate(st, pass1.replay.constants(ch0, r)[0], lk, 1.0, acc["day"], acc["scalars"],
                                acc["cash"], acc["collected"], acc["headroom"], acc["low"], acc["floor"], acc["raised"],
                                acc["ev_lo"], acc["ev_hi"], F)
                day, sc = _expected(t, r, ch0.s, ch0.N, int(ch0.s.facility_cents))
                where = f"{style} seed {seed} draw {h.draws[r]} {h.path.steps}"
                for i, name in enumerate(leaf.DAILY_NAMES):
                    np.testing.assert_array_equal(acc["day"][i], day[name], err_msg=f"{name} {where}")
                for j, cls in enumerate(range(5)):
                    np.testing.assert_array_equal(acc["day"][leaf.L_ARREARS0 + j], t.processed.arrears[r][:, cls],
                                                  err_msg=f"arrears {j} {where}")
                for name, i in leaf.SC.items():
                    if name in sc:
                        np.testing.assert_allclose(acc["scalars"][i], sc[name], rtol=1e-12, atol=1e-6,
                                                   err_msg=f"{name} {where}")
                for name in ("atm_proceeds", "offering_proceeds"):
                    assert acc["scalars"][leaf.SC[name]] == ev.proceeds[name][r], (name, where)
                # the leaf's own vector (weight one): the scalars, and each day row by month (a flow summed, a stock
                # at the month's last day)
                np.testing.assert_array_equal(F[:leaf.NSC], acc["scalars"], err_msg=where)
                _, month_of = pass2.calendar(ch0.s, ch0.N)
                for i in range(leaf.NDAY):
                    by_month = [acc["day"][i][month_of == m].sum() if i in leaf.FLOWS
                                else acc["day"][i][month_of == m][-1] for m in range(pass2.months_of(ch0))]
                    np.testing.assert_allclose(F[leaf.NSC + i * len(by_month):leaf.NSC + (i + 1) * len(by_month)],
                                               by_month, rtol=1e-12, atol=1e-6, err_msg=where)
                # the first day below the operating need (core.Reduction.floor_day; the last slot: never)
                below = t.cash[r] < line.need[r, :ch0.N]
                assert acc["floor"][int(below.argmax()) if below.any() else ch0.N] == 1.0, where
                # the outcome class at the period's end (page.outcome_shares: filed by cause, else draw_classes)
                cause = int(tr.cause[r]) if tr.cause is not None else 0
                want = CAUSE_CLASS[cause] if cause else (classes[r] if classes is not None else "unresolved")
                got = [n for n in leaf.SCALAR_NAMES if n.startswith("o_") and acc["scalars"][leaf.SC[n]] == 1.0]
                assert got == ["o_" + want], (got, want, where)
                counts["history-draws"] += 1
    assert counts["history-draws"] > 0
    print(style, dict(counts))
