"""The analysis setup: supplied financing terms, dates and the loan they define. No policy engine selects anything."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta

from app.finance.calendar import next_business_day
from app.finance.slope_products import SlopeOffer

HORIZON_DAYS = 180
DRAWS = 512
SEED = 20240819
MAX_LIMIT_MULTIPLIER = 33 / 15
NEED_DAYS = 30  # the operating reserve's central setting (spec §16.3): days of operating need


@dataclass(frozen=True)
class Financing:
    """A financing completion booked on every path (spec §16.3): proceeds on `on`; a debt booking also carries its
    service, dated payments in positive cents booked as debt-service outflows. Never inferred from an intention."""
    on: date
    amount_cents: int
    kind: str  # "equity" | "debt"
    service: tuple[tuple[date, int], ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in ("equity", "debt") or (self.kind == "equity" and self.service):
            raise ValueError(f"financing kind {self.kind!r} with {len(self.service)} service payments")


@dataclass(frozen=True)
class CostPlan:
    """From `start`, every operating outflow except legal fees and debt service falls by `share_bps`."""
    start: date
    share_bps: int


def is_slope_case(inputs: dict) -> bool:
    return "supplied_terms" in (inputs.get("financing_plan") or {})


@dataclass(frozen=True)
class Setup:
    review: date
    horizon: date
    funding: date
    invoice_due: date
    invoice_cents: int
    amount_cents: int  # financed, up to the invoice
    fee_bps: int
    installments: int
    days: int
    discount_rate_bps: int
    exposure_scale: float = 1.0  # multiplies the payer-side exposure of disputes where the borrower pays
    collateral_share: tuple[float, float] | None = None  # override of the model's 50%-100% surety collateral range
    variability: float = 1.0  # scales operating deviations around the simulated mean
    # Slope's reusable line (spec §2.1): limit = limit_share x mean monthly (customer receipts - debt service) over the
    # trailing three complete months, reassessed at every draw. The single-draw fields above stay as the reference
    # draw that `slope finance check` and the agent's loan-terms tool read.
    limit_share_bps: int = 1500
    limit_multiplier: float = 1.0  # 1.0 to 33/15: the low to the high end of Slope's published 15-33% range
    line_usage: float = 1.0  # share of eligible supplier invoices the borrower routes through Slope
    facility_cents: int = 0  # other committed facilities (backup liquidity); Akoustis has none
    # The common financial model's scenario controls (spec §16.3), shared by every path. `need_days` is the one
    # operating-reserve setting: collections capacity, the company-response triggers and Jev's facts all read it.
    need_days: int = NEED_DAYS
    financing: tuple[Financing, ...] = ()
    cost_plan: CostPlan | None = None

    @property
    def share_bps(self) -> int:
        """The limit share after the multiplier, in basis points."""
        return int(round(self.limit_share_bps * self.limit_multiplier))

    @property
    def offer(self) -> SlopeOffer:
        return SlopeOffer(offer_id="supplied", term_id="supplied", amount_cents=self.amount_cents, fee_bps=self.fee_bps,
                          days=self.days, installments=self.installments, tier="supplied")

    def with_controls(self, controls: dict) -> Setup:
        """Apply the page's controls (validated): amount up to the invoice, fee, exposure, collateral, variability,
        line usage and the limit multiplier."""
        upd = {}
        if controls.get("amount_cents") is not None:
            upd["amount_cents"] = max(0, min(int(controls["amount_cents"]), self.invoice_cents))
        if controls.get("fee_bps") is not None:
            upd["fee_bps"] = max(0, min(int(controls["fee_bps"]), 3000))
        if controls.get("exposure_scale") is not None:
            upd["exposure_scale"] = max(0.0, min(float(controls["exposure_scale"]), 2.0))
        if controls.get("collateral_share") is not None:
            lo, hi = (max(0.0, min(float(x), 1.0)) for x in controls["collateral_share"])
            upd["collateral_share"] = (min(lo, hi), max(lo, hi))
        if controls.get("variability") is not None:
            upd["variability"] = max(0.0, min(float(controls["variability"]), 3.0))
        if controls.get("line_usage") is not None:
            upd["line_usage"] = max(0.0, min(float(controls["line_usage"]), 1.0))
        if controls.get("limit_multiplier") is not None:
            upd["limit_multiplier"] = max(1.0, min(float(controls["limit_multiplier"]), MAX_LIMIT_MULTIPLIER))
        if controls.get("need_days") is not None:
            upd["need_days"] = max(0, min(int(controls["need_days"]), 90))
        return replace(self, **upd)


def financing_from_json(items: list[dict]) -> tuple[Financing, ...]:
    return tuple(Financing(on=date.fromisoformat(f["date"]), amount_cents=int(f["amount_cents"]), kind=f["kind"],
                           service=tuple((date.fromisoformat(p["date"]), int(p["amount_cents"]))
                                         for p in f.get("service", ())))
                 for f in items)


def cost_plan_from_json(c: dict | None) -> CostPlan | None:
    return None if c is None else CostPlan(start=date.fromisoformat(c["start"]), share_bps=int(c["share_bps"]))


def controls_json(setup: Setup) -> dict:
    """The scenario controls as JSON (the inverse of `controls_from_json`)."""
    return {"need_days": setup.need_days,
            "financing": [{"date": f.on.isoformat(), "amount_cents": f.amount_cents, "kind": f.kind,
                           "service": [{"date": d.isoformat(), "amount_cents": c} for d, c in f.service]}
                          for f in setup.financing],
            "cost_plan": None if setup.cost_plan is None else {"start": setup.cost_plan.start.isoformat(),
                                                               "share_bps": setup.cost_plan.share_bps}}


def controls_from_json(d: dict) -> dict:
    """Setup fields from a case's scenario settings (any subset of need_days, financing, cost_plan)."""
    out: dict = {}
    if "need_days" in d:
        out["need_days"] = int(d["need_days"])
    if "financing" in d:
        out["financing"] = financing_from_json(d["financing"] or [])
    if "cost_plan" in d:
        out["cost_plan"] = cost_plan_from_json(d["cost_plan"])
    return out


def scenarios(inputs: dict) -> dict[str, dict]:
    """The case's named scenario settings over the central one (`common_model.scenarios`); central is {}."""
    return {"central": {}, **{k: {f: v for f, v in s.items() if f != "basis"}
                              for k, s in ((inputs.get("common_model") or {}).get("scenarios") or {}).items()}}


def setup_from_inputs(inputs: dict, review: date, scenario: str = "central") -> Setup:
    """The supplied terms and the common financial model's central settings, or a named scenario over them."""
    plan = inputs["financing_plan"]
    t = plan["supplied_terms"]
    line = plan.get("line") or {}
    funding = next_business_day(review + timedelta(days=1))
    return Setup(review=review, horizon=review + timedelta(days=HORIZON_DAYS), funding=funding,
                 invoice_due=next_business_day(funding + timedelta(days=plan["invoice_due_days_after_funding"])),
                 invoice_cents=t["invoice_cents"], amount_cents=t["amount_cents"],
                 fee_bps=line.get("fee_bps", t["fee_bps"]), installments=line.get("installments", t["installments"]),
                 days=t["days"], discount_rate_bps=t["discount_rate_bps"],
                 limit_share_bps=line.get("limit_share_bps", 1500),
                 line_usage=line.get("line_usage_bps", 10_000) / 10_000,
                 **{**controls_from_json((inputs.get("common_model") or {}).get("central") or {}),
                    **controls_from_json(scenarios(inputs)[scenario])})
