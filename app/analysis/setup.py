"""The analysis setup: supplied financing terms, dates and the loan they define. No policy engine selects anything."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta

from app.finance.calendar import next_business_day
from app.finance.slope_products import SlopeOffer

HORIZON_DAYS = 180
DRAWS = 512
SEED = 20240819


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

    @property
    def offer(self) -> SlopeOffer:
        return SlopeOffer(offer_id="supplied", term_id="supplied", amount_cents=self.amount_cents, fee_bps=self.fee_bps,
                          days=self.days, installments=self.installments, tier="supplied")

    def with_controls(self, controls: dict) -> Setup:
        """Apply the page's controls (validated): amount up to the invoice, fee, exposure, collateral, variability."""
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
        return replace(self, **upd)


def setup_from_inputs(inputs: dict, review: date) -> Setup:
    plan = inputs["financing_plan"]
    t = plan["supplied_terms"]
    funding = next_business_day(review + timedelta(days=1))
    return Setup(review=review, horizon=review + timedelta(days=HORIZON_DAYS), funding=funding,
                 invoice_due=next_business_day(funding + timedelta(days=plan["invoice_due_days_after_funding"])),
                 invoice_cents=t["invoice_cents"], amount_cents=t["amount_cents"], fee_bps=t["fee_bps"],
                 installments=t["installments"], days=t["days"], discount_rate_bps=t["discount_rate_bps"])
