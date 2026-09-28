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
NEED_DAYS = 30  # the cash floor's central setting (spec §16.3): days of operating need
# How Slope collects (spec §16.3 Collection). "debit": each installment is debited in full, in due-date order, when
# available cash covers it; a failed debit stays overdue and is retried (the central case). "protect_need": the
# borrower keeps its next `need_days` of operating need back, so Slope collects min(owed, max(0, available - need))
# (the sensitivity; the previous §2.2 rule, and the default so earlier recorded setups read unchanged).
COLLECTION_MODES = ("debit", "protect_need")


@dataclass(frozen=True)
class Financing:
    """A dated cash booking shared by every path (spec §16.3): a financing completion (equity, or debt with its
    service: dated payments in positive cents booked as debt-service outflows), or a one-off non-operating receipt
    such as a tax refund. Proceeds on `on`, outside the operating need. Never inferred from an intention."""
    on: date
    amount_cents: int
    kind: str  # "equity" | "debt" | "receipt"
    service: tuple[tuple[date, int], ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in ("equity", "debt", "receipt") or (self.kind != "debt" and self.service):
            raise ValueError(f"financing kind {self.kind!r} with {len(self.service)} service payments")


@dataclass(frozen=True)
class CostPlan:
    """From `start`, every operating outflow except legal fees and debt service falls by `share_bps`."""
    start: date
    share_bps: int


@dataclass(frozen=True)
class Exposure:
    """The line's state on the review date when it was opened earlier: what every forecast trajectory starts from.
    Installments still to fall due (due date after the review date, whole cents), the amount already past due, the
    outstanding principal, and the net cash the line's history moved into the borrower's account (funded - collected;
    the connected feed shows the supplier payments Slope made and none of its collections). Empty: a new line."""
    installments: tuple[tuple[date, int], ...] = ()
    principal_cents: int = 0
    past_due_cents: int = 0
    cash_cents: int = 0

    @property
    def owed_cents(self) -> int:
        return self.past_due_cents + sum(c for _, c in self.installments)

    def __post_init__(self) -> None:
        if min((c for _, c in self.installments), default=1) <= 0 or self.past_due_cents < 0 or \
                not 0 <= self.principal_cents <= self.owed_cents or (self.principal_cents == 0) != (self.owed_cents == 0):
            raise ValueError(f"inconsistent opening exposure {self}")


def exposure_json(e: Exposure) -> dict:
    return {"installments": [{"due": d.isoformat(), "amount_cents": c} for d, c in e.installments],
            "principal_cents": e.principal_cents, "past_due_cents": e.past_due_cents, "cash_cents": e.cash_cents}


def exposure_from_json(d: dict | None) -> Exposure:
    if not d:
        return Exposure()
    return Exposure(installments=tuple((date.fromisoformat(i["due"]), int(i["amount_cents"])) for i in d["installments"]),
                    principal_cents=int(d["principal_cents"]), past_due_cents=int(d.get("past_due_cents", 0)),
                    cash_cents=int(d.get("cash_cents", 0)))


def is_slope_case(inputs: dict) -> bool:
    return "supplied_terms" in (inputs.get("financing_plan") or {})


@dataclass(frozen=True)
class Setup:
    """The loan and the common financial model's settings for one analysis."""
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
    # The common financial model's scenario controls (spec §16.3), shared by every path. `need_days` sets the cash
    # floor (next `need_days` days of operating need): the company's cash-floor decision, Jev's cash facts, and
    # settlement and stay capacity read it. `collection` says how Slope collects; `need_days` limits collections
    # only under "protect_need".
    need_days: int = NEED_DAYS
    collection: str = "protect_need"
    financing: tuple[Financing, ...] = ()
    cost_plan: CostPlan | None = None
    # A line opened before the review date (its state on that date; spec §16.2): installments in flight fall due and
    # are collected under §2.2, stayed on a petition under §2.3, and their principal counts against the limit.
    exposure: Exposure = Exposure()

    def __post_init__(self) -> None:
        if self.collection not in COLLECTION_MODES:
            raise ValueError(f"collection mode {self.collection!r}; expected one of {COLLECTION_MODES}")

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
        if controls.get("collection") in COLLECTION_MODES:
            upd["collection"] = controls["collection"]
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
    return {"need_days": setup.need_days, "collection": setup.collection,
            "financing": [{"date": f.on.isoformat(), "amount_cents": f.amount_cents, "kind": f.kind,
                           "service": [{"date": d.isoformat(), "amount_cents": c} for d, c in f.service]}
                          for f in setup.financing],
            "cost_plan": None if setup.cost_plan is None else {"start": setup.cost_plan.start.isoformat(),
                                                               "share_bps": setup.cost_plan.share_bps}}


def controls_from_json(d: dict) -> dict:
    """Setup fields from a case's scenario settings (any subset of need_days, collection, financing, cost_plan)."""
    out: dict = {}
    if "need_days" in d:
        out["need_days"] = int(d["need_days"])
    if "collection" in d:
        out["collection"] = str(d["collection"])
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
                 exposure=exposure_from_json((line.get("opening_state") or {}).get("exposure")),
                 **{**controls_from_json((inputs.get("common_model") or {}).get("central") or {}),
                    **controls_from_json(scenarios(inputs)[scenario])})
