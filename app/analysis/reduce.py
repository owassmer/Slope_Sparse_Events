"""The analysis of a tree too large to hold (Option 1): the paths reduced on runners, block by block, into tables the
size of the questions, never the paths (app/disputes/pool.py writes the blocks; `core.Reduction` is the in-memory
analysis these tables reproduce).

Every figure `core.Reduction` computes is a sum over (path, draw) of the path's probability times a per-draw value,
or a quantile of such weighted values. A path's draws fall in the question classes its draws read
(`forecast.expand_classes`); each class combination is a group with one probability. Per path the event cash is traced
once and the loan engine run once; each group adds its draws' values times its probability:

- FULL scenarios (Jev's answers; the residual judgments neutral, attribution step 2): every per-day sum, the
  histograms of `core.Reduction`, and fine histograms (FINE bins) for the quantiles it takes exactly per draw (the
  lowest cash, the collected total, the lowest headroom): within half a fine bin of the exact quantile;
- SCALAR scenarios (each question type held to each answer): the expected scalars;
- per question and answer, the derivative of every expected scalar and of the SERIES per-day sums in that answer's
  probability (each path reads a question once, so its probability is linear in it): the change under any single
  override is exact (`Tables.override`);
- per path the stress row under adverse placement (`core.stress_rows`), the worst kept; the probability mass by the
  path's first steps (the resolution view) and by its verdict outcome.
"""
from __future__ import annotations

import heapq
import json
import os
import pickle
import sys
import time
from collections import defaultdict

import numpy as np

from app.analysis.core import (
    ARREARS_KEYS,
    DAILY,
    NO_DUE,
    PREFERENCE_DAYS,
    PROCEEDS,
    _scalars,
)

FINE = 1 << 20
SERIES = ("collected", "due_cum", "past_due", "outstanding")
TRIE_DEPTH = 8
STRESS_KEEP = 500


def _fine(lo: float, hi: float) -> tuple[float, float]:
    lo = float(np.floor(lo))
    return lo, max((float(np.ceil(hi)) + 1 - lo) / FINE, 1.0)


class Tables:
    """The sums `core.Reduction` keeps per path, under several probability settings at once (the module docstring).
    `full`, `scalar`: the settings' names; `bins`: `core.Analysis._bins`'s; `ranges`: the fine histograms' spans."""

    def __init__(self, full: list[str], scalar: list[str], draws: int, days: int, bins: dict, month_of_day,
                 need: np.ndarray | None, ranges: dict, facility_cents: int) -> None:
        self.full, self.scalar = list(full), list(scalar)
        F, L = len(full), len(scalar)
        self.draws, self.days, self.bins, self.month_of_day, self.need = draws, days, bins, month_of_day, need
        self.facility_cents = facility_cents
        self.means: dict = defaultdict(lambda: np.zeros(F))
        self.lo_means: dict = defaultdict(lambda: np.zeros(L))
        self.per_day = {k: np.zeros((F, days)) for k in (*DAILY, *ARREARS_KEYS)}
        self.counts = {k: np.zeros((F, len(bins[k].lo) * bins[k].n)) for k in ("cash", "collected", "headroom")}
        self.hr_count, self.hr_negative = np.zeros(F), np.zeros(F)
        self.floor = np.zeros((F, days + 1))  # the first day cash is below the need (days: never), weight p/n per draw
        self.ranges = ranges
        self.fine = {k: np.zeros((F, FINE)) for k in ranges}
        self.dscal: dict = {}  # (question, answer) -> [len(skeys)] derivative of the expected scalars (central)
        self.dser: dict = {}  # (question, answer) -> [len(SERIES), days]
        self.skeys: list[str] | None = None
        self.trie: dict = defaultdict(lambda: np.zeros(3))  # step prefix -> [mass, petition, collected] (central)
        self.outcomes: dict = defaultdict(lambda: np.zeros(3))  # (verdict, ruling, outcome) -> the same
        self.stress: list = []  # heap of (-min_cash_p5, n, row)
        self.paths = self.groups = 0
        self.clipped = 0  # values outside their bins' span (the spans are built so that none are)

    # --- one path ---------------------------------------------------------------------------------------------------
    def add(self, p, t, ev, groups: list, stress_row: dict | None = None) -> None:
        """Path p's trajectories `t` (run on its event cash `ev`) under its groups [(draws mask or None, full probs
        [F], scalar probs [L], derivative atoms [(question, answer, dP)])]."""
        n = self.draws
        sc = _scalars(t)
        if ev is not None and ev.proceeds is not None:
            sc.update({k: ev.proceeds[k] for k in PROCEEDS})
        if self.skeys is None:
            self.skeys = sorted(sc)
        below = (t.cash < self.need) if self.need is not None else None
        first = (np.where(below.any(axis=1), below.argmax(axis=1), -1) if below is not None else None)
        verdict = next((s[2] for s in p.steps if s[0] == "verdict"), "")
        ruling = next((s[2] for s in p.steps if s[0] == "post_trial_ruling"), "")
        self.paths += 1
        all_due, all_coll = np.cumsum(t.due, axis=1), np.cumsum(t.collections, axis=1)  # per row: once per path
        for mask, P, PL, atoms in groups:
            self.groups += 1
            on = (lambda a: a) if mask is None else (lambda a, m=mask: a[m])  # noqa: E731
            cnt = n if mask is None else int(mask.sum())
            s_vals = np.array([float(on(sc[k]).sum()) / n for k in self.skeys])
            for i, k in enumerate(self.skeys):
                self.means[k] += P * s_vals[i]
                self.lo_means[k] += PL * s_vals[i]
            cash, coll, fund, outs = on(t.cash), on(t.collections), on(t.fundings), on(t.outstanding)
            idx, pet = np.arange(self.days), on(t.petition)[:, None]
            by_day = (pet >= 0) & (pet <= idx)
            window = (pet >= 0) & (idx >= pet - PREFERENCE_DAYS) & (idx < pet)
            cum_due, cum_coll = on(all_due), on(all_coll)
            fs = fund.sum(axis=0)
            d = {"cash": cash.sum(axis=0),
                 "backup": (cash + np.maximum(self.facility_cents - on(t.capacity), 0)).sum(axis=0),
                 "collected": cum_coll.sum(axis=0), "due_cum": cum_due.sum(axis=0), "fundings": fs,
                 "drawn": np.cumsum(fs), "collections": coll.sum(axis=0), "outstanding": outs.sum(axis=0),
                 "locked": on(t.locked).sum(axis=0), "capacity": on(t.capacity).sum(axis=0),
                 "petitioned": by_day.sum(axis=0), "frozen": (by_day * on(t.stayed)[:, None]).sum(axis=0),
                 "past_due": ((cum_due - cum_coll) * ~by_day).sum(axis=0),
                 "frozen_due": ((cum_due - cum_coll) * by_day).sum(axis=0), "clawback": (coll * window).sum(axis=0)}
            if t.processed is not None:
                by_class = on(t.processed.arrears).sum(axis=0)
                for j, k in enumerate(ARREARS_KEYS):
                    d[k] = by_class[:, j]
            for k, v in d.items():
                self.per_day[k] += P[:, None] * v[None, :]
            for name, x in (("cash", cash), ("collected", cum_coll)):
                b = self.bins[name]
                self.clipped += int(((x < b.lo) | (x >= b.lo + b.n * b.width)).sum())
            for name, flat in (("cash", self.bins["cash"].flat(cash)), ("collected", self.bins["collected"].flat(
                    cum_coll))):
                c = np.bincount(flat, minlength=self.counts[name].shape[1])
                nz = np.flatnonzero(c)
                self.counts[name][:, nz] += P[:, None] * c[nz][None, :]
            hr = slice(None) if mask is None else mask[t.headroom_rows]
            hv = t.headroom[hr]
            if hv.size:
                c = np.bincount(self.bins["headroom"].flat(hv, self.month_of_day[t.headroom_days[hr]]),
                                minlength=self.counts["headroom"].shape[1])
                nz = np.flatnonzero(c)
                self.counts["headroom"][:, nz] += P[:, None] * c[nz][None, :]
            self.hr_count += P * hv.size
            self.hr_negative += P * float((hv < 0).sum())
            if first is not None:
                fd = on(first)
                self.floor += P[:, None] * (np.bincount(np.where(fd >= 0, fd, self.days), minlength=self.days + 1)
                                            / n)[None, :]
            for name, vals in (("min_cash", on(t.min_cash)), ("collected", on(t.collected)),
                               ("min_headroom", on(t.min_headroom))):
                if name == "min_headroom":
                    vals = vals[vals != NO_DUE]
                lo, width = self.ranges[name]
                j = ((vals - lo) / width).astype(np.int64)
                self.clipped += int(((j < 0) | (j >= FINE)).sum())
                nz, c = np.unique(np.clip(j, 0, FINE - 1), return_counts=True)
                self.fine[name][:, nz] += P[:, None] * (c / n)[None, :]
            ser = np.stack([d[k] for k in SERIES]) / n
            for key, ans, dp in atoms:
                a = self.dscal.get((key, ans))
                if a is None:
                    a = self.dscal[(key, ans)] = np.zeros(len(self.skeys))
                    self.dser[(key, ans)] = np.zeros((len(SERIES), self.days))
                a += dp * s_vals
                self.dser[(key, ans)] += dp * ser
            share = cnt / n
            c0 = P[0] * share
            pp, cc = float(on(sc["petition_p"]).sum()) / n, float(on(sc["collected"]).sum()) / n
            for dep in range(1, min(TRIE_DEPTH, len(p.steps)) + 1):
                self.trie[p.steps[:dep]] += (c0, P[0] * pp, P[0] * cc)
            self.outcomes[(verdict, ruling, p.outcome)] += (c0, P[0] * pp, P[0] * cc)
        if stress_row is not None:
            key = -stress_row["min_cash_p5_cents"]
            item = (key, self.paths, stress_row)
            if len(self.stress) < STRESS_KEEP:
                heapq.heappush(self.stress, item)
            elif item > self.stress[0]:
                heapq.heapreplace(self.stress, item)

    # --- merge ------------------------------------------------------------------------------------------------------
    def merge(self, other: Tables) -> Tables:
        for k, v in other.means.items():
            self.means[k] += v
        for k, v in other.lo_means.items():
            self.lo_means[k] += v
        for k in self.per_day:
            self.per_day[k] += other.per_day[k]
        for k in self.counts:
            self.counts[k] += other.counts[k]
        for k in self.fine:
            self.fine[k] += other.fine[k]
        self.clipped += other.clipped
        self.hr_count += other.hr_count
        self.hr_negative += other.hr_negative
        self.floor += other.floor
        for k, v in other.dscal.items():
            if k in self.dscal:
                self.dscal[k] += v
                self.dser[k] += other.dser[k]
            else:
                self.dscal[k], self.dser[k] = v.copy(), other.dser[k].copy()
        self.skeys = self.skeys or other.skeys
        for k, v in other.trie.items():
            self.trie[k] += v
        for k, v in other.outcomes.items():
            self.outcomes[k] += v
        for item in other.stress:
            item = (item[0], item[1] + 10**12 * (len(self.stress) + 1), item[2])
            if len(self.stress) < STRESS_KEEP:
                heapq.heappush(self.stress, item)
            elif item > self.stress[0]:
                heapq.heapreplace(self.stress, item)
        self.paths += other.paths
        self.groups += other.groups
        return self

    def __getstate__(self) -> dict:  # defaultdicts with lambdas do not pickle; the fine histograms go sparse
        s = dict(self.__dict__)
        for k in ("means", "lo_means", "trie", "outcomes"):
            s[k] = dict(s[k])
        s["fine"] = {k: (nz := np.flatnonzero(v.any(axis=0)), v[:, nz]) for k, v in self.fine.items()}
        return s

    def __setstate__(self, s: dict) -> None:
        F, L = len(s["full"]), len(s["scalar"])
        for k, size in (("means", F), ("lo_means", L), ("trie", 3), ("outcomes", 3)):
            s[k] = defaultdict(lambda size=size: np.zeros(size), s[k])
        fine = {}
        for k, (nz, v) in s["fine"].items():
            fine[k] = np.zeros((F, FINE))
            fine[k][:, nz] = v
        s["fine"] = fine
        self.__dict__.update(s)

    # --- the figures ------------------------------------------------------------------------------------------------
    def _fine_q(self, name: str, i: int, qs) -> np.ndarray | None:
        h = self.fine[name][i]
        tot = h.sum()
        if tot <= 0:
            return None
        lo, width = self.ranges[name]
        cum = np.cumsum(h) / tot
        return np.array([lo + (np.argmax(cum >= q - 1e-12) + 0.5) * width for q in qs])

    def expected(self, i: int = 0) -> dict[str, float]:
        return {k: float(v[i]) for k, v in self.means.items()}

    def metrics(self, i: int = 0) -> dict:
        """`core.Reduction.metrics` under full setting i."""
        from app.analysis.core import QS

        e = self.expected(i)
        mq = self._fine_q("min_cash", i, QS)
        headroom, hw = None, self.hr_count[i]
        if hw > 0:
            hq = self.bins["headroom"].pooled_quantiles(self.counts["headroom"][i].reshape(
                len(self.bins["headroom"].lo), self.bins["headroom"].n), QS)
            headroom = {"p5_cents": float(hq[0]), "p50_cents": float(hq[1]), "p95_cents": float(hq[2]),
                        "negative_p": float(self.hr_negative[i] / hw)}
        low = self._fine_q("min_headroom", i, QS)
        kq = self._fine_q("collected", i, QS)
        assert mq is not None and kq is not None, "a setting with no probability mass"
        due = float(self.per_day["due_cum"][i, -1] / self.draws)
        past = float(self.per_day["past_due"][i, -1] / self.draws)
        return {
            "due_horizon_cents": due, "past_due_horizon_cents": past,
            "frozen_due_cents": due - e["collected"] - past,
            "collection_rate": e["collected"] / due if due else None,
            "drawn_cents": e["drawn"], "fees_cents": e["fees"], "contractual_cents": e["contractual"],
            "collected_cents": e["collected"], "stayed_claim_cents": e["stayed"],
            "collected_p5_cents": float(kq[0]), "collected_p50_cents": float(kq[1]), "collected_p95_cents": float(kq[2]),
            "stayed_claim_recovery": "unknown: stayed from the petition, recovered (if at all) after the horizon",
            "stayed_principal_cents": e["stayed_principal"], "preference_exposed_cents": e["preference"],
            "not_yet_due_cents": e["not_yet_due"], "uncollected_horizon_cents": e["uncollected"],
            "peak_outstanding_cents": e["peak_outstanding"], "time_weighted_outstanding_cents": e["avg_outstanding"],
            "dollar_days": e["dollar_days"], "lender_pv_cents": e["lender_pv"], "pv_fundings_cents": e["pv_fundings"],
            "pv_collections_cents": e["pv_collections"], "petition_p": min(1.0, e["petition_p"]),
            "headroom_at_due": headroom,
            "min_headroom_p5_cents": float(low[0]) if low is not None else None,
            "min_headroom_p50_cents": float(low[1]) if low is not None else None,
            "min_cash_mean_cents": e["min_cash"], "min_cash_p5_cents": float(mq[0]),
            "shortfall_p": min(1.0, e["shortfall_p"]), "shortfall_mean_cents": e["shortfall"],
            "peak_locked_cents": e["peak_locked"], "peak_capacity_cents": e["peak_capacity"],
            "horizon_cash_mean_cents": e["horizon_cash"],
            "full_collection_by_maturity_p": min(1.0, e["recovered_all"]),
            "uncollected_maturity_cents": e["unrecovered"],
        }

    def first_floor(self, i: int = 0) -> dict:
        h = self.floor[i]
        w = h / h.sum()
        share = float(w[:self.days].sum())
        k = int(np.searchsorted(np.cumsum(w), 0.5))
        return {"share": share, "median_day": k if share >= 0.5 else None}

    def daily(self, i: int, limit: np.ndarray) -> dict:
        """`core.Reduction.daily` under full setting i (the collected total's horizon quantiles from the fine
        histogram)."""
        from app.analysis.core import QS

        E = {k: v[i] / self.draws for k, v in self.per_day.items()}

        def q(name):
            b = self.bins[name]
            h = self.counts[name][i].reshape(len(b.lo), b.n)
            return b.quantiles(h / np.maximum(h.sum(axis=1, keepdims=True), 1e-300), QS)
        cq, kq = q("cash"), q("collected")
        last = self._fine_q("collected", i, QS)
        if last is not None:
            kq[:, -1] = last
        cum_p = E["petitioned"]
        exposure = np.divide(E["frozen"], cum_p, out=np.zeros(self.days), where=cum_p > 0)
        ints = lambda x: np.rint(x).astype(np.int64).tolist()  # noqa: E731
        return {
            "cash_mean": ints(E["cash"]), "cash_p5": ints(cq[0]), "cash_p50": ints(cq[1]), "cash_p95": ints(cq[2]),
            "backup_liquidity_mean": ints(E["backup"]),
            "backup_liquidity_p5": ints(cq[0]) if self.facility_cents == 0 else None,
            "collected_mean": ints(E["collected"]), "collected_p5": ints(kq[0]), "collected_p50": ints(kq[1]),
            "collected_p95": ints(kq[2]), "contractual": ints(E["due_cum"]),
            "drawn_mean": ints(E["drawn"]), "fundings_mean": ints(E["fundings"]),
            "collections_mean": ints(E["collections"]), "outstanding_mean": ints(E["outstanding"]),
            "locked_mean": ints(E["locked"]), "capacity_mean": ints(E["capacity"]),
            "limit_mean": ints(limit.mean(axis=0)), "limit_p5": ints(np.quantile(limit, 0.05, axis=0, method="lower")),
            "petition_cum_p": [float(x) for x in cum_p], "petition_exposure_mean": ints(exposure),
            "frozen_mean": ints(E["frozen"]), "frozen_due_mean": ints(E["frozen_due"]),
            "past_due_mean": ints(E["past_due"]), "clawback_mean": ints(E["clawback"]),
        }

    def override(self, question: str, dist: dict, base: dict) -> dict[str, float]:
        """The expected scalars with one question's answers set to `dist` (base: Jev's), all else Jev's: exact, the
        path probabilities being linear in each question's answer."""
        assert self.skeys is not None
        e = np.array([self.means[k][0] for k in self.skeys])
        for ans, p in dist.items():
            d = self.dscal.get((question, ans))
            if d is not None:
                e = e + (p - base[ans]) * d
        return dict(zip(self.skeys, e.tolist(), strict=True))


# --- probabilities of a path's groups -------------------------------------------------------------------------------

def edge_prob(key: str, branch: str, dist) -> float:
    return dist[key][branch]


def group_probs(edges, dists: list) -> np.ndarray:
    """The path probability of `edges` under each setting in `dists` (forecast.Dist objects), as
    `forecast.path_probability` computes it."""
    out = np.ones(len(dists))
    for i, D in enumerate(dists):
        p = 1.0
        for key, b in edges:
            p *= D[key][b]
        out[i] = p
    return out


def atoms_derivative(edges, D) -> list[tuple[str, str, float]]:
    """For each (question, answer) the path's edges read, d P(path) / d p(question, answer) under setting D: the other
    edges' product times the edge's own derivative (a composite: the sum over its conjunctions holding that answer of
    the other atoms' product)."""
    from app.disputes.forecast import COMPOSITE, _conjunctions

    seen: set = set()
    for key, _ in edges:  # the derivative is the exact change only if the path reads each question once
        ks = {k for c in _conjunctions(key) for k, _ in c} if key.startswith(COMPOSITE) else {key}
        if seen & ks:
            raise ValueError(f"a path reads {sorted(seen & ks)[0]} twice: its probability is not linear in it")
        seen |= ks
    vals = [D[k][b] for k, b in edges]
    # the other edges' product as prefix times suffix products (linear in the edges; a zero edge makes every other
    # edge's product zero exactly as the term-by-term product did)
    before, after, acc = [1.0] * (len(vals) + 1), [1.0] * (len(vals) + 1), 1.0
    for i, v in enumerate(vals):
        acc *= v
        before[i + 1] = acc
    acc = 1.0
    for i in range(len(vals) - 1, -1, -1):
        acc *= vals[i]
        after[i] = acc
    out: dict = defaultdict(float)
    for i, (key, b) in enumerate(edges):
        rest = before[i] * after[i + 1]
        if rest == 0.0:
            continue
        if not key.startswith(COMPOSITE):
            out[(key, b)] += rest
            continue
        conj = _conjunctions(key)
        # a composite's 'yes' is the sum over its conjunctions; its 'no' is one minus it
        sign = 1.0 if b == "yes" else -1.0
        for c in conj:
            for m, (k, a) in enumerate(c):
                other = 1.0
                for n_, (k2, a2) in enumerate(c):
                    if n_ != m:
                        other *= D[k2][a2]
                out[(k, a)] += sign * rest * other
    return [(k, a, v) for (k, a), v in out.items() if v != 0.0]


# --- the runner pass ------------------------------------------------------------------------------------------------

def fine_ranges(bins: dict) -> dict:
    """The fine histograms' spans, from the bins (the lowest cash lies in the cash bins' span, the horizon's collected
    total in the collected bins', the lowest headroom in the headroom bins')."""
    def top(b):
        return float((b.lo + b.n * b.width).max())
    return {"min_cash": _fine(float(bins["cash"].lo.min()), top(bins["cash"])),
            "collected": _fine(0.0, top(bins["collected"])),
            "min_headroom": _fine(float(bins["headroom"].lo.min()), top(bins["headroom"]))}


def prepared(feed, setup, d, m: dict, sens: dict, stress: bool = False):
    """A `core.Analysis` holding only what tracing and running one dispute's paths needs (its draws, line, calendar),
    so each path's event cash and trajectories are the analysis's own."""
    from app.analysis.core import Analysis, EventModel

    a = Analysis.__new__(Analysis)
    a._prepare(feed, setup, EventModel(disputes={d.instance_id: d}, judgments={}, per={}, order=[]), stress, sens, m,
               None)
    return a


def make_tables(a, full: list[str], scalar: list[str], ev_range) -> Tables:
    bins = a._bins_from(*ev_range)
    return Tables(full, scalar, a._draws.n, a.days, bins, a.month_of_day,
                  a.line.need[:, :a.days], fine_ranges(bins), a.setup.facility_cents)


def reduce_paths(a, paths: list, full: list, scalar: list, known, dead, tables: Tables, stress_a=None,
                 on_child=None, stress_out: list | None = None) -> Tables:
    """Each walked path traced once and run once (`a`: `prepared`), split by its draws' question classes
    (`forecast.expand_classes`), each class combination a group of `tables.add` with its probability under each
    setting (`full`, `scalar`: forecast.Dist). With `stress_a` (prepared with stress) each path's stress row too,
    every row appended to `stress_out`. `on_child(child, t, ev, mask)`: each group's path, for checks."""
    from app.analysis.core import BATCH, run_many
    from app.analysis.setup import DRAWS
    from app.disputes.forecast import class_firsts, expand_classes, path_mask, path_probability

    first = class_firsts(known, dead)  # once: the same for every path
    for lo in range(0, len(paths), BATCH):
        chunk = paths[lo:lo + BATCH]
        evs = [a.event_cash((p,)) for p in chunk]
        # the chunk's stress rows in one engine run (each row is computed alone in the kernel)
        rows = stress_a._stress_rows([(p,) for p in chunk]) if stress_a is not None else [None] * len(chunk)
        for p, t, ev, row in zip(chunk, run_many(a.line, a.opening, evs), evs, rows, strict=True):
            groups = []
            for c in expand_classes([p], known, DRAWS, dead, first):
                mk = path_mask(c, DRAWS)
                mk = None if mk is not None and mk.all() else mk
                groups.append((mk, np.array([path_probability(c.edges, D) for D in full]),
                               np.array([path_probability(c.edges, D) for D in scalar]),
                               atoms_derivative(c.edges, full[0])))
                if on_child is not None:
                    on_child(c, t, ev, mk)
            if row is not None and stress_out is not None:
                stress_out.append((p.steps, p.outcome, row))
            tables.add(p, t, ev, groups, row)
    return tables


# --- the runner job -------------------------------------------------------------------------------------------------

def settings_for(answers: dict[str, dict[str, float]], nodes: dict) -> tuple[list, list, list, list]:
    """The settings the tables carry, from Jev's answer to every question asked (`answers`: key -> distribution):
    full: Jev's, and every residual neutral (attribution step 2, `forecast.neutral_map`'s rule); scalar: each question
    type (a node name) held to each of its answers, every other question at Jev's (`core.judgment_sensitivity`'s
    0% / 100%, per type: every situation of it at once). Composites follow from their parts (forecast.Dist)."""
    from app.disputes.forecast import Dist

    neutral = {k: {b: 1 / len(v) for b in v} for k, v in answers.items()}
    by_type: dict = defaultdict(list)
    for k in answers:
        by_type[nodes[k].node].append(k)
    s_names, s_dists = [], []
    for t in sorted(by_type):
        for a in dict.fromkeys(b for k in by_type[t] for b in answers[k]):
            s_names.append(f"{t}={a}")
            s_dists.append(Dist({**answers, **{k: {b: float(b == a) for b in answers[k]}
                                               for k in by_type[t] if a in answers[k]}}))
    return ["jev", "neutral"], [Dist(answers), Dist(neutral)], s_names, s_dists


def _blocks(cost: dict, n: int) -> list[list[tuple[str, int, int]]]:
    """n contiguous blocks of the paths (the parts in control's order, each path costing its part's walk seconds per
    path), of about equal cost: each block a list of (part, first, end)."""
    items = [(f, c, max(sec, 1e-3) / c) for f, (c, sec) in sorted(cost.items()) if c]
    total = sum(c * per for _, c, per in items)
    out: list = [[] for _ in range(n)]
    acc, b = 0.0, 0
    for f, c, per in items:
        i = 0
        while i < c:
            room = (b + 1) * total / n - acc
            take = c - i if b == n - 1 else min(c - i, max(1, int(round(room / per))))
            out[b].append((f, i, i + take))
            acc += take * per
            i += take
            if acc >= (b + 1) * total / n - 1e-9 and b < n - 1:
                b += 1
    return out


def job(run_id: str, ctl_dir: str, answers_file: str, job_i: int, jobs: int, procs: int, out: str,
        *, root=None, block_ranges=None) -> None:
    """This machine's blocks job*procs .. +procs of jobs*procs: each in a forked process, its paths reduced
    (`reduce_paths`, with stress rows) into out/tab<block>.pkl and out/stress<block>.pkl."""
    from app.analysis.build import run_context
    from app.disputes.forecast import PENDING, Forecaster
    from app.disputes.parallel import _variant

    t0 = time.time()
    with open(os.path.join(ctl_dir, "control.pkl"), "rb") as fh:
        ctl = pickle.load(fh)
    with open(answers_file) as fh:
        ans = json.load(fh)
    from pathlib import Path

    ctx = run_context(run_id, root if root is not None else Path("runs/recorded"))
    setup, sens = _variant(ctx)
    fc = Forecaster(ctx["live"], ctx["findings"], borrower=ctx["borrower"], review=ctx["review"],
                    horizon=setup.horizon, hydrate=ctx["hydrate"], setup=setup, slots=ctx["slots"], model=ctx["m"],
                    sens=sens)
    d = next(x for x, _ in fc.ordered() if x.stage == PENDING and x.borrower_role == "debtor")
    a = prepared(ctx["feed"], setup, d, ctx["m"], sens)
    sa = prepared(ctx["feed"], setup, d, ctx["m"], sens, stress=True)
    f_names, full, s_names, scal = settings_for(ans["answers"], ctl["nodes"])
    known, dead = set(ctl["nodes"]), frozenset(ans.get("dead", ()))
    blocks = _blocks(ctl["part_cost"], jobs * procs) if block_ranges is None else block_ranges
    os.makedirs(out, exist_ok=True)
    print(f"{time.time() - t0:7.0f}s reduce job {job_i}: {len(full)} full and {len(scal)} scalar settings",
          file=sys.stderr, flush=True)
    pids = []
    for k in range(job_i * procs, job_i * procs + procs):
        pid = os.fork()
        if pid == 0:
            code = 1
            try:
                from app.disputes.pool import read_paths

                paths = []
                for f, lo, hi in blocks[k]:  # the block's range alone (pool.write_paths indexes each part)
                    paths += read_paths(os.path.join(ctl_dir, "paths", f), lo, hi)
                tab = make_tables(a, f_names, s_names, ctl["ev_range"])
                rows: list = []
                reduce_paths(a, paths, full, scal, known, dead, tab, stress_a=sa, stress_out=rows)
                with open(os.path.join(out, f"tab{k}.pkl"), "wb") as fh:
                    pickle.dump(tab, fh, protocol=pickle.HIGHEST_PROTOCOL)
                with open(os.path.join(out, f"stress{k}.pkl"), "wb") as fh:
                    pickle.dump(rows, fh, protocol=pickle.HIGHEST_PROTOCOL)
                print(f"{time.time() - t0:7.0f}s block {k}: {len(paths)} paths, {tab.groups} groups, "
                      f"clipped {tab.clipped}", file=sys.stderr, flush=True)
                code = 0
            except BaseException as e:  # noqa: BLE001 (reported through the exit code and the log)
                import traceback
                print(f"block {k} failed: {e!r}\n{traceback.format_exc()}", file=sys.stderr, flush=True)
            finally:
                os._exit(code)
        pids.append(pid)
    bad = [k for k, pid in enumerate(pids) if os.waitpid(pid, 0)[1] != 0]
    if bad:
        raise SystemExit(f"reduce: block(s) {bad} of job {job_i} failed")


def merge(folder: str, out: str) -> None:
    """Every block's tables (folder/**/tab*.pkl) merged into out/tables.pkl; every stress row into out/stress.pkl."""
    from pathlib import Path

    t0 = time.time()
    tab, rows, n = None, [], 0
    for f in sorted(Path(folder).rglob("tab*.pkl")):
        with open(f, "rb") as fh:
            t = pickle.load(fh)
        tab = t if tab is None else tab.merge(t)
        n += 1
    for f in sorted(Path(folder).rglob("stress*.pkl")):
        with open(f, "rb") as fh:
            rows += pickle.load(fh)
    if tab is None:
        raise SystemExit("merge: no tables")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "tables.pkl"), "wb") as fh:
        pickle.dump(tab, fh, protocol=pickle.HIGHEST_PROTOCOL)
    with open(os.path.join(out, "stress.pkl"), "wb") as fh:
        pickle.dump(rows, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"{time.time() - t0:7.0f}s merge: {n} blocks, {tab.paths} paths, {tab.groups} groups, {len(rows)} stress "
          f"rows, clipped {tab.clipped}", file=sys.stderr, flush=True)
    if tab.clipped:
        raise SystemExit(f"merge: {tab.clipped} values fell outside their bins")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "job":
        job(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), int(sys.argv[6]), int(sys.argv[7]), sys.argv[8])
    elif cmd == "merge":
        merge(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit(f"unknown command {cmd}")
