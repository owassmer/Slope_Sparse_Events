"""The page's figures from the exact evaluator (design/002-exact-evaluator.md): pass 2's sums over every history of
every operating draw (`pass2.combine`), read as the page reads them, and the decisions Russell can move.

Every expectation is exact. Percentiles are read from histograms, within half a bin (`bin_width`). What Russell
changes is a decision, as his odds against the judge's on one of its answers (Changing judgments): every question of
the decision keeps the judge's answer in its own situation, that answer's odds multiplied (`scaled`). The page carries,
for each decision and answer, the figures at each step of a ladder of odds (`ODDS`), each computed here exactly from
pass 2's terms: a figure is affine in the probabilities of a decision that no history meets twice (pass 2's
`repeats`), so moving all its questions at once moves it by the sum of each one's change times its terms.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.analysis.core import QS, Bins
from app.exact import leaf

ODDS = (0.0, 0.125, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, math.inf)  # Russell's odds against the judge's (0, inf: never, always)
TILES = ("funded", "due", "collected", "unpaid", "past_due", "frozen_due", "not_yet_due", "petition_p", "clawback",
         "stayed", "peak_outstanding", "avg_outstanding", "atm_proceeds", "offering_proceeds")
OUTCOMES = ("o_filed_enforcement", "o_filed_notes", "o_filed_cash", "o_settled", "o_paid", "o_stayed", "o_vacated",
            "o_unresolved")
MONTHLY = ("drawn", "due", "collected", "past_due", "frozen_due", "frozen_not_due", "clawback", "collected_cum",
           "contractual", "outstanding", "petitioned", "frozen")


def scaled(p: np.ndarray, a: int, r: float) -> np.ndarray | None:
    """The distribution p with answer a's odds multiplied by r (r = 0: never; inf: always), the other answers keeping
    their proportions; None where that cannot be (the others have no probability to give or take)."""
    p = np.asarray(p, dtype=np.float64)
    rest = 1.0 - p[a]
    if r == 1.0:
        return p.copy()
    if r == math.inf:
        q = np.zeros_like(p)
        q[a] = 1.0
        return q
    if rest <= 0.0:
        return None  # the judge gives the answer certainty: its odds have nothing to move against
    pa = 0.0 if r == 0.0 else r * p[a] / (r * p[a] + rest)
    q = p * ((1.0 - pa) / rest)
    q[a] = pa
    return q


def rows(v: np.ndarray, months: int) -> np.ndarray:
    """A figure vector's per-day rows by month [NDAY, months] (`leaf.nf`)."""
    return v[leaf.NSC:].reshape(leaf.NDAY, months)


def view(v: np.ndarray, months: int) -> dict:
    """An expected figure vector (per draw) as the page reads it: the tiles, the outcome shares, the monthly table's
    expected columns and the month-end series."""
    sc = dict(zip(leaf.SCALAR_NAMES, v[:leaf.NSC].tolist(), strict=True))
    r = rows(v, months)
    due, past = float(r[leaf.L_DUE_CUM, -1]), float(r[leaf.L_PAST_DUE, -1])
    tiles = {"funded": sc["drawn"], "due": due, "collected": sc["collected"], "unpaid": due - sc["collected"],
             "past_due": past, "frozen_due": due - sc["collected"] - past, "not_yet_due": sc["not_yet_due"],
             "petition_p": sc["petition_p"], "clawback": sc["preference"], "stayed": sc["stayed"],
             "peak_outstanding": sc["peak_outstanding"], "avg_outstanding": sc["avg_outstanding"],
             "atm_proceeds": sc["atm_proceeds"], "offering_proceeds": sc["offering_proceeds"]}
    coll, due_cum = r[leaf.L_COLLECTED], r[leaf.L_DUE_CUM]
    monthly = {"drawn": r[leaf.L_FUNDINGS], "due": np.diff(due_cum, prepend=0.0),
               "collected": np.diff(coll, prepend=0.0), "past_due": r[leaf.L_PAST_DUE],
               "frozen_due": r[leaf.L_FROZEN_DUE], "frozen_not_due": r[leaf.L_FROZEN] - r[leaf.L_FROZEN_DUE],
               "clawback": r[leaf.L_CLAWBACK], "collected_cum": coll, "contractual": due_cum,
               "outstanding": r[leaf.L_OUTSTANDING], "petitioned": r[leaf.L_PETITIONED], "frozen": r[leaf.L_FROZEN]}
    return {"tiles": tiles, "outcomes": {k: sc[k] for k in OUTCOMES},
            "monthly": {k: monthly[k].tolist() for k in MONTHLY}, "reach": sc["one"]}


def flat(fig: dict) -> list[float]:
    """A view as one list (`flat_keys` names its entries), for the ladder."""
    return ([fig["tiles"][k] for k in TILES] + [fig["outcomes"][k] for k in OUTCOMES]
            + [x for k in MONTHLY for x in fig["monthly"][k]])


def flat_keys(months: int) -> list[str]:
    return list(TILES) + list(OUTCOMES) + [f"{k}:{m}" for k in MONTHLY for m in range(months)]


@dataclass
class Combined:
    """pass2.combine's output, read."""
    draws: int
    months: int
    day: np.ndarray
    root: np.ndarray
    T: np.ndarray
    repeats: np.ndarray
    vmass: float
    hist: dict
    bins: dict
    floor: np.ndarray
    raised: np.ndarray
    worst: dict

    @classmethod
    def load(cls, path) -> Combined:
        z = np.load(path)
        months = (z["root"].size - leaf.NSC) // leaf.NDAY
        bins = {"cash": Bins(z["cash_lo"], z["cash_w"], z["cash"].shape[1]),
                "collected": Bins(z["coll_lo"], z["coll_w"], z["collected"].shape[1]),
                "headroom": Bins(z["head_lo"], z["head_w"], z["headroom"].shape[1]),
                "low": Bins(z["low_lo"], z["low_w"], z["low"].shape[1])}
        return cls(draws=int(z["draws"].size), months=int(months), day=z["day"], root=z["root"], T=z["T"],
                   repeats=z["repeats"], vmass=float(z["vmass"][0]),
                   hist={k: z[k] for k in ("cash", "collected", "headroom", "low")}, bins=bins, floor=z["floor"],
                   raised=z["raised"],
                   worst={"draw": int(z["worst_draw"]), "unrecovered": float(z["worst_unrecovered"]),
                          "stayed": float(z["worst_stayed"]), "history": z["worst_history"].tolist()})

    def base(self) -> np.ndarray:
        """The expected figure vector under the judge's answers (each draw weighs one)."""
        return self.root / self.draws

    def bin_width(self) -> float:
        """The widest bin a shown percentile is read from (its error is at most half of it)."""
        return float(max(self.bins["cash"].width.max(), self.bins["collected"].width.max(),
                         self.bins["headroom"].width.max(), self.bins["low"].width.max()))

    def quantiles(self, kind: str, qs=QS) -> np.ndarray:
        h = self.hist[kind]
        return self.bins[kind].quantiles(h / np.maximum(h.sum(axis=1, keepdims=True), 1e-300), qs)


@dataclass
class Question:
    key: str
    branches: list
    p: np.ndarray
    terms: np.ndarray  # [answers, nf]: d figures / d P[answer], summed over the draws


def questions(answers_path, cb: Combined, fc=None, d=None) -> dict[str, list[Question]]:
    """Every judge question with its terms, by decision: a question's key without its situation class (the verdict
    form's: its item, over the answers before it). The verdict form's terms are its classes' carried by the chain
    rule (`pass2.verdict_terms`), so they need the forecaster (fc, d)."""
    from app.exact import pass2

    lines = [json.loads(t) for t in Path(answers_path).open()]
    ans = pass2._answers(lines, len(fc.verdict_classes(d)) if fc is not None else 0)
    if cb.T.shape[0] > ans.rows or len(ans.decisions) != cb.repeats.size:
        raise ValueError("the answers are not the ones pass 2 was run with: their terms' rows differ")
    if cb.T.shape[0] < ans.rows:  # the lines' sum keeps rows up to the last nonzero one
        cb.T = np.vstack([cb.T, np.zeros((ans.rows - cb.T.shape[0], cb.T.shape[1]))])
    bad = [k for k, x in zip(ans.decisions, cb.repeats, strict=True) if x > 1e-12]
    if bad:
        raise ValueError(f"decisions met twice on one history (a figure is not affine in them): {bad[:5]}")
    out: dict[str, list[Question]] = {}
    row = {k: (b, n) for k, b, n in zip(ans.keys, ans.base, ans.nans, strict=True)}
    for a in lines:
        if a["verdict"] or a.get("refused"):
            continue
        b, n = row[a["key"]]
        out.setdefault(a["key"].split("#")[0], []).append(
            Question(a["key"], a["branches"], np.array(a["distribution"]), cb.T[b:b + n]))
    if fc is not None:
        dist = pass2.verdict_dist(ans)
        vt = pass2.verdict_terms(fc, d, dist, cb.T[ans.vbase:ans.vbase + len(fc.verdict_classes(d))])
        for key, p in ans.verdict.items():
            br = list(p)
            terms = np.stack([vt.get((key, a), np.zeros(cb.T.shape[1])) for a in br])
            out.setdefault("|".join(key.split("|")[:2]), []).append(Question(key, br, np.array([p[a] for a in br]),
                                                                              terms))
    return out


def ladder(qs: list[Question], base: np.ndarray, draws: int, months: int) -> dict:
    """A decision's figures at each step of `ODDS` on each of its answers (`flat` lists; None where the odds cannot
    move: the judge gives the answer certainty everywhere it arises), its reach (the probability it arises on a
    history) and the judge's answer, reach-weighted over its situations."""
    answers = list(dict.fromkeys(b for q in qs for b in q.branches))
    one = leaf.SC["one"]
    reach_q = np.array([float(q.terms[0, one]) / draws for q in qs])
    reach = float(reach_q.sum())
    judge = {}
    for a in answers:
        w = sum(r * float(q.p[q.branches.index(a)]) for q, r in zip(qs, reach_q, strict=True) if a in q.branches)
        judge[a] = w / reach if reach > 0 else None
    out = {}
    for a in answers:
        steps = []
        for r in ODDS:
            delta, moved = np.zeros_like(base), False
            for q in qs:
                if a not in q.branches:
                    continue
                p2 = scaled(q.p, q.branches.index(a), r)
                if p2 is None:
                    continue
                delta += (p2 - q.p) @ q.terms
                moved = True
            steps.append(flat(view(base + delta / draws, months)) if moved or r == 1.0 else None)
        out[a] = steps
    return {"answers": answers, "reach": reach, "judge": judge, "ladder": out}
