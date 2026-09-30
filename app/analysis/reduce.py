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
        for mask, P, PL, atoms in groups:
            self.groups += 1
            on = (lambda a: a) if mask is None else (lambda a, m=mask: a[m])  # noqa: E731
            cnt = n if mask is None else int(mask.sum())
            s_vals = np.array([float(on(sc[k]).sum()) / n for k in self.skeys])
            for i, k in enumerate(self.skeys):
                self.means[k] += P * s_vals[i]
                self.lo_means[k] += PL * s_vals[i]
            cash, due, coll, fund, outs = on(t.cash), on(t.due), on(t.collections), on(t.fundings), on(t.outstanding)
            idx, pet = np.arange(self.days), on(t.petition)[:, None]
            by_day = (pet >= 0) & (pet <= idx)
            window = (pet >= 0) & (idx >= pet - PREFERENCE_DAYS) & (idx < pet)
            cum_due, cum_coll = np.cumsum(due, axis=1), np.cumsum(coll, axis=1)
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

    def __getstate__(self) -> dict:  # defaultdicts with lambdas do not pickle
        s = dict(self.__dict__)
        for k in ("means", "lo_means", "trie", "outcomes"):
            s[k] = dict(s[k])
        return s

    def __setstate__(self, s: dict) -> None:
        F, L = len(s["full"]), len(s["scalar"])
        for k, size in (("means", F), ("lo_means", L), ("trie", 3), ("outcomes", 3)):
            s[k] = defaultdict(lambda size=size: np.zeros(size), s[k])
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
    out: dict = defaultdict(float)
    for i, (key, b) in enumerate(edges):
        rest = 1.0
        for j, v in enumerate(vals):
            if j != i:
                rest *= v
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
    from app.disputes.forecast import expand_classes, path_mask, path_probability

    for lo in range(0, len(paths), BATCH):
        chunk = paths[lo:lo + BATCH]
        evs = [a.event_cash((p,)) for p in chunk]
        for p, t, ev in zip(chunk, run_many(a.line, a.opening, evs), evs, strict=True):
            groups = []
            for c in expand_classes([p], known, DRAWS, dead):
                mk = path_mask(c, DRAWS)
                mk = None if mk is not None and mk.all() else mk
                groups.append((mk, np.array([path_probability(c.edges, D) for D in full]),
                               np.array([path_probability(c.edges, D) for D in scalar]),
                               atoms_derivative(c.edges, full[0])))
                if on_child is not None:
                    on_child(c, t, ev, mk)
            row = stress_a._stress_rows([(p,)])[0] if stress_a is not None else None
            if row is not None and stress_out is not None:
                stress_out.append((p.steps, p.outcome, row))
            tables.add(p, t, ev, groups, row)
    return tables
