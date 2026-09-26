"""The analysis: Jev's conditional probabilities composed over every joint event path, simulated against the shared
operating draws, carried into the reusable line's dated cash flows; bank-only versus event-adjusted; the three-step
attribution (spec §9 as scoped by §15.1); 0%/Jev/100% sensitivity of each judgment ranked by its effect on the lender;
and a separate stress view.

Memory is bounded by reducing each joint path as soon as it is simulated. Per path the analysis keeps the per-draw
scalars' means, the per-draw lowest cash and lowest headroom, the headroom values at each due date, per-day sums over
draws (cash, collections, outstanding, petitions, frozen claim, ...) and per-day fixed-bin histograms of available cash
and cumulative collections. Every expectation is exact (a probability-weighted sum of per-path means); a quantile of the
daily cash or collections is read from the summed histograms, within one bin width of the exact weighted quantile. The
bins are fixed across paths per day, from a first pass over the event cash alone, so no trajectory falls outside them.

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
from app.analysis.engine import NEED_DAYS, NO_DUE, PREFERENCE_DAYS, Trajectories, prepare, run, with_petition
from app.analysis.events import Basis, Draws, EventCash, event_cash
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
ATTRIBUTION = (("bank_only", "Bank data only"),
               ("record", "Plus what the record fixes, residual judgments neutral"),
               ("jev", "Plus Jev's judgments"))


@dataclass
class EventModel:
    disputes: dict[str, DisputeInstance]
    judgments: dict[str, Judgment]
    per: dict[str, dict[str, list[DisputePath]]]
    order: list[tuple[DisputeInstance, DisputeInstance | None]]
    combos: list[tuple[DisputePath, ...]] = field(default_factory=list)
    neutral: dict[str, dict[str, float]] | None = None  # the event model's neutral residuals (attribution step 2)

    def __post_init__(self) -> None:
        if not self.combos:
            self.combos = joint_paths(self.per, self.order) if self.order else [()]

    def probs(self, overrides: dict | None = None) -> np.ndarray:
        dist = distributions(self.judgments, overrides)
        return np.array([combo_probability(c, dist) for c in self.combos])


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


CASH_BINS, COLLECTED_BINS, HEADROOM_BINS = 128, 64, 256
# Per-day sums over draws kept for every path (expectations reweight them exactly).
DAILY = ("cash", "backup", "collected", "due_cum", "drawn", "fundings", "collections", "outstanding", "locked",
         "capacity", "petitioned", "frozen", "past_due", "clawback")


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
        self.per_day = {k: np.zeros((n, days)) for k in DAILY}
        self.counts = {"cash": Counts(days, bins["cash"].n), "collected": Counts(days, bins["collected"].n),
                       "headroom": Counts(len(bins["headroom"].lo), bins["headroom"].n)}
        self.hr_count = np.zeros(n)  # headroom values (trajectory x due date) per path, and how many are negative
        self.hr_negative = np.zeros(n)
        self.peak_day = np.zeros(n, dtype=np.int64)  # the day of the highest expected outstanding balance

    def add(self, i: int, t: Trajectories) -> None:
        for k, v in _scalars(t).items():
            self.means.setdefault(k, np.zeros(self.n))[i] = float(v.mean())
        self.min_cash[i], self.min_headroom[i] = t.min_cash, t.min_headroom
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
            pooled = self.counts["headroom"].weighted(probs, normalise=False).sum(axis=0, keepdims=True)
            hq = self.bins["headroom"].quantiles(pooled / pooled.sum(), QS)[:, 0]
            headroom = {"p5_cents": float(hq[0]), "p50_cents": float(hq[1]), "p95_cents": float(hq[2]),
                        "negative_p": float(probs @ self.hr_negative / hw)}
        mins = self.min_headroom[live].ravel()
        has = mins != NO_DUE
        low = weighted_quantiles(mins[has].astype(np.float64), w[has] / w[has].sum(), QS) if has.any() else None
        return {
            "drawn_cents": e["drawn"], "fees_cents": e["fees"], "contractual_cents": e["contractual"],
            "collected_cents": e["collected"], "stayed_claim_cents": e["stayed"],
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

    def daily(self, probs: np.ndarray) -> dict:
        probs = np.asarray(probs, dtype=np.float64)
        E = {k: (probs @ v) / self.draws for k, v in self.per_day.items()}
        cq = self.bins["cash"].quantiles(self.counts["cash"].weighted(probs), QS)
        kq = self.bins["collected"].quantiles(self.counts["collected"].weighted(probs), QS)
        cum_p = E["petitioned"]
        exposure = np.divide(E["frozen"], cum_p, out=np.zeros(self.days), where=cum_p > 0)
        lim = self.limit  # the reassessed limit is set by operating draws alone, so it is the same on every path
        ints = lambda x: np.rint(x).astype(np.int64).tolist()  # noqa: E731
        return {
            "cash_mean": ints(E["cash"]), "cash_p5": ints(cq[0]), "cash_p50": ints(cq[1]), "cash_p95": ints(cq[2]),
            "backup_liquidity_mean": ints(E["backup"]),
            # with no other facility backup liquidity is available cash, so its P5 is the cash P5
            "backup_liquidity_p5": ints(cq[0]) if self.setup.facility_cents == 0 else None,
            "collected_mean": ints(E["collected"]), "collected_p5": ints(kq[0]), "collected_p50": ints(kq[1]),
            "collected_p95": ints(kq[2]),
            "contractual": ints(E["due_cum"]),  # expected installments due, cumulative
            "drawn_mean": ints(E["drawn"]), "fundings_mean": ints(E["fundings"]),
            "collections_mean": ints(E["collections"]), "outstanding_mean": ints(E["outstanding"]),
            "locked_mean": ints(E["locked"]), "capacity_mean": ints(E["capacity"]),
            "limit_mean": ints(lim.mean(axis=0)), "limit_p5": ints(np.quantile(lim, 0.05, axis=0, method="lower")),
            "petition_cum_p": [float(x) for x in cum_p], "petition_exposure_mean": ints(exposure),
            "frozen_mean": ints(E["frozen"]), "past_due_mean": ints(E["past_due"]),
            "clawback_mean": ints(E["clawback"]),
        }


CACHE_PATHS = 64  # a joint model reuses each dispute's paths across combinations; one dispute never does


class Analysis:
    """Simulates every joint path on the shared operating draws, keeps each path's reduction (module docstring) and
    recomputes weights without re-simulating. With `stress`, it keeps only the stress rows."""

    def __init__(self, feed: BankFeed, setup: Setup, model: EventModel, stress: bool = False) -> None:
        self.feed, self.setup, self.model, self.m = feed, setup, model, load_model()
        self.days = (setup.horizon - setup.review).days
        self.ops = operating.simulate(feed, self.days + NEED_DAYS, DRAWS, SEED, setup.variability)
        self.line = prepare(setup, self.ops)
        self.opening = feed.available_cents
        self._draws = Draws(DRAWS, stress=stress, basis=Basis.of(self.ops, self.line.need, self.opening))
        self._cache: dict = {}
        self._shared = len(model.order) > 1
        self.bank = run(self.line, self.opening, EventCash.zeros(DRAWS, self.days))
        if stress:
            self.stress_rows = [self._stress_row(c) for c in model.combos]
            return
        bins = self._bins()
        days = [setup.review + timedelta(days=t + 1) for t in range(self.days)]
        self.months = sorted({(d.year, d.month) for d in days})
        month_of_day = np.array([self.months.index((d.year, d.month)) for d in days], dtype=np.int64)

        def make(n: int) -> Reduction:
            return Reduction(n, DRAWS, self.days, setup, self.line.limit, bins, month_of_day)

        self.bank_r = make(1)
        self.bank_r.add(0, self.bank)
        self.bank_r.finish()
        self.r = make(len(model.combos))
        for i, c in enumerate(model.combos):  # one path at a time: its dense arrays are dropped once reduced
            self.r.add(i, run(self.line, self.opening, self.event_cash(c)))
        self.r.finish()
        self._cache.clear()
        self.means = self.r.means

    def _bins(self) -> dict[str, Bins]:
        """Per-day ranges that hold every trajectory: the bank-only cash, moved by the cumulative event cash (a first
        pass over the event cash alone) and by at most the line's own swing (funded less collected lies between
        -fee x routed and the highest limit); cumulative collections lie between 0 and routed x (1 + fee); headroom
        at a due date (one range for every month) lies within the cash range, less need and at most all owed."""
        lo_ev, hi_ev = np.zeros(self.days), np.zeros(self.days)
        for c in self.model.combos:
            ev = self.event_cash(c)
            cum = np.cumsum(ev.cash - ev.lock, axis=1)
            np.minimum(lo_ev, cum.min(axis=0), out=lo_ev)
            np.maximum(hi_ev, cum.max(axis=0), out=hi_ev)
        routed = np.cumsum(self.line.routes.sum(axis=2), axis=1).max(axis=0)
        fee = (self.setup.fee_bps + 1) / 10_000  # + 1 bp covers the half-up cent on each draw
        margin = float(self.line.limit.max()) + routed * fee + 10_000
        lo, hi = self.bank.cash.min(axis=0) + lo_ev - margin, self.bank.cash.max(axis=0) + hi_ev + margin
        owed = float(routed[-1]) * (1 + fee) + 10_000
        months = len({(d.year, d.month) for d in (self.setup.review + timedelta(days=t + 1) for t in range(self.days))})
        h_lo = float(lo.min()) - float(self.line.need.max()) - owed - float(self.line.routes.sum(axis=2).max())
        return {"cash": Bins.spanning(lo, hi, CASH_BINS),
                "collected": Bins.spanning(np.zeros(self.days), routed * (1 + fee) + 10_000, COLLECTED_BINS),
                "headroom": Bins.spanning(np.full(months, h_lo), np.full(months, float(hi.max()) + owed), HEADROOM_BINS)}

    def event_cash(self, combo: tuple[DisputePath, ...]) -> EventCash:
        ev = EventCash.zeros(DRAWS, self.days)
        for p in combo:
            key = (p.instance_id, p.steps)
            e = self._cache.get(key)
            if e is None:
                e = event_cash(self.model.disputes[p.instance_id], p, self.setup, self.m, self._draws)
                if self._shared and len(self._cache) < CACHE_PATHS:
                    self._cache[key] = e
            ev = ev + e
        return ev

    def _stress_row(self, combo: tuple[DisputePath, ...]) -> dict:
        """One path under adverse placement, and again with a petition on the day of its highest expected
        outstanding balance."""
        ev = self.event_cash(combo)
        t = run(self.line, self.opening, ev)
        peak = int(np.argmax(t.outstanding.mean(axis=0)))
        pt = run(self.line, self.opening, with_petition(ev, peak))
        return {"min_cash_p5_cents": float(np.quantile(t.min_cash, 0.05)),
                "min_cash_p50_cents": float(np.quantile(t.min_cash, 0.5)),
                "uncollected_maturity_cents": float((t.stayed + t.uncollected).mean()),
                "stayed_claim_cents": float(t.stayed.mean()), "lender_pv_cents": float(t.lender_pv.mean()),
                "petition_at_peak": {"day": (self.setup.review + timedelta(days=peak + 1)).isoformat(),
                                     "stayed_claim_mean_cents": float(pt.stayed.mean()),
                                     "stayed_claim_p95_cents": float(np.quantile(pt.stayed, 0.95)),
                                     "preference_exposed_mean_cents": float(pt.preference.mean())}}

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
        return {"bank_only": self._view(self.bank_r, np.array([1.0])), "event_adjusted": self._view(self.r, probs)}

    def attribution(self, overrides: dict | None = None) -> list[dict]:
        """Three reweightings: bank data only (no events); plus what the record fixes, with every residual judgment
        neutral; plus Jev's judgments (with any overrides). Step 3 minus step 2 is the signal from this record."""
        metrics = [self.bank_r.metrics(np.array([1.0])),
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
