"""The analysis: Jev's conditional probabilities composed over every joint event path, simulated against the shared
operating draws, carried into the reusable line's dated cash flows; bank-only versus event-adjusted; the three-step
attribution (spec §9 as scoped by §15.1); 0%/Jev/100% sensitivity of each judgment ranked by its effect on the lender;
and a separate stress view.

Memory is bounded by reducing each joint path as soon as it is simulated. Per path the analysis keeps the per-draw
scalars' means, the per-draw lowest cash and lowest headroom, the headroom values at each due date, per-day sums over
draws (cash, collections, outstanding, petitions, frozen claim, ...) and per-day fixed-bin histograms of available cash
and cumulative collections. Every expectation is exact (a probability-weighted sum of per-path means); a quantile of the
daily cash or collections is read from the summed histograms, within one bin width of the exact weighted quantile;
collected at the horizon and the lowest cash and headroom are exact, from per-draw values. The bins are fixed across
paths per day (headroom per month), from a first pass over the event cash and the line's limit (not the invoices the
borrower would route), so no trajectory falls outside them and a bin is small against what the line moves.

Each trajectory (joint path p, operating draw d) has weight P(p) / draws. Every structurally feasible path is simulated,
whatever its probability; a zero-probability path simply carries no weight in the distribution and stays in stress.
Both views run on the same operating draws and the same line (limit, need, routed invoices), so with no events they
are identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np

from app.analysis import operating
from app.analysis.engine import (NEED_DAYS, NO_DUE, PREFERENCE_DAYS, Trajectories, installment_amounts, prepare, run,
                                 run_many)
from app.analysis.events import BANK, Basis, Draws, EventCash, bank_trace, event_trace
from app.analysis.setup import DRAWS, SEED, Setup
from app.analysis.stats import expectation, weighted_quantiles
from app.disputes.forecast import DisputePath, Judgment, combo_probability, distributions, joint_paths
from app.disputes.rules import load_model
from app.domain.investigation import DisputeInstance
from app.finance.bank import BankFeed

QS = (0.05, 0.5, 0.95)
STEP_LABELS = {("settle_before_ruling", "yes"): "settle before the ruling", ("amount_fixed", "yes"): "amount fixed",
               ("amount_fixed", "no"): "no ruling in the period", ("settle_after_judgment", "yes"): "settle",
               ("appeal", "yes"): "appeal", ("secured_stay", "yes"): "secured stay",
               ("secured_stay", "no"): "no secured stay", ("settle_during_appeal", "yes"): "settle during the appeal",
               ("settle_during_appeal", "no"): "appeal still pending", ("voluntary_payment", "yes"): "paid",
               ("enforcement", "yes"): "collected by enforcement", ("enforcement", "no"): "uncollected",
               ("security_form", "cash_deposit"): "cash deposit", ("security_form", "surety_bond"): "surety bond",
               ("security_form", "letter_of_credit"): "letter of credit"}
# Per-trajectory scalars averaged per path (expectations reweight them without re-simulating).
SCALARS = ("lender_pv", "pv_fundings", "pv_collections", "dollar_days", "drawn", "fees", "contractual", "collected",
           "stayed", "stayed_principal", "preference", "not_yet_due", "uncollected", "min_cash")
ATTRIBUTION = (("bank_only", "Bank data: the company's decisions at its cash floor, with Jev's answers"),
               ("record", "Plus the researched record, its residual judgments neutral"),
               ("jev", "Plus Jev's judgments on the researched record"))


@dataclass
class EventModel:
    disputes: dict[str, DisputeInstance]
    judgments: dict[str, Judgment]
    per: dict[str, dict[str, list[DisputePath]]]
    order: list[tuple[DisputeInstance, DisputeInstance | None]]
    combos: list[tuple[DisputePath, ...]] = field(default_factory=list)
    neutral: dict[str, dict[str, float]] | None = None  # the event model's neutral residuals (attribution step 2)
    # The bank view: the company's distress decisions on the bank data and the common borrower inputs alone
    # (Forecaster.bank_paths), with their own judgments. With no dispute the augmented view is the bank view.
    bank_paths: list[DisputePath] = field(default_factory=list)
    bank_judgments: dict[str, Judgment] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.combos:
            self.combos = joint_paths(self.per, self.order) if self.order else self.bank_combos

    @property
    def bank_combos(self) -> list[tuple[DisputePath, ...]]:
        return [(p,) for p in self.bank_paths] or [()]

    def _dist(self, overrides: dict | None):
        return distributions({**self.bank_judgments, **self.judgments}, overrides)

    def probs(self, overrides: dict | None = None) -> np.ndarray:
        return self._weigh("combos", self.combos, overrides)

    def bank_probs(self, overrides: dict | None = None) -> np.ndarray:
        return self._weigh("bank", self.bank_combos, overrides)

    def _weigh(self, name: str, combos: list, overrides: dict | None) -> np.ndarray:
        """`combo_probability` of every combo, vectorised: each combo's edges (in order) index the distinct
        (node, branch) pairs, padded with a factor of exactly 1; the product runs edge by edge, left to right, as
        `path_probability` does, so every probability is the same float."""
        cache = self.__dict__.setdefault("_edge_index", {})
        enc = cache.get(name)
        if enc is None or enc[0] != len(combos) or (combos and enc[1] is not combos[0]):
            pairs: dict[tuple[str, str], int] = {}
            rows = [[pairs.setdefault(e, len(pairs)) for p in c for e in p.edges] for c in combos]
            width = max((len(r) for r in rows), default=0)
            ref = np.full((len(rows), width), len(pairs), dtype=np.int64)  # len(pairs): the factor 1
            for i, r in enumerate(rows):
                ref[i, :len(r)] = r
            enc = cache[name] = (len(combos), combos[0] if combos else None, list(pairs), np.asfortranarray(ref))
        _, _, pairs, ref = enc
        dist = self._dist(overrides)
        vals = np.array([dist[k][b] for k, b in pairs] + [1.0], dtype=np.float64)
        out = np.ones(len(combos))
        for j in range(ref.shape[1]):
            out *= vals[ref[:, j]]
        return out


def neutral_overrides(model: EventModel) -> dict[str, dict[str, float]]:
    """Attribution step 2: what the record fixes, with every residual judgment neutral (a Noul at 0.5, a Choice
    uniform). The event model supplies the map when it has one; otherwise every judgment is taken as a residual."""
    if model.neutral is not None:
        return {k: dict(v) for k, v in model.neutral.items()}
    return {k: {b: 1 / len(j.distribution) for b in j.distribution} for k, j in model.judgments.items()}


def path_label(p: DisputePath) -> str:
    return " → ".join(STEP_LABELS[(n, b)] for n, _, b in p.steps if (n, b) in STEP_LABELS)


def _scalars(t: Trajectories) -> dict[str, np.ndarray]:
    out = {k: getattr(t, k) for k in SCALARS}
    out["fees"] = t.fees
    out["petition_p"] = (t.petition >= 0).astype(np.float64)
    out["shortfall_p"] = (t.min_cash < 0).astype(np.float64)
    out["shortfall"] = np.maximum(-t.min_cash, 0)
    out["peak_outstanding"] = t.outstanding.max(axis=1)
    out["avg_outstanding"] = t.outstanding.mean(axis=1)
    out["peak_locked"] = t.locked.max(axis=1)
    out["peak_capacity"] = t.capacity.max(axis=1)
    out["unrecovered"] = t.stayed + t.uncollected  # owed inside the horizon (or stayed) and not collected
    out["recovered_all"] = (out["unrecovered"] == 0).astype(np.float64)
    out["horizon_cash"] = t.cash[:, -1]
    return out


CASH_BINS, COLLECTED_BINS, HEADROOM_BINS = 128, 128, 1024
# Per-day sums over draws kept for every path (expectations reweight them exactly).
DAILY = ("cash", "backup", "collected", "due_cum", "drawn", "fundings", "collections", "outstanding", "locked",
         "capacity", "petitioned", "frozen", "frozen_due", "past_due", "clawback")


@dataclass
class Bins:
    """Fixed per-day bins: day t spans [lo[t], lo[t] + n * width[t]). Values outside are clipped to the end bins (the
    ranges are built so that none are)."""
    lo: np.ndarray
    width: np.ndarray
    n: int

    @classmethod
    def spanning(cls, lo: np.ndarray, hi: np.ndarray, n: int) -> Bins:
        lo = np.floor(lo).astype(np.float64)
        return cls(lo, np.maximum((np.ceil(hi) + 1 - lo) / n, 1.0), n)

    def flat(self, x: np.ndarray, rows: np.ndarray | None = None) -> np.ndarray:
        """Flat bin index (row x n + bin) of values x: [draws, days] with one row per day, or 1-D with `rows`."""
        if rows is None:
            rows = np.arange(x.shape[1])[None, :]
        j = np.clip(((x - self.lo[rows]) / self.width[rows]).astype(np.int64), 0, self.n - 1)
        return (j + rows * self.n).ravel()

    def quantiles(self, h: np.ndarray, qs: tuple[float, ...]) -> np.ndarray:
        """[len(qs), days] from weighted counts h [days, n] (each day summing to one): the midpoint of the first bin
        whose cumulative weight reaches q, so within half a bin of the exact weighted quantile."""
        cum = np.cumsum(h, axis=1)
        return np.stack([self.lo[:len(h)] + (np.argmax(cum >= q - 1e-12, axis=1) + 0.5) * self.width[:len(h)]
                         for q in qs])


    def pooled_quantiles(self, h: np.ndarray, qs: tuple[float, ...]) -> np.ndarray:
        """Quantiles over all rows together from weighted counts h [rows, n] (rows with their own bins): each bin
        at its midpoint, so within half of its row's bin width of the exact quantile."""
        mids = self.lo[:len(h), None] + (np.arange(self.n) + 0.5) * self.width[:len(h), None]
        keep = h.ravel() > 0
        return weighted_quantiles(mids.ravel()[keep], h.ravel()[keep] / h.sum(), qs)


class Counts:
    """Per-path bin counts, stored sparsely (most of a path's draws share a few bins on any day): the non-zero flat
    bins and their counts, path by path."""

    def __init__(self, rows: int, bins: int) -> None:
        self.rows, self.bins = rows, bins
        self._idx: list[np.ndarray] = []
        self._cnt: list[np.ndarray] = []

    def add(self, flat: np.ndarray) -> None:
        c = np.bincount(flat, minlength=self.rows * self.bins)
        idx = np.flatnonzero(c)
        self._idx.append(idx.astype(np.int32))
        self._cnt.append(c[idx].astype(np.uint32))

    def finish(self) -> Counts:
        self.lens = np.array([len(x) for x in self._idx], dtype=np.int64)
        self.idx, self.cnt = np.concatenate(self._idx), np.concatenate(self._cnt)
        del self._idx, self._cnt
        return self

    def weighted(self, probs: np.ndarray, normalise: bool = True) -> np.ndarray:
        """[rows, bins] probability-weighted counts, each row normalised to one unless asked not to (an empty row
        stays zero)."""
        w = np.repeat(np.asarray(probs, dtype=np.float64), self.lens) * self.cnt
        h = np.bincount(self.idx, weights=w, minlength=self.rows * self.bins).reshape(self.rows, self.bins)
        return h / np.maximum(h.sum(axis=1, keepdims=True), 1e-300) if normalise else h







class Reduction:
    """The compact record of a set of simulated paths (see the module docstring), and the probability-weighted
    metrics and daily series read from it."""

    def __init__(self, n: int, draws: int, days: int, setup: Setup, line_limit: np.ndarray, bins: dict[str, Bins],
                 month_of_day: np.ndarray) -> None:
        self.n, self.draws, self.days, self.setup, self.limit = n, draws, days, setup, line_limit
        self.bins, self.month_of_day = bins, month_of_day
        self.means: dict[str, np.ndarray] = {}
        self.min_cash = np.empty((n, draws), dtype=np.int64)
        self.min_headroom = np.empty((n, draws), dtype=np.int64)
        self.collected = np.empty((n, draws), dtype=np.int64)  # per draw, at the horizon (exact quantiles)
        self.per_day = {k: np.zeros((n, days)) for k in DAILY}
        self.counts = {"cash": Counts(days, bins["cash"].n), "collected": Counts(days, bins["collected"].n),
                       "headroom": Counts(len(bins["headroom"].lo), bins["headroom"].n)}
        self.hr_count = np.zeros(n)  # headroom values (trajectory x due date) per path, and how many are negative
        self.hr_negative = np.zeros(n)
        self.peak_day = np.zeros(n, dtype=np.int64)  # the day of the highest expected outstanding balance

    def add(self, i: int, t: Trajectories) -> None:
        for k, v in _scalars(t).items():
            self.means.setdefault(k, np.zeros(self.n))[i] = float(v.mean())
        self.min_cash[i], self.min_headroom[i], self.collected[i] = t.min_cash, t.min_headroom, t.collected
        idx, pet = np.arange(self.days), t.petition[:, None]
        by_day = (pet >= 0) & (pet <= idx)
        window = (pet >= 0) & (idx >= pet - PREFERENCE_DAYS) & (idx < pet)
        cum_due, cum_coll = np.cumsum(t.due, axis=1), np.cumsum(t.collections, axis=1)
        d = self.per_day
        d["cash"][i] = t.cash.sum(axis=0)
        d["backup"][i] = (t.cash + np.maximum(self.setup.facility_cents - t.capacity, 0)).sum(axis=0)
        d["collected"][i], d["due_cum"][i] = cum_coll.sum(axis=0), cum_due.sum(axis=0)
        d["fundings"][i] = t.fundings.sum(axis=0)
        d["drawn"][i] = np.cumsum(d["fundings"][i])
        d["collections"][i], d["outstanding"][i] = t.collections.sum(axis=0), t.outstanding.sum(axis=0)
        d["locked"][i], d["capacity"][i] = t.locked.sum(axis=0), t.capacity.sum(axis=0)
        d["petitioned"][i] = by_day.sum(axis=0)
        d["frozen"][i] = (by_day * t.stayed[:, None]).sum(axis=0)
        d["past_due"][i] = ((cum_due - cum_coll) * ~by_day).sum(axis=0)
        d["frozen_due"][i] = ((cum_due - cum_coll) * by_day).sum(axis=0)  # frozen installments already due
        d["clawback"][i] = (t.collections * window).sum(axis=0)
        self.counts["cash"].add(self.bins["cash"].flat(t.cash))
        self.counts["collected"].add(self.bins["collected"].flat(cum_coll))
        self.counts["headroom"].add(self.bins["headroom"].flat(t.headroom, self.month_of_day[t.headroom_days]))
        self.hr_count[i], self.hr_negative[i] = len(t.headroom), float((t.headroom < 0).sum())
        self.peak_day[i] = int(np.argmax(d["outstanding"][i]))

    def finish(self) -> Reduction:
        for c in self.counts.values():
            c.finish()
        return self

    # --- reweighting ---------------------------------------------------------------------------------------------

    def expected(self, probs: np.ndarray) -> dict[str, float]:
        return {k: expectation(v, probs) for k, v in self.means.items()}

    def metrics(self, probs: np.ndarray) -> dict:
        probs = np.asarray(probs, dtype=np.float64)
        e = self.expected(probs)
        live = probs > 0  # a zero-weight trajectory never moves a weighted quantile
        w = np.repeat(probs[live] / self.draws, self.draws)
        mq = weighted_quantiles(self.min_cash[live].ravel().astype(np.float64), w, QS)
        # headroom at each due date, pooled over (trajectory, due date), each weighted by its trajectory
        headroom, hw = None, probs @ self.hr_count
        if hw > 0:
            hq = self.bins["headroom"].pooled_quantiles(self.counts["headroom"].weighted(probs, normalise=False), QS)
            headroom = {"p5_cents": float(hq[0]), "p50_cents": float(hq[1]), "p95_cents": float(hq[2]),
                        "negative_p": float(probs @ self.hr_negative / hw)}
        mins = self.min_headroom[live].ravel()
        has = mins != NO_DUE
        low = weighted_quantiles(mins[has].astype(np.float64), w[has] / w[has].sum(), QS) if has.any() else None
        kq = self.collected_quantiles(probs)
        return {
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
            # the current page's names: for the line there is no single maturity, so these read at the horizon
            "full_collection_by_maturity_p": min(1.0, e["recovered_all"]),
            "uncollected_maturity_cents": e["unrecovered"],
        }

    def collected_quantiles(self, probs: np.ndarray) -> np.ndarray:
        """P5 / P50 / P95 of collected at the horizon, exact from each trajectory's total."""
        probs = np.asarray(probs, dtype=np.float64)
        live = probs > 0
        w = np.repeat(probs[live] / probs[live].sum(), self.draws) / self.draws
        return weighted_quantiles(self.collected[live].ravel().astype(np.float64), w, QS)

    def daily(self, probs: np.ndarray, collected_q: bool = True) -> dict:
        """The daily series under `probs`. `collected_q=False` skips the collected quantiles (the page's reweight
        does not show them, and the exact horizon quantile sorts every draw)."""
        probs = np.asarray(probs, dtype=np.float64)
        E = {k: (probs @ v) / self.draws for k, v in self.per_day.items()}
        cq = self.bins["cash"].quantiles(self.counts["cash"].weighted(probs), QS)
        kq = None
        if collected_q:
            kq = self.bins["collected"].quantiles(self.counts["collected"].weighted(probs), QS)
            kq[:, -1] = self.collected_quantiles(probs)  # at the horizon, exact from the per-draw totals
        cum_p = E["petitioned"]
        exposure = np.divide(E["frozen"], cum_p, out=np.zeros(self.days), where=cum_p > 0)
        lim = self.limit  # the reassessed limit is set by operating draws alone, so it is the same on every path
        ints = lambda x: np.rint(x).astype(np.int64).tolist()  # noqa: E731
        return {
            "cash_mean": ints(E["cash"]), "cash_p5": ints(cq[0]), "cash_p50": ints(cq[1]), "cash_p95": ints(cq[2]),
            "backup_liquidity_mean": ints(E["backup"]),
            # with no other facility backup liquidity is available cash, so its P5 is the cash P5
            "backup_liquidity_p5": ints(cq[0]) if self.setup.facility_cents == 0 else None,
            "collected_mean": ints(E["collected"]), **({"collected_p5": ints(kq[0]), "collected_p50": ints(kq[1]),
                                                        "collected_p95": ints(kq[2])} if kq is not None else {}),
            "contractual": ints(E["due_cum"]),  # expected installments due, cumulative
            "drawn_mean": ints(E["drawn"]), "fundings_mean": ints(E["fundings"]),
            "collections_mean": ints(E["collections"]), "outstanding_mean": ints(E["outstanding"]),
            "locked_mean": ints(E["locked"]), "capacity_mean": ints(E["capacity"]),
            "limit_mean": ints(lim.mean(axis=0)), "limit_p5": ints(np.quantile(lim, 0.05, axis=0, method="lower")),
            "petition_cum_p": [float(x) for x in cum_p], "petition_exposure_mean": ints(exposure),
            "frozen_mean": ints(E["frozen"]), "frozen_due_mean": ints(E["frozen_due"]), "past_due_mean": ints(E["past_due"]),
            "clawback_mean": ints(E["clawback"]),
        }


CACHE_PATHS = 64  # a joint model reuses each dispute's paths across combinations; one dispute never does
BATCH = 8  # joint paths simulated together (engine.run_many)


PACKED_BYTES = 256 * 2**20  # at most this much event cash kept (sparse) from the bin pass for the main pass


def _pack(ev: EventCash) -> tuple:
    """A combo's event cash, sparse (most draws and days book nothing): flat index and value of each non-zero."""
    out = []
    for x in (ev.cash, ev.lock, ev.capacity, ev.petition + 1):  # petition: -1 (none) packs as zero
        i = np.flatnonzero(x)
        out += [i.astype(np.int32), x.ravel()[i]]
    return tuple(out)


def _unpack(packed: tuple, draws: int, days: int) -> EventCash:
    ev = EventCash.zeros(draws, days)
    for j, x in enumerate((ev.cash, ev.lock, ev.capacity)):
        np.put(x, packed[2 * j], packed[2 * j + 1])
    np.put(ev.petition, packed[6], packed[7] - 1)
    return ev


def _petition_at(line, t: Trajectories, petition: np.ndarray, peak: int) -> tuple[np.ndarray, np.ndarray]:
    """The stayed claim and preference exposure of `run(line, opening, with_petition(ev, peak))`, read from `t`, the
    run of the same events without it (`petition`: theirs). The engine's petition day becomes the earlier of its own
    and `peak`; before that day the two runs are the same trajectory, and from it on nothing is collected or drawn, so
    the stayed claim is the contract booked less collected before it and the preference window's collections are
    t's (all integer cents)."""
    days = line.days
    pet = np.minimum(np.where((petition >= 0) & (petition < days), petition, days), peak)
    idx = np.arange(days)
    before = idx[None, :] < pet[:, None]
    collected = (t.collections * before).sum(axis=1)
    booked = installment_amounts(t.draw_amounts, line.setup.fee_bps, line.setup.installments).sum(axis=1)
    keep = t.draw_days < pet[t.draw_rows]
    contract = np.zeros(len(pet), dtype=np.int64)
    np.add.at(contract, t.draw_rows[keep], booked[keep])
    window = (idx[None, :] >= pet[:, None] - PREFERENCE_DAYS) & before
    return contract - collected, (t.collections * window).sum(axis=1)


class Analysis:
    """Simulates every joint path on the shared operating draws, keeps each path's reduction (module docstring) and
    recomputes weights without re-simulating. With `stress`, it keeps only the stress rows."""

    def __init__(self, feed: BankFeed, setup: Setup, model: EventModel, stress: bool = False,
                 sens: dict | None = None, dispute_model: dict | None = None, progress=None) -> None:
        """`sens` and `dispute_model` are the chains' sensitivities and model (the tree must be built with the same);
        `progress(phase, done, total)` is told how far the simulation has got."""
        self._prepare(feed, setup, model, stress, sens, dispute_model, progress)
        try:
            if stress:
                self.stress_rows = []
                for lo in range(0, len(model.combos), BATCH):
                    self.stress_rows += self._stress_rows(model.combos[lo:lo + BATCH])
                    for i in range(lo, len(self.stress_rows)):
                        self._tick("stress", i, len(model.combos))
                return
            bins = self._bins()

            def make(n: int) -> Reduction:
                return Reduction(n, DRAWS, self.days, setup, self.line.limit, bins, self.month_of_day)

            self.bank_r = make(len(model.bank_combos))
            for i, c in enumerate(model.bank_combos):
                self.bank_r.add(i, run(self.line, self.opening, self._events("bank", i, c)))
            self.bank_r.finish()
            self.r = make(len(model.combos))
            for lo in range(0, len(model.combos), BATCH):  # a few paths at a time: dense arrays dropped once reduced
                chunk = model.combos[lo:lo + BATCH]
                evs = [self._events("path", i, c) for i, c in enumerate(chunk, lo)]
                for i, t in enumerate(run_many(self.line, self.opening, evs), lo):
                    self.r.add(i, t)
                    self._tick("simulate", i, len(model.combos))
            self.r.finish()
            self.means = self.r.means
        finally:
            self._cache.clear()
            self._draws.prefixes = {}
            self._packed = {}

    def _prepare(self, feed: BankFeed, setup: Setup, model: EventModel, stress: bool, sens: dict | None,
                 dispute_model: dict | None, progress) -> None:
        """The operating draws, the line and the calendar the passes share."""
        self.feed, self.setup, self.model, self.m = feed, setup, model, dispute_model or load_model()
        self.sens, self._progress = sens or {}, progress or (lambda *_: None)
        self.days = (setup.horizon - setup.review).days
        self.ops = operating.simulate(feed, self.days + NEED_DAYS, DRAWS, SEED, setup.variability)
        self.line = prepare(setup, self.ops)
        self.opening = feed.available_cents
        self._draws = Draws(DRAWS, stress=stress, basis=Basis.of(self.ops, self.line.need, self.opening))
        self._draws.prefixes = {}  # the combos run depth-first: each walks only the steps after the shared prefix
        self._cache: dict = {}
        self._packed: dict = {}  # (kind, index) -> the bin pass's event cash, sparse, for the main pass
        self._shared = len(model.order) > 1
        self.instrument = next((f for d in model.disputes.values() for f in d.financing if f.status != "superseded"),
                               None)  # the common borrower input (the notes' coupon) both views carry
        self.bank = run(self.line, self.opening, EventCash.zeros(DRAWS, self.days))  # operating flows alone
        if not stress:
            days = [setup.review + timedelta(days=t + 1) for t in range(self.days)]
            self.months = sorted({(d.year, d.month) for d in days})
            self.month_of_day = np.array([self.months.index((d.year, d.month)) for d in days], dtype=np.int64)

    def _event_range(self, kind: str, combos: list, lo: int = 0) -> tuple[np.ndarray, np.ndarray]:
        """Per day, the lowest and highest cumulative event cash (less encumbrance) over the combos' draws (and 0).
        Each combo's event cash is kept, sparse, for the main pass (within PACKED_BYTES)."""
        lo_ev, hi_ev = np.zeros(self.days), np.zeros(self.days)
        for i, c in enumerate(combos, lo):
            ev = self.event_cash(c)
            if self._packed_bytes < PACKED_BYTES:
                self._packed[(kind, i)] = pk = _pack(ev)
                self._packed_bytes += sum(x.nbytes for x in pk) + 400
            cum = np.cumsum(ev.cash - ev.lock, axis=1)
            np.minimum(lo_ev, cum.min(axis=0), out=lo_ev)
            np.maximum(hi_ev, cum.max(axis=0), out=hi_ev)
        return lo_ev, hi_ev

    def _events(self, kind: str, i: int, combo: tuple[DisputePath, ...]) -> EventCash:
        """The combo's event cash: the bin pass's, unpacked (the same arrays), or computed again."""
        pk = self._packed.pop((kind, i), None)
        return self.event_cash(combo) if pk is None else _unpack(pk, DRAWS, self.days)

    def _bins(self) -> dict[str, Bins]:
        """Per-day ranges that hold every trajectory, bounded by what the line can move (its limit), not by the
        invoices the borrower would route:
        - funded by day t <= the highest limit so far + principal repaid, and only draws whose first installment has
          fallen due can have been repaid, so cumulative collections C(t) <= (1 + fee) x limit + C(first due of a draw
          made after t's last repayable draw); funded F(t) <= limit + C(t) / (1 + fee);
        - available cash: the bank-only cash, moved by the cumulative event cash (a first pass over the event cash
          alone) and by at most the line's own swing (funded less collected lies between -fee x F and the limit);
        - headroom at a due date, per month: that month's cash range, less the need and at most all owed, and owed
          is at most (1 + fee) x principal outstanding <= (1 + fee) x the highest limit."""
        self._packed, self._packed_bytes = {}, 0
        lo_ev, hi_ev = self._event_range("bank", self.model.bank_combos)  # min and max: exact in any order
        for lo in range(0, len(self.model.combos), BATCH):
            a, b = self._event_range("path", self.model.combos[lo:lo + BATCH], lo)
            np.minimum(lo_ev, a, out=lo_ev)
            np.maximum(hi_ev, b, out=hi_ev)
            for i in range(lo, min(lo + BATCH, len(self.model.combos))):
                self._tick("bins", i, len(self.model.combos))
        fee = (self.setup.fee_bps + 1) / 10_000  # + 1 bp covers the half-up cent on each draw
        lim = np.maximum.accumulate(self.line.limit.max(axis=0).astype(np.float64)) + 10_000
        first_due = self.line.due_idx[:, 0]
        coll, fund = np.zeros(self.days), np.zeros(self.days)
        for t in range(self.days):
            repayable = np.flatnonzero(first_due <= t)  # draws made on these days may have been collected by t
            prev = coll[int(repayable.max())] if repayable.size else 0.0
            coll[t] = (1 + fee) * lim[t] + prev if repayable.size else 0.0
            fund[t] = lim[t] + coll[t] / (1 + fee)
        coll = np.maximum.accumulate(coll) + 10_000
        margin = lim + fund * fee + 10_000
        lo, hi = self.bank.cash.min(axis=0) + lo_ev - margin, self.bank.cash.max(axis=0) + hi_ev + margin
        owed = (1 + fee) * float(lim.max()) + 10_000
        low = lo - self.line.need[:, :self.days].max(axis=0) - owed
        h_lo = np.array([low[self.month_of_day == k].min() for k in range(len(self.months))])
        h_hi = np.array([hi[self.month_of_day == k].max() for k in range(len(self.months))])
        return {"cash": Bins.spanning(lo, hi, CASH_BINS), "collected": Bins.spanning(np.zeros(self.days), coll, COLLECTED_BINS),
                "headroom": Bins.spanning(h_lo, h_hi, HEADROOM_BINS)}

    def event_cash(self, combo: tuple[DisputePath, ...]) -> EventCash:
        ev = EventCash.zeros(DRAWS, self.days)
        for p in combo:
            key = (p.instance_id, p.steps)
            e = self._cache.get(key)
            if e is None and p.instance_id == BANK:
                e = bank_trace(self.instrument, p.steps, self.setup, self.m, self._draws, self.sens).events
            elif e is None:
                e = event_trace(self.model.disputes[p.instance_id], p, self.setup, self.m, self._draws, self.sens).events
                if self._shared and len(self._cache) < CACHE_PATHS:
                    self._cache[key] = e
            ev = ev + e
        return ev

    def _tick(self, phase: str, i: int, n: int) -> None:
        if i % 100 == 0 or i == n - 1:
            self._progress(phase, i + 1, n)

    def _stress_rows(self, combos: list[tuple[DisputePath, ...]]) -> list[dict]:
        """Each path under adverse placement, and again with a petition on the day of its highest expected
        outstanding balance."""
        evs = [self.event_cash(c) for c in combos]
        ts = run_many(self.line, self.opening, evs)
        peaks = [int(np.argmax(t.outstanding.mean(axis=0))) for t in ts]
        pts = [_petition_at(self.line, t, ev.petition, peak) for t, ev, peak in zip(ts, evs, peaks, strict=True)]
        return [self._stress_row(t, pt, peak) for t, pt, peak in zip(ts, pts, peaks, strict=True)]

    def _stress_row(self, t: Trajectories, pt: tuple[np.ndarray, np.ndarray], peak: int) -> dict:
        stayed, preference = pt  # with a petition at the peak (_petition_at)
        return {"min_cash_p5_cents": float(np.quantile(t.min_cash, 0.05)),
                "min_cash_p50_cents": float(np.quantile(t.min_cash, 0.5)),
                "uncollected_maturity_cents": float((t.stayed + t.uncollected).mean()),
                "stayed_claim_cents": float(t.stayed.mean()), "lender_pv_cents": float(t.lender_pv.mean()),
                "petition_at_peak": {"day": (self.setup.review + timedelta(days=peak + 1)).isoformat(),
                                     "stayed_claim_mean_cents": float(stayed.mean()),
                                     "stayed_claim_p95_cents": float(np.quantile(stayed, 0.95)),
                                     "preference_exposed_mean_cents": float(preference.mean())}}

    # --- summaries ---------------------------------------------------------------------------------

    def expected(self, probs: np.ndarray) -> dict[str, float]:
        return self.r.expected(probs)

    @staticmethod
    def _view(r: Reduction, probs: np.ndarray) -> dict:
        return {"metrics": r.metrics(probs), "daily": r.daily(probs)}

    def min_cash_quantile(self, probs: np.ndarray, q: float) -> float:
        w = np.repeat(np.asarray(probs, dtype=np.float64) / DRAWS, DRAWS)
        return float(weighted_quantiles(self.r.min_cash.ravel().astype(np.float64), w, (q,))[0])

    def views(self, overrides: dict | None = None) -> dict:
        probs = self.model.probs(overrides)
        return {"bank_only": self._view(self.bank_r, self.model.bank_probs(overrides)),
                "event_adjusted": self._view(self.r, probs)}

    def attribution(self, overrides: dict | None = None) -> list[dict]:
        """Three views on the same operating draws: the bank view (bank data and the common borrower inputs, with
        Jev's answers to the company's decisions there); the augmented view with every residual judgment on the
        researched record neutral; the augmented view with Jev's judgments (with any overrides). Step 3 minus step 1
        is what the research adds; step 3 minus step 2 is Jev's reading of the record."""
        metrics = [self.bank_r.metrics(self.model.bank_probs(overrides)),
                   self.r.metrics(self.model.probs(neutral_overrides(self.model))),
                   self.r.metrics(self.model.probs(overrides))]
        out, prev = [], None
        for (key, label), m in zip(ATTRIBUTION, metrics, strict=True):
            delta = {k: m[k] - prev[k] for k in m if isinstance(m[k], float) and isinstance(prev.get(k), float)} \
                if prev else {}
            out.append({"step": key, "label": label, "metrics": m, "delta": delta})
            prev = m
        return out

    def scenarios(self, overrides: dict | None = None) -> list[dict]:
        probs = self.model.probs(overrides)
        rows = []
        for i, combo in enumerate(self.model.combos):
            m = {k: float(v[i]) for k, v in self.means.items()}
            rows.append({"index": i, "probability": float(probs[i]),
                         "paths": [{"dispute": p.instance_id, "outcome": p.outcome, "label": path_label(p)} for p in combo],
                         "min_cash_mean_cents": m["min_cash"],
                         "min_cash_p5_cents": float(np.quantile(self.r.min_cash[i], 0.05)),
                         "lender_pv_cents": m["lender_pv"], "dollar_days": m["dollar_days"],
                         "collected_cents": m["collected"], "stayed_claim_cents": m["stayed"],
                         "preference_exposed_cents": m["preference"], "petition_p": m["petition_p"],
                         "uncollected_maturity_cents": m["unrecovered"],
                         "peak_locked_cents": m["peak_locked"], "peak_capacity_cents": m["peak_capacity"]})
        return sorted(rows, key=lambda r: -r["probability"])

    def judgment_sensitivity(self, overrides: dict | None = None) -> list[dict]:
        """Each prospective judgment at 0%, Jev and 100% (a choice at each option held certain), all else fixed:
        ranked by the change in expected discounted lender cash flows, then principal dollar-days, then the balance
        owed and not collected (stayed or unpaid) inside the horizon."""
        base = self.expected(self.model.probs(overrides))
        rows = []
        for key, j in self.model.judgments.items():
            if tuple(j.distribution) == ("yes", "no"):
                variants = {"0%": {"yes": 0.0, "no": 1.0}, "100%": {"yes": 1.0, "no": 0.0}}
            else:
                variants = {k: {o: float(o == k) for o in j.distribution} for k in j.distribution}
            results = {name: self.expected(self.model.probs({**(overrides or {}), key: dist}))
                       for name, dist in variants.items()}
            for r in (*results.values(), base):
                r["uncollected_maturity"] = r["unrecovered"]  # the current page's name
            metrics = ("lender_pv", "dollar_days", "unrecovered", "stayed", "min_cash", "shortfall_p", "petition_p")
            rows.append({"key": key, "variants": results, "jev": base,
                         "range": {m: max(r[m] for r in results.values()) - min(r[m] for r in results.values())
                                   for m in metrics}})
        # Ranked by the lender's loss first: owed and uncollected, or stayed by a petition. Lender PV within the horizon
        # mostly tracks draw volume, so it only breaks ties.
        return sorted(rows, key=lambda r: (-round(r["range"]["unrecovered"], 2), -round(r["range"]["lender_pv"], 2),
                                           -round(r["range"]["dollar_days"], 2), -r["range"]["min_cash"]))


def stress(feed: BankFeed, setup: Setup, model: EventModel, overrides: dict | None = None) -> list[dict]:
    """Every feasible joint path under adverse placement, whatever its probability. Never weighted, never decisive.
    Each path is also run with a petition placed on the day of its highest expected outstanding balance: the stayed
    claim and preference exposure a petition would leave at the worst point for the lender."""
    a = Analysis(feed, setup, model, stress=True)
    probs = model.probs(overrides)
    rows = [{"index": i, "probability": float(probs[i]),
             "paths": [{"dispute": p.instance_id, "outcome": p.outcome, "label": path_label(p)} for p in combo], **r}
            for i, (combo, r) in enumerate(zip(model.combos, a.stress_rows, strict=True))]
    return sorted(rows, key=lambda r: r["min_cash_p5_cents"])


def dates(setup: Setup) -> list[str]:
    return [(setup.review + timedelta(days=t + 1)).isoformat() for t in range((setup.horizon - setup.review).days)]
