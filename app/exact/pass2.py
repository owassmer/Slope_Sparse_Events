"""Pass 2's Python side (design/002-exact-evaluator.md, Two passes): the bins the percentiles read (from pass 1's
range of final event cash, as `core.Analysis._bins_from` builds them), the analysis's constants on a draw
(`leaf.LK`), a judge's answers as the tables `tree.evaluate` reads, and a draw's evaluation."""
from __future__ import annotations

import numpy as np

from app.analysis.core import CASH_BINS, COLLECTED_BINS, HEADROOM_BINS, Bins
from app.analysis.engine import run
from app.analysis.events import EventCash
from app.exact import fields_walk as w
from app.exact import leaf, replay, tree, walk


def calendar(setup, days: int) -> tuple[list, np.ndarray]:
    from datetime import timedelta

    dates = [setup.review + timedelta(days=t + 1) for t in range(days)]
    months = sorted({(d.year, d.month) for d in dates})
    return months, np.array([months.index((d.year, d.month)) for d in dates], dtype=np.int64)


def bins(ch0, lo_ev: np.ndarray, hi_ev: np.ndarray) -> dict[str, Bins]:
    """`core.Analysis._bins_from` on the chain's line: the per-day ranges that hold every trajectory, from the
    operating cash alone (`engine.run` with no events, every draw) moved by the cumulative event cash's range."""
    line, setup, days = ch0.basis.line, ch0.s, ch0.N
    opening = ch0.basis.opening - setup.exposure.cash_cents
    bank = run(line, opening, EventCash.zeros(line.ops.draws, days))
    months, month_of_day = calendar(setup, days)
    fee = (setup.fee_bps + 1) / 10_000
    lim = np.maximum.accumulate(line.limit.max(axis=0).astype(np.float64)) + 10_000
    first_due = line.due_idx[:, 0]
    coll, fund = np.zeros(days), np.zeros(days)
    for t in range(days):
        repayable = np.flatnonzero(first_due <= t)
        prev = coll[int(repayable.max())] if repayable.size else 0.0
        coll[t] = (1 + fee) * lim[t] + prev if repayable.size else 0.0
        fund[t] = lim[t] + coll[t] / (1 + fee)
    opened = setup.exposure.owed_cents
    coll = np.maximum.accumulate(coll) + opened + 10_000
    margin = lim + fund * fee + 10_000
    lo, hi = bank.cash.min(axis=0) + lo_ev - margin, bank.cash.max(axis=0) + hi_ev + margin
    owed = (1 + fee) * float(lim.max()) + opened + 10_000
    low = lo - line.need[:, :days].max(axis=0) - owed
    h_lo = np.array([low[month_of_day == k].min() for k in range(len(months))])
    h_hi = np.array([hi[month_of_day == k].max() for k in range(len(months))])
    return {"cash": Bins.spanning(lo, hi, CASH_BINS), "collected": Bins.spanning(np.zeros(days), coll, COLLECTED_BINS),
            "headroom": Bins.spanning(h_lo, h_hi, HEADROOM_BINS)}


def constants(ch0, b: dict[str, Bins]) -> leaf.LK:
    line, setup = ch0.basis.line, ch0.s
    _, month_of_day = calendar(setup, ch0.N)
    return leaf.LK(df=np.ascontiguousarray(line.df[:ch0.N], dtype=np.float64), facility=int(setup.facility_cents),
                   p0=int(setup.exposure.principal_cents), month=month_of_day,
                   cash_lo=b["cash"].lo, cash_w=b["cash"].width, coll_lo=b["collected"].lo, coll_w=b["collected"].width,
                   head_lo=b["headroom"].lo, head_w=b["headroom"].width, nbins_cash=b["cash"].n,
                   nbins_coll=b["collected"].n, nbins_head=b["headroom"].n)


def accumulators(ch0, b: dict[str, Bins]) -> dict[str, np.ndarray]:
    months, _ = calendar(ch0.s, ch0.N)
    return {"day": np.zeros((leaf.NDAY, ch0.N)), "scalars": np.zeros(leaf.NSC),
            "cash": np.zeros((ch0.N, b["cash"].n)), "collected": np.zeros((ch0.N, b["collected"].n)),
            "headroom": np.zeros((len(months), b["headroom"].n)),
            "ev_lo": np.zeros(ch0.N, np.int64), "ev_hi": np.zeros(ch0.N, np.int64)}


def evaluate(ch0, r: int, tk: tree.TK, lk: leaf.LK, qhash: np.ndarray, qprob: np.ndarray, vprob: np.ndarray,
             forced=(), acc: dict | None = None) -> tuple[float, int, dict]:
    """Pass 2 on draw row r: (the histories' total weight, the leaves, the accumulators)."""
    k, ln = replay.constants(ch0, r)
    st = replay.state(ch0, r, ln)
    ws = replay.workspace(ch0, r, st)
    sk = tree.stack_for(st)
    acc = acc if acc is not None else accumulators(ch0, {"cash": Bins(lk.cash_lo, lk.cash_w, lk.nbins_cash),
                                                         "collected": Bins(lk.coll_lo, lk.coll_w, lk.nbins_coll),
                                                         "headroom": Bins(lk.head_lo, lk.head_w, lk.nbins_head)})
    order = np.argsort(qhash)
    total, leaves = tree.evaluate(st, k, walk.WK(listing=tk.listing), tk, ws, sk, lk, qhash[order], qprob[order],
                                  vprob, np.array(sorted(forced), dtype=np.int64), acc["day"], acc["scalars"],
                                  acc["cash"], acc["collected"], acc["headroom"], acc["ev_lo"], acc["ev_hi"])
    return total, leaves, acc


def table(questions: np.ndarray, ids: np.ndarray, judge) -> tuple[np.ndarray, np.ndarray]:
    """The distinct questions (`ids`: pass 1's identities) and each one's answer distribution from `judge(row)` (a
    pass 1 row: its node, context, amount class, group and answer count), as (qhash, qprob)."""
    uniq, first = np.unique(ids, return_index=True)
    qprob = np.zeros((len(uniq), tree.MAX_ANS))
    for i, j in enumerate(first):
        p = np.asarray(judge(questions[j]), dtype=np.float64)
        if p.shape != (questions[j, w.QNANS],) or abs(p.sum() - 1) > 1e-12 or (p < 0).any():
            raise ValueError(f"the judge's answer for question {j} is not a distribution over its answers")
        qprob[i, :len(p)] = p
    return uniq, qprob


def uniform(row) -> np.ndarray:
    """Even odds on every answer (the page's 'neutral' judgments, which it labels as such)."""
    return np.full(int(row[w.QNANS]), 1.0 / int(row[w.QNANS]))


def verdict_probs(fc, d, dist) -> np.ndarray:
    """Each verdict class's probability (`Forecaster.verdict_classes`' order): the sum over its disjoint conjunctions
    of the product of the verdict form's answers (`dist(key) -> {answer: probability}`)."""
    out = []
    for conjunctions in fc.verdict_classes(d).values():
        out.append(sum(float(np.prod([dist(key)[ans] for key, ans in conj])) for conj in conjunctions))
    p = np.array(out)
    if abs(p.sum() - 1) > 1e-9:
        raise ValueError(f"the verdict classes' probabilities sum to {p.sum()}, not one")
    return p
