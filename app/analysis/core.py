"""The analysis: Jev's conditional probabilities composed over every joint event path, simulated against the shared
operating draws, carried into the reusable line's dated cash flows; bank-only versus event-adjusted; the three-step
attribution (spec §9 as scoped by §15.1); 0%/Jev/100% sensitivity of each judgment ranked by its effect on the lender;
and a separate stress view.

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
from app.analysis.engine import NEED_DAYS, NO_DUE, Trajectories, prepare, run, with_petition
from app.analysis.events import Draws, EventCash, event_cash
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
    return out


class Analysis:
    """Holds the operating draws, the line and the event cash for one setup; recomputes weights without
    re-simulating."""

    def __init__(self, feed: BankFeed, setup: Setup, model: EventModel, stress: bool = False) -> None:
        self.feed, self.setup, self.model, self.m = feed, setup, model, load_model()
        self.days = (setup.horizon - setup.review).days
        self.ops = operating.simulate(feed, self.days + NEED_DAYS, DRAWS, SEED, setup.variability)
        self.line = prepare(setup, self.ops)
        self.opening = feed.available_cents
        self._draws, self._cache = Draws(DRAWS, stress=stress), {}
        self.bank = run(self.line, self.opening, EventCash.zeros(DRAWS, self.days))
        self.paths: list[Trajectories] = [run(self.line, self.opening, self.event_cash(c)) for c in model.combos]
        per = [_scalars(t) for t in self.paths]
        self.means = {k: np.array([float(s[k].mean()) for s in per]) for k in per[0]}

    def event_cash(self, combo: tuple[DisputePath, ...]) -> EventCash:
        ev = EventCash.zeros(DRAWS, self.days)
        for p in combo:
            key = (p.instance_id, p.steps)
            if key not in self._cache:
                self._cache[key] = event_cash(self.model.disputes[p.instance_id], p, self.setup, self.m, self._draws)
            ev = ev + self._cache[key]
        return ev

    # --- summaries ---------------------------------------------------------------------------------

    def expected(self, probs: np.ndarray) -> dict[str, float]:
        return {k: expectation(v, probs) for k, v in self.means.items()}

    def _weights(self, probs: np.ndarray) -> np.ndarray:
        return np.repeat(np.asarray(probs, dtype=np.float64) / DRAWS, DRAWS)

    def _metrics(self, trajs: list[Trajectories], probs: np.ndarray) -> dict:
        w = self._weights(probs)
        per = [_scalars(t) for t in trajs]
        s = {k: np.concatenate([v[k] for v in per]) for k in per[0]}
        e = {k: float(w @ v) for k, v in s.items()}
        mq = weighted_quantiles(s["min_cash"].astype(np.float64), w, QS)
        # headroom at each due date, pooled over (trajectory, due date), each weighted by its trajectory
        rows = np.concatenate([t.headroom_rows + i * DRAWS for i, t in enumerate(trajs)])
        vals = np.concatenate([t.headroom for t in trajs]).astype(np.float64)
        hw = w[rows]
        headroom = None
        if hw.sum() > 0:
            hq = weighted_quantiles(vals, hw / hw.sum(), QS)
            headroom = {"p5_cents": float(hq[0]), "p50_cents": float(hq[1]), "p95_cents": float(hq[2]),
                        "negative_p": float(hw @ (vals < 0) / hw.sum())}
        mins = np.concatenate([t.min_headroom for t in trajs])
        has = (mins != NO_DUE) & (w > 0)
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
            "horizon_cash_mean_cents": float(w @ np.concatenate([t.cash[:, -1] for t in trajs])),
            # the current page's names: for the line there is no single maturity, so these read at the horizon
            "full_collection_by_maturity_p": min(1.0, float(w @ (s["unrecovered"] == 0))),
            "uncollected_maturity_cents": e["unrecovered"],
        }

    def _daily(self, trajs: list[Trajectories], probs: np.ndarray) -> dict:
        w = self._weights(probs)
        stack = lambda attr: np.concatenate([getattr(t, attr) for t in trajs], axis=0)  # noqa: E731
        cash, cum = stack("cash"), np.cumsum(stack("collections"), axis=1)
        undrawn = np.maximum(self.setup.facility_cents - stack("capacity"), 0)
        backup = cash + undrawn
        cq, kq, bq = (weighted_quantiles(x, w, QS) for x in (cash, cum, backup))
        oq = weighted_quantiles(stack("outstanding"), w, QS)
        # lead time: the reassessed limit is set by operating draws alone, so it is the same on every path
        lim = self.line.limit
        pet, stayed = stack("petition"), stack("stayed")
        by_day = (pet[:, None] >= 0) & (pet[:, None] <= np.arange(self.days)[None, :])
        cum_p = w @ by_day
        exposure = np.divide(w @ (by_day * stayed[:, None]), cum_p, out=np.zeros(self.days), where=cum_p > 0)
        ints = lambda x: np.rint(x).astype(np.int64).tolist()  # noqa: E731
        return {
            "cash_mean": ints(w @ cash), "cash_p5": ints(cq[0]), "cash_p50": ints(cq[1]), "cash_p95": ints(cq[2]),
            "backup_liquidity_mean": ints(w @ backup), "backup_liquidity_p5": ints(bq[0]),
            "collected_mean": ints(w @ cum), "collected_p5": ints(kq[0]), "collected_p50": ints(kq[1]),
            "collected_p95": ints(kq[2]),
            "contractual": ints(w @ np.cumsum(stack("due"), axis=1)),  # expected installments due, cumulative
            "drawn_mean": ints(w @ np.cumsum(stack("fundings"), axis=1)),
            "fundings_mean": ints(w @ stack("fundings")), "collections_mean": ints(w @ stack("collections")),
            "outstanding_mean": ints(w @ stack("outstanding")), "outstanding_p5": ints(oq[0]),
            "outstanding_p95": ints(oq[2]),
            "locked_mean": ints(w @ stack("locked")), "capacity_mean": ints(w @ stack("capacity")),
            "limit_mean": ints(lim.mean(axis=0)), "limit_p5": ints(np.quantile(lim, 0.05, axis=0, method="lower")),
            "petition_cum_p": [float(x) for x in cum_p], "petition_exposure_mean": ints(exposure),
        }

    def _view(self, trajs: list[Trajectories], probs: np.ndarray) -> dict:
        return {"metrics": self._metrics(trajs, probs), "daily": self._daily(trajs, probs)}

    def min_cash_quantile(self, probs: np.ndarray, q: float) -> float:
        w = self._weights(probs)
        return float(weighted_quantiles(np.concatenate([t.min_cash for t in self.paths]).astype(np.float64), w,
                                        (q,))[0])

    def views(self, overrides: dict | None = None) -> dict:
        probs = self.model.probs(overrides)
        return {"bank_only": self._view([self.bank], np.array([1.0])), "event_adjusted": self._view(self.paths, probs)}

    def attribution(self, overrides: dict | None = None) -> list[dict]:
        """Three reweightings: bank data only (no events); plus what the record fixes, with every residual judgment
        neutral; plus Jev's judgments (with any overrides). Step 3 minus step 2 is the signal from this record."""
        metrics = [self._metrics([self.bank], np.array([1.0])),
                   self._metrics(self.paths, self.model.probs(neutral_overrides(self.model))),
                   self._metrics(self.paths, self.model.probs(overrides))]
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
        for i, (combo, t) in enumerate(zip(self.model.combos, self.paths, strict=True)):
            m = {k: float(v[i]) for k, v in self.means.items()}
            rows.append({"index": i, "probability": float(probs[i]),
                         "paths": [{"dispute": p.instance_id, "outcome": p.outcome, "label": path_label(p)} for p in combo],
                         "min_cash_mean_cents": m["min_cash"],
                         "min_cash_p5_cents": float(np.quantile(t.min_cash, 0.05)),
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
        return sorted(rows, key=lambda r: (-round(r["range"]["lender_pv"], 2), -round(r["range"]["dollar_days"], 2),
                                           -r["range"]["unrecovered"], -r["range"]["min_cash"]))


def stress(feed: BankFeed, setup: Setup, model: EventModel, overrides: dict | None = None) -> list[dict]:
    """Every feasible joint path under adverse placement, whatever its probability. Never weighted, never decisive.
    Each path is also run with a petition placed on the day of its highest expected outstanding balance: the stayed
    claim and preference exposure a petition would leave at the worst point for the lender."""
    a = Analysis(feed, setup, model, stress=True)
    probs = model.probs(overrides)
    rows = []
    for i, (combo, t) in enumerate(zip(model.combos, a.paths, strict=True)):
        peak = int(np.argmax(t.outstanding.mean(axis=0)))
        pt = run(a.line, a.opening, with_petition(a.event_cash(combo), peak))
        rows.append({"index": i, "probability": float(probs[i]),
                     "paths": [{"dispute": p.instance_id, "outcome": p.outcome, "label": path_label(p)} for p in combo],
                     "min_cash_p5_cents": float(np.quantile(t.min_cash, 0.05)),
                     "min_cash_p50_cents": float(np.quantile(t.min_cash, 0.5)),
                     "uncollected_maturity_cents": float((t.stayed + t.uncollected).mean()),
                     "stayed_claim_cents": float(t.stayed.mean()), "lender_pv_cents": float(t.lender_pv.mean()),
                     "petition_at_peak": {"day": (setup.review + timedelta(days=peak + 1)).isoformat(),
                                          "stayed_claim_mean_cents": float(pt.stayed.mean()),
                                          "stayed_claim_p95_cents": float(np.quantile(pt.stayed, 0.95)),
                                          "preference_exposed_mean_cents": float(pt.preference.mean())}})
    return sorted(rows, key=lambda r: r["min_cash_p5_cents"])


def dates(setup: Setup) -> list[str]:
    return [(setup.review + timedelta(days=t + 1)).isoformat() for t in range((setup.horizon - setup.review).days)]
