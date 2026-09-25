"""The analysis: Jev's conditional probabilities composed over every joint event path, simulated against the shared
operating draws, carried into the loan's dated cash flows; bank-only versus event-adjusted; 0%/Jev/100% sensitivity
of each judgment ranked by its effect on the lender; financial-parameter sensitivity; and a separate stress view.

Each trajectory (joint path p, operating draw d) has weight P(p) / draws. Every structurally feasible path is simulated,
whatever its probability; a zero-probability path simply carries no weight in the distribution and stays in stress.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import timedelta

import numpy as np

from app.analysis import operating
from app.analysis.engine import Trajectories, run, schedule_arrays
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


@dataclass
class EventModel:
    disputes: dict[str, DisputeInstance]
    judgments: dict[str, Judgment]
    per: dict[str, dict[str, list[DisputePath]]]
    order: list[tuple[DisputeInstance, DisputeInstance | None]]
    combos: list[tuple[DisputePath, ...]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.combos:
            self.combos = joint_paths(self.per, self.order) if self.order else [()]

    def probs(self, overrides: dict | None = None) -> np.ndarray:
        dist = distributions(self.judgments, overrides)
        return np.array([combo_probability(c, dist) for c in self.combos])


def path_label(p: DisputePath) -> str:
    return " → ".join(STEP_LABELS[(n, b)] for n, _, b in p.steps if (n, b) in STEP_LABELS)


class Analysis:
    """Holds the operating draws and event cash for one setup; recomputes weights without re-simulating."""

    def __init__(self, feed: BankFeed, setup: Setup, model: EventModel, stress: bool = False) -> None:
        self.feed, self.setup, self.model, self.m = feed, setup, model, load_model()
        self.days = (setup.horizon - setup.review).days
        self.ops = operating.simulate(feed, self.days, DRAWS, SEED, setup.variability)
        draws = Draws(DRAWS, stress=stress)
        cache: dict = {}

        def cash_for(p: DisputePath) -> EventCash:
            key = (p.instance_id, p.steps)
            if key not in cache:
                cache[key] = event_cash(model.disputes[p.instance_id], p, setup, self.m, draws)
            return cache[key]

        opening = feed.available_cents
        self.bank = run(setup, opening, self.ops, EventCash.zeros(DRAWS, self.days))
        self.paths: list[Trajectories] = []
        for combo in model.combos:
            ev = EventCash.zeros(DRAWS, self.days)
            for p in combo:
                ev = ev + cash_for(p)
            self.paths.append(run(setup, opening, self.ops, ev))
        self.means = {k: np.array([getattr(t, k).mean() for t in self.paths]) for k in
                      ("lender_pv", "dollar_days", "uncollected_maturity", "uncollected_horizon", "min_cash")}
        self.means["shortfall_p"] = np.array([(t.min_cash < 0).mean() for t in self.paths])
        self.means["shortfall"] = np.array([np.maximum(-t.min_cash, 0).mean() for t in self.paths])
        self.means["full_p"] = np.array([(t.uncollected_maturity == 0).mean() for t in self.paths])
        self.means["peak_locked"] = np.array([t.locked.max(axis=1).mean() for t in self.paths])
        self.means["peak_capacity"] = np.array([t.capacity.max(axis=1).mean() for t in self.paths])

    # --- summaries ---------------------------------------------------------------------------------

    def expected(self, probs: np.ndarray) -> dict[str, float]:
        return {k: expectation(v, probs) for k, v in self.means.items()}

    def _view(self, trajs: list[Trajectories], probs: np.ndarray) -> dict:
        w = np.repeat(probs / DRAWS, DRAWS)
        stack = lambda attr: np.concatenate([getattr(t, attr) for t in trajs], axis=0)  # noqa: E731
        cash, cum = stack("cash"), np.cumsum(stack("collections"), axis=1)
        mins = stack("min_cash")
        unc = stack("uncollected_maturity")
        cq, kq, mq = weighted_quantiles(cash, w, QS), weighted_quantiles(cum, w, QS), weighted_quantiles(mins, w, QS)
        due, maturity, _, total = schedule_arrays(self.setup)
        return {
            "metrics": {
                "lender_pv_cents": float(w @ stack("lender_pv")), "dollar_days": float(w @ stack("dollar_days")),
                "full_collection_by_maturity_p": float(w @ (unc == 0)),
                "uncollected_maturity_cents": float(w @ unc),
                "uncollected_horizon_cents": float(w @ stack("uncollected_horizon")),
                "min_cash_mean_cents": float(w @ mins), "min_cash_p5_cents": float(mq[0]),
                "shortfall_p": float(w @ (mins < 0)), "shortfall_mean_cents": float(w @ np.maximum(-mins, 0)),
                "peak_locked_cents": float(w @ stack("locked").max(axis=1)),
                "peak_capacity_cents": float(w @ stack("capacity").max(axis=1)),
                "horizon_cash_mean_cents": float(w @ cash[:, -1]),
            },
            "daily": {
                "cash_mean": np.rint(w @ cash).astype(int).tolist(),
                "cash_p5": cq[0].astype(int).tolist(), "cash_p50": cq[1].astype(int).tolist(),
                "cash_p95": cq[2].astype(int).tolist(),
                "collected_mean": np.rint(w @ cum).astype(int).tolist(),
                "collected_p5": kq[0].astype(int).tolist(), "collected_p50": kq[1].astype(int).tolist(),
                "collected_p95": kq[2].astype(int).tolist(),
                "contractual": np.cumsum(due).astype(int).tolist(),
                "outstanding_mean": np.rint(w @ stack("outstanding")).astype(int).tolist(),
                "locked_mean": np.rint(w @ stack("locked")).astype(int).tolist(),
                "capacity_mean": np.rint(w @ stack("capacity")).astype(int).tolist(),
            },
            "maturity_index": maturity, "total_repayable_cents": total,
        }

    def min_cash_quantile(self, probs: np.ndarray, q: float) -> float:
        w = np.repeat(probs / DRAWS, DRAWS)
        return float(weighted_quantiles(np.concatenate([t.min_cash for t in self.paths]), w, (q,))[0])

    def views(self, overrides: dict | None = None) -> dict:
        probs = self.model.probs(overrides)
        return {"bank_only": self._view([self.bank], np.array([1.0])), "event_adjusted": self._view(self.paths, probs)}

    def scenarios(self, overrides: dict | None = None) -> list[dict]:
        probs = self.model.probs(overrides)
        rows = []
        for i, (combo, t) in enumerate(zip(self.model.combos, self.paths, strict=True)):
            rows.append({"index": i, "probability": float(probs[i]),
                         "paths": [{"dispute": p.instance_id, "outcome": p.outcome, "label": path_label(p)} for p in combo],
                         "min_cash_mean_cents": float(t.min_cash.mean()),
                         "min_cash_p5_cents": float(np.quantile(t.min_cash, 0.05)),
                         "uncollected_maturity_cents": float(t.uncollected_maturity.mean()),
                         "lender_pv_cents": float(t.lender_pv.mean()), "dollar_days": float(t.dollar_days.mean()),
                         "peak_locked_cents": float(t.locked.max(axis=1).mean()),
                         "peak_capacity_cents": float(t.capacity.max(axis=1).mean())})
        return sorted(rows, key=lambda r: -r["probability"])

    def judgment_sensitivity(self, overrides: dict | None = None) -> list[dict]:
        """Each prospective judgment at 0%, Jev and 100% (a choice at each option held certain), all else fixed:
        ranked by the change in expected discounted lender cash flows, then principal dollar-days, then the
        uncollected balance at maturity."""
        base = self.expected(self.model.probs(overrides))
        rows = []
        for key, j in self.model.judgments.items():
            if tuple(j.distribution) == ("yes", "no"):
                variants = {"0%": {"yes": 0.0, "no": 1.0}, "100%": {"yes": 1.0, "no": 0.0}}
            else:
                variants = {k: {o: float(o == k) for o in j.distribution} for k in j.distribution}
            results = {name: self.expected(self.model.probs({**(overrides or {}), key: dist}))
                       for name, dist in variants.items()}
            metrics = ("lender_pv", "dollar_days", "uncollected_maturity", "min_cash", "shortfall_p")
            rows.append({"key": key, "variants": results, "jev": base,
                         "range": {m: max(r[m] for r in results.values()) - min(r[m] for r in results.values())
                                   for m in metrics}})
        return sorted(rows, key=lambda r: (-round(r["range"]["lender_pv"], 2), -round(r["range"]["dollar_days"], 2),
                                           -r["range"]["uncollected_maturity"], -r["range"]["min_cash"]))


def parameter_sensitivity(feed: BankFeed, setup: Setup, model: EventModel, overrides: dict | None = None) -> list[dict]:
    """Financial parameters, kept apart from the signal: collateral share, the borrower-pays exposure, operating
    variability. Each variant re-simulates with the same seeds."""
    out = []
    for name, variants in [("collateral_share", [("50%", {"collateral_share": (0.5, 0.5)}),
                                                 ("75%", {"collateral_share": (0.75, 0.75)}),
                                                 ("100%", {"collateral_share": (1.0, 1.0)})]),
                           ("exposure", [("75%", {"exposure_scale": setup.exposure_scale * 0.75}),
                                         ("87.5%", {"exposure_scale": setup.exposure_scale * 0.875}),
                                         ("100%", {"exposure_scale": setup.exposure_scale})]),
                           ("operating_variability", [("0.5x", {"variability": 0.5}), ("1x", {"variability": 1.0}),
                                                      ("1.5x", {"variability": 1.5})])]:
        rows = []
        for label, change in variants:
            a = Analysis(feed, replace(setup, **change), model)
            probs = model.probs(overrides)
            rows.append({"label": label, **a.expected(probs), "min_cash_p5": a.min_cash_quantile(probs, 0.05)})
        out.append({"parameter": name, "variants": rows})
    return out


def stress(feed: BankFeed, setup: Setup, model: EventModel, overrides: dict | None = None) -> list[dict]:
    """Every feasible joint path under adverse placement, whatever its probability. Never weighted, never decisive."""
    a = Analysis(feed, setup, model, stress=True)
    probs = model.probs(overrides)
    rows = [{"index": i, "probability": float(probs[i]),
             "paths": [{"dispute": p.instance_id, "outcome": p.outcome, "label": path_label(p)} for p in combo],
             "min_cash_p5_cents": float(np.quantile(t.min_cash, 0.05)),
             "min_cash_p50_cents": float(np.quantile(t.min_cash, 0.5)),
             "uncollected_maturity_cents": float(t.uncollected_maturity.mean()),
             "lender_pv_cents": float(t.lender_pv.mean())}
            for i, (combo, t) in enumerate(zip(model.combos, a.paths, strict=True))]
    return sorted(rows, key=lambda r: r["min_cash_p5_cents"])


def dates(setup: Setup) -> list[str]:
    return [(setup.review + timedelta(days=t + 1)).isoformat() for t in range((setup.horizon - setup.review).days)]
