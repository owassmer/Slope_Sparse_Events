"""The exact evaluator's cash reads (app/exact/cash.py) against `events.Chain`'s, after every step of random real
histories: the compiled line run on the event cash the step left gives the Chain's end-of-day cash on every day, its
first unpaid day and §3.3 day, and its balance at processing on many days; and run forward from the line's state saved
at a day before which nothing changed since, the same."""
import numpy as np
import pytest

from app.analysis import events
from app.analysis import line_state as ls
from app.exact import cash, export, oracle
from app.exact import fields as f

DRAWS = (0, 145, 365)
DAYS = (-1, 0, 1, 20, 45, 90, 120, 150, 179, 10**6)


@pytest.mark.parametrize("style", ["uniform", "long"])
def test_cash_reads_match_the_python(monkeypatch, style):
    counts = {"steps": 0, "runs": 0, "balance": 0, "resumed": 0, "triggers": 0}
    original = events.Chain.step
    lines, saved = {}, {}

    def step(ch, node, ctx, branch):
        out = original(ch, node, ctx, branch)
        if not ch.daily:
            return out
        cum, first, nonpay = ch.processed()
        lock = ch.lock_cash()
        bal = {t: ch.processing_balance(np.full(ch.n, t, dtype=np.int64)) for t in DAYS}
        tau, out_ = ch.tau(), ch.cash_out()
        fall = {p: ch._fall_after(np.full(ch.n, p, dtype=np.int64)) for p in (-1, 0, 30, 90, 150)}
        for r in range(ch.n):
            key = (id(ch.basis), r)
            if key not in lines:
                lines[key] = cash.line_for(ch, r)
            ln = lines[key]
            S, D, _ = export.chain_state(ch, r)
            lk = np.ascontiguousarray(lock[r], dtype=np.int64)
            got = cash.forward(S, D, lk, ln, ln.initial, 0)
            np.testing.assert_array_equal(ln.outputs[0], cum[r], err_msg=f"{node} {branch} draw {r}")
            assert got == (first[r], nonpay[r]), (node, branch, r)
            zero = bool(ch.sens.get("cash_floor"))
            assert cash.tau(ln, got[0], zero, True) == tau[r], (node, r)
            assert cash.cash_out(ln, got[0], zero, True) == out_[r], (node, r)
            for prev in (-1, 0, 30, 90, 150):
                assert cash.fall_after(ln, prev, zero) == fall[prev][r], (node, prev, r)
                counts["triggers"] += 1
            for t in DAYS:
                assert cash.processing_balance(D, ln, t) == bal[t][r], (node, t, r)
                counts["balance"] += 1
            # resumed: the line's state at a day before which the event cash is unchanged since it was saved
            prev = saved.get(key)
            if prev is not None:
                a, state, D0, lk0, S0 = prev
                if (np.array_equal(D0[:, :a], D[:, :a]) and np.array_equal(lk0[:a], lk[:a])
                        and S0[cash.OBL_INC].tolist() == S[cash.OBL_INC].tolist() and S0[f.PETITION] == S[f.PETITION]):
                    got2 = cash.forward(S, D, lk, ln, state, a)
                    np.testing.assert_array_equal(ln.outputs[0, a:], cum[r, a:], err_msg=f"resumed {node} {r}")
                    assert got2 == (first[r], nonpay[r]), (node, r)
                    counts["resumed"] += 1
            # the line's state saved at this step's decision day, on the event cash now
            a = int(np.clip(np.asarray(out)[r], 0, ch.N)) if np.asarray(out).ndim else 0
            st = ln.initial.copy()
            cash.event_rows(S, D, lk, ln, 0)
            c = ls.Cash(ln.post, ln.levy, ln.out, ln.obl, ln.inc, ln.lock, ln.capacity, int(S[f.PETITION]))
            ls.advance(st, ln.tables, c, 0, a, ln.outputs, ln.draws, ln.scratch)
            saved[key] = (a, st, D.copy(), lk.copy(), S.copy())
            counts["runs"] += 1
        counts["steps"] += 1
        return out

    monkeypatch.setattr(events.Chain, "step", step)
    for h in oracle.histories(6, DRAWS, seed=8, style=style):
        lines.clear()
        saved.clear()
        fc, d = oracle.context(h.draws)
        events.event_trace(d, h.path, fc.setup, fc.m, fc.draws, fc.sens)
    assert counts["runs"] > 0 and counts["resumed"] > 0
    print(style, counts)
