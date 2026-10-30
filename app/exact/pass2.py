"""Pass 2's Python side (design/002-exact-evaluator.md, Two passes): the bins the percentiles read (from pass 1's
range of final event cash, as `core.Analysis._bins_from` builds them), the analysis's constants on a draw
(`leaf.LK`), a judge's answers as the tables `tree.evaluate` reads, a draw's evaluation, a line of draws and the sum
of the lines.

The terms (Changing judgments): each judge question's answers have rows in T (`Answers.base`), each row a figure
vector (`leaf.nf`): d figures / d P[question, answer], summed over the draws. The verdict's classes have rows after
them (`Answers.vbase`); `verdict_terms` carries those to the verdict form's questions."""
from __future__ import annotations

from typing import NamedTuple

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


def months_of(ch0) -> int:
    return len(calendar(ch0.s, ch0.N)[0])


def evaluate(ch0, r: int, tk: tree.TK, lk: leaf.LK, ans: Answers, vprob: np.ndarray, forced=(),
             acc: dict | None = None) -> tuple[float, int, dict]:
    """Pass 2 on draw row r: (the histories' total weight, the leaves, the accumulators: the figures, the terms `T`,
    the expected figure vector `root` and the repeated decisions' weight `repeats`, each added to over draws)."""
    k, ln = replay.constants(ch0, r)
    st = replay.state(ch0, r, ln)
    ws = replay.workspace(ch0, r, st)
    sk = tree.stack_for(st)
    if acc is None:
        acc = accumulators(ch0, {"cash": Bins(lk.cash_lo, lk.cash_w, lk.nbins_cash),
                                 "collected": Bins(lk.coll_lo, lk.coll_w, lk.nbins_coll),
                                 "headroom": Bins(lk.head_lo, lk.head_w, lk.nbins_head)})
        nf = leaf.nf(months_of(ch0))
        acc.update(T=np.zeros((ans.rows, nf)), root=np.zeros(nf), repeats=np.zeros(len(ans.decisions)))
    total, leaves = tree.evaluate(st, k, walk.WK(listing=tk.listing), tk, ws, sk, lk, ans.qhash, ans.qprob, ans.qpair,
                                  ans.qdec, vprob, ans.vbase, np.array(sorted(forced), dtype=np.int64), acc["day"],
                                  acc["scalars"], acc["cash"], acc["collected"], acc["headroom"], acc["ev_lo"],
                                  acc["ev_hi"], acc["T"], acc["root"], acc["repeats"])
    return total, leaves, acc


class Answers(NamedTuple):
    """A judge's answers as `tree.evaluate` reads them: per walk identity (sorted `qhash`) its distribution (`qprob`;
    NaN: declined, unknown), the first row of its judge question's terms (`qpair`) and its decision (`qdec`); each judge
    question's key, first row and answer count; the decisions (a key without its situation class); the verdict form's
    answers by key (`verdict`; a declined one is absent) and its classes' rows (from `vbase`)."""
    qhash: np.ndarray
    qprob: np.ndarray
    qpair: np.ndarray
    qdec: np.ndarray
    keys: list
    base: np.ndarray
    nans: np.ndarray
    decisions: list
    verdict: dict
    vbase: int
    rows: int


def _answers(lines, nclasses: int) -> Answers:
    ids, probs, pair, dec, keys, base, nans, decisions, verdict = [], [], [], [], [], [], [], {}, {}
    row = 0
    for a in lines:
        if a["verdict"]:
            if not a.get("refused"):
                verdict[a["key"]] = dict(zip(a["branches"], a["distribution"], strict=True))
            continue
        n = len(a["branches"])
        p = np.zeros(tree.MAX_ANS)
        p[:n] = np.nan if a.get("refused") else a["distribution"]  # unknown: never a number
        di = decisions.setdefault(a["key"].split("#")[0], len(decisions))
        keys.append(a["key"])
        base.append(row)
        nans.append(n)
        for i in a["ids"]:  # every walk identity the question answers for
            ids.append(i)
            probs.append(p)
            pair.append(row)
            dec.append(di)
        row += n
    qhash = np.array(ids, dtype=np.int64)
    if len(np.unique(qhash)) != len(qhash):
        raise ValueError("a question identity answered twice")
    o = np.argsort(qhash)
    return Answers(qhash[o], np.array(probs).reshape(-1, tree.MAX_ANS)[o], np.array(pair, np.int64)[o],
                   np.array(dec, np.int64)[o], keys, np.array(base, np.int64), np.array(nans, np.int64),
                   list(decisions), verdict, row, row + nclasses)


def answers(path, nclasses: int) -> Answers:
    """The judge's answers (`judge.answer_all`'s file), with rows for the verdict's `nclasses` classes."""
    import json
    from pathlib import Path

    return _answers((json.loads(t) for t in Path(path).open()), nclasses)


def table(questions: np.ndarray, ids: np.ndarray, judge, nclasses: int) -> Answers:
    """The distinct questions (`ids`: pass 1's identities), each its own judge question and decision, and each one's
    answer distribution from `judge(row)` (a pass 1 row: its node, context, amount class, group and answer count)."""
    uniq, first = np.unique(ids, return_index=True)
    lines = []
    for i, j in zip(uniq, first, strict=True):
        p = np.asarray(judge(questions[j]), dtype=np.float64)
        if p.shape != (questions[j, w.QNANS],) or abs(p.sum() - 1) > 1e-12 or (p < 0).any():
            raise ValueError(f"the judge's answer for question {j} is not a distribution over its answers")
        lines.append({"ids": [int(i)], "key": str(int(i)), "verdict": False, "branches": [str(b) for b in range(len(p))],
                      "distribution": p.tolist()})
    return _answers(lines, nclasses)


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


def verdict_dist(ans: Answers):
    """The verdict form's answers by key; a declined (or missing) one stops pass 2: the verdict is on every history."""
    def dist(key):
        if key not in ans.verdict:
            raise LookupError(f"the verdict form's question {key} has no answer (declined or not asked): unknown")
        return ans.verdict[key]
    return dist


def verdict_terms(fc, d, dist, t_class: np.ndarray) -> dict:
    """The verdict form's questions' terms from the classes' (chain rule): d figures / d P[key, answer] = the sum over
    classes of the class's term times d P[class] / d P[key, answer], each class a sum of conjunctions that hold each
    question at most once."""
    out: dict = {}
    for c, conjunctions in enumerate(fc.verdict_classes(d).values()):
        for conj in conjunctions:
            for i, (key, a) in enumerate(conj):
                others = float(np.prod([dist(k)[b] for j, (k, b) in enumerate(conj) if j != i]))
                out.setdefault((key, a), 0.0)
                out[(key, a)] = out[(key, a)] + others * t_class[c]
    return out


# --- a line of draws, and the sum of the lines (GitHub: run.txt; on a laptop, the lines one after another) ------------
HEAVY = "var/pool"  # the run workflow keeps it on GitHub (`heavy-*` artifacts): the terms, read by `combine`


def ranges(files) -> tuple[np.ndarray, np.ndarray]:
    """The final cumulative event cash's range over every draw, from pass 1's lines (`pass1.main`'s range files)."""
    parts = [np.load(f) for f in files]
    if not parts:
        raise FileNotFoundError("no pass 1 range files")
    return np.min([p["lo"] for p in parts], axis=0), np.max([p["hi"] for p in parts], axis=0)


def line(first: int, last: int, answers_path, range_files, forced, out_dir, heavy_dir=HEAVY, log=print) -> dict:
    """Pass 2 on draws first..last: the figures summed over them to <out_dir>/pass2-<first>.npz, the terms' nonzero
    rows to <heavy_dir>/terms-<first>.npz. The bins are the case's (every draw's operating cash, moved by every draw's
    event cash range), so the lines' histograms add."""
    import json
    import time
    from pathlib import Path

    from app.analysis.events import Chain
    from app.exact import oracle, pass1

    fc_all, d = oracle._forecaster()
    lo, hi = ranges(range_files)
    b = bins(Chain(d, fc_all.setup, fc_all.m, fc_all.draws, fc_all.sens), lo, hi)
    draws = list(range(first, last + 1))
    fc, _ = oracle.context(tuple(draws))
    ch0 = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
    ch0.instrument_cash()
    tk = pass1.case_tables(fc_all, d, ch0)
    lk = constants(ch0, b)
    ans = answers(answers_path, len(fc_all.verdict_classes(d)))
    vprob = verdict_probs(fc_all, d, verdict_dist(ans))
    acc, per = None, {}
    for r, draw in enumerate(draws):
        t0 = time.perf_counter()
        total, leaves, acc = evaluate(ch0, r, tk, lk, ans, vprob, forced, acc)
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"draw {draw}: the histories' weights sum to {total}")
        per[draw] = {"leaves": leaves, "seconds": round(time.perf_counter() - t0, 1)}
        log(f"draw {draw}: {per[draw]}")
    if (acc["ev_lo"] < lo).any() or (acc["ev_hi"] > hi).any():
        raise ValueError("a leaf's event cash outside pass 1's range: the bins would clip it")
    rows = np.flatnonzero(np.abs(acc["T"]).sum(axis=1))
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    Path(heavy_dir).mkdir(parents=True, exist_ok=True)
    np.savez_compressed(Path(heavy_dir) / f"terms-{first:03d}.npz", rows=rows, T=acc["T"][rows])
    np.savez_compressed(Path(out_dir) / f"pass2-{first:03d}.npz", draws=np.array(draws), **{
        k: acc[k] for k in ("day", "scalars", "cash", "collected", "headroom", "ev_lo", "ev_hi", "root", "repeats")},
        cash_lo=b["cash"].lo, cash_w=b["cash"].width, coll_lo=b["collected"].lo, coll_w=b["collected"].width,
        head_lo=b["headroom"].lo, head_w=b["headroom"].width)
    (Path(out_dir) / f"pass2-{first:03d}.json").write_text(json.dumps({"draws": per, "rows": int(ans.rows)}))
    return acc


def combine(out_files, term_files, out_path) -> dict:
    """The lines summed: the figures (each a sum over its draws), the range (min, max), the terms; the bins must be one
    case's."""
    from pathlib import Path

    parts = [np.load(f) for f in sorted(out_files)]
    if not parts:
        raise FileNotFoundError("no pass 2 lines")
    got = {"draws": np.concatenate([p["draws"] for p in parts])}
    if len(np.unique(got["draws"])) != len(got["draws"]):
        raise ValueError("a draw in two lines")
    for k in ("cash_lo", "cash_w", "coll_lo", "coll_w", "head_lo", "head_w"):
        if any(not np.array_equal(p[k], parts[0][k]) for p in parts):
            raise ValueError(f"the lines' bins differ ({k})")
        got[k] = parts[0][k]
    for k in ("day", "scalars", "cash", "collected", "headroom", "root", "repeats"):
        got[k] = np.sum([p[k] for p in parts], axis=0)
    got["ev_lo"] = np.min([p["ev_lo"] for p in parts], axis=0)
    got["ev_hi"] = np.max([p["ev_hi"] for p in parts], axis=0)
    T = None
    for f in term_files:
        t = np.load(f)
        if T is None:
            T = np.zeros((0, t["T"].shape[1]))
        need = int(t["rows"].max()) + 1 if t["rows"].size else 0
        if need > T.shape[0]:
            T = np.vstack([T, np.zeros((need - T.shape[0], T.shape[1]))])
        T[t["rows"]] += t["T"]
    got["T"] = T if T is not None else np.zeros((0, 0))
    np.savez_compressed(Path(out_path), **got)
    return got


def standin(states_dir, out_path) -> int:
    """Even odds on every answer of every question the reduce wrote, as a judge's answers file: the stand-in that runs
    pass 2's machinery where the judge's answers are not in yet. Every line says so (`standin`); nothing it gives is a
    judgment."""
    import json
    from pathlib import Path

    from app.exact import judge

    n = 0
    with Path(out_path).open("w") as fh:
        for line, _ in judge.questions(Path(states_dir)):
            k = len(line["branches"])
            fh.write(json.dumps({"ids": line["ids"], "key": line["key"], "verdict": line["verdict"],
                                 "branches": line["branches"], "distribution": [1.0 / k] * k, "standin": True}) + "\n")
            n += 1
    return n


def main(argv=None) -> None:
    """`python -m app.exact.pass2 line <first> <last> <answers.jsonl> <forced.json> <out dir> <range files...>`;
    `... combine <out.npz> <pass 2 out dir> <terms dir>`; `... standin <states dir> <answers.jsonl>`."""
    import json
    import sys
    from pathlib import Path

    args = argv or sys.argv[1:]
    if args[0] == "line":
        first, last = int(args[1]), int(args[2])
        forced = set(json.loads(Path(args[4]).read_text()))
        line(first, last, args[3], args[6:], forced, args[5], log=lambda s: print(s, flush=True))
    elif args[0] == "combine":
        got = combine(sorted(Path(args[2]).rglob("pass2-*.npz")), sorted(Path(args[3]).rglob("terms-*.npz")), args[1])
        print(f"{len(got['draws'])} draws; terms {got['T'].shape}")
    elif args[0] == "standin":
        print(standin(args[1], args[2]), "questions")
    else:
        raise SystemExit(main.__doc__)


if __name__ == "__main__":
    main()
