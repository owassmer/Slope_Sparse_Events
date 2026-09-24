"""Offline reference arithmetic. No model calls, network, or borrower forecasts."""

from dataclasses import dataclass
from decimal import Decimal, localcontext, ROUND_HALF_UP
from pathlib import Path
import json

D = Decimal
CENT = D("0.01")
ZERO = D("0")

SOURCES = {
    "synergy_merchant_agreement_20240501": {
        "url": "https://www.sec.gov/Archives/edgar/data/1562733/000121390024056991/ea020832401ex10-32_synergy.htm",
        "locator": "Exhibit 10.32; cover, definitions, Sections 4.1.1, 4.1.2 and 4.2.1(b)",
        "description": "May 1, 2024 WebBank / Synergy CHC merchant loan agreement",
    },
    "synergy_s1a_20240813": {
        "url": "https://www.sec.gov/Archives/edgar/data/1562733/000121390024068424/ea0208324-04.htm",
        "locator": "Liquidity discussion; Note 11, F-24 to F-25; legal proceedings pp. 59-60",
        "description": "Filed August 13, 2024; June 30 financials and approximately $2m cash on August 12",
    },
}


def decimal(value):
    """Reject binary floating point and non-finite monetary inputs."""
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError("Use Decimal, decimal string, or integer, never float/bool")
    result = value if isinstance(value, D) else D(value)
    if not result.is_finite():
        raise ValueError("Input must be finite")
    return result


def nonnegative(value, name):
    result = decimal(value)
    if result < ZERO:
        raise ValueError(f"{name} must be nonnegative")
    return result


def money(value):
    return format(decimal(value).quantize(CENT, rounding=ROUND_HALF_UP), ".2f")


@dataclass(frozen=True)
class MerchantTerms:
    advance: D = D("370000")
    fixed_cost: D = D("48100")
    total_payment: D = D("418100")
    remittance_rate: D = D("0.25")

    def __post_init__(self):
        for key in ("advance", "fixed_cost", "total_payment", "remittance_rate"):
            object.__setattr__(self, key, nonnegative(getattr(self, key), key))
        if self.advance + self.fixed_cost != self.total_payment:
            raise ValueError("Advance plus fixed cost must equal total payment")
        if not ZERO < self.remittance_rate <= D("1"):
            raise ValueError("Remittance rate must be in (0,1]")

    def cumulative_floor(self, month):
        fractions = {6: D("0.30"), 12: D("0.60"), 18: D("1")}
        if month not in fractions:
            raise ValueError("Only contract checkpoints 6, 12 and 18 months are defined")
        return self.total_payment * fractions[month]

    def qualifying_credit_threshold(self, month):
        """Credits needed if the floor is met entirely by the 25% remittance."""
        return self.cumulative_floor(month) / self.remittance_rate

    def cumulative_shortfall_only(self, month, remittances_received, manual_payments_received):
        """Necessary cumulative check only; NOT sufficient for the 12-month test."""
        daily = nonnegative(remittances_received, "remittances_received")
        manual = nonnegative(manual_payments_received, "manual_payments_received")
        if daily + manual > self.total_payment:
            raise ValueError("Payments cannot exceed the contractual total in this calculator")
        return max(ZERO, self.cumulative_floor(month) - daily - manual)

    def window_top_up(self, month, daily_payments_in_window,
                      accepted_manual_credit_for_window, total_received_to_date):
        """Six-month window shortfall, capped at remaining total obligation.

        Section 4.2.1(b) measures Daily Payments within each six-month window.
        Manual credit must be confirmed for this window; no automatic carry of
        first-window overpayments or unverified/reversed manual payments.
        Earlier unmet obligations are not erased by this current-window test.
        """
        if month not in (6, 12):
            raise ValueError("Window test is defined only for months 6 and 12")
        daily = nonnegative(daily_payments_in_window, "daily_payments_in_window")
        manual = nonnegative(accepted_manual_credit_for_window, "accepted_manual_credit_for_window")
        received = nonnegative(total_received_to_date, "total_received_to_date")
        if daily + manual > received or received > self.total_payment:
            raise ValueError("Window credits must fit within total lender receipts and contractual total")
        shortfall = max(ZERO, self.total_payment * D("0.30") - daily - manual)
        return min(self.total_payment - received, shortfall)


@dataclass(frozen=True)
class ConditionalBudget:
    """Single-horizon identity; None means unknown, never an assumed zero.

    Opening cash is the August 12 closing cash anchor. Future flows start after
    that date. Operating outflows EXCLUDE supplier settlements, debt service and
    reserve. Debt service EXCLUDES the supplier settlements already in H2 dues.
    Cash collections are before separately modeled debt remittances. Inflows
    must exclude any hypothetical new facility being evaluated.
    """
    opening_cash: D = D("2000000")  # Approximate management-disclosed amount.
    h2_settlements_remaining_at_june30: D = D("2600000")
    unavailable_portion_of_opening_cash: D | None = None
    settlements_paid_july1_through_august12: D | None = None
    future_customer_cash_collections: D | None = None
    future_committed_financing_draws: D | None = None
    future_other_confirmed_cash_inflows: D | None = None
    future_operating_cash_outflows_excluding_settlements_and_debt: D | None = None
    future_other_debt_service_excluding_settlements: D | None = None
    required_cash_reserve: D | None = None

    def __post_init__(self):
        for name, value in vars(self).items():
            if value is not None:
                object.__setattr__(self, name, nonnegative(value, name))
        paid = self.settlements_paid_july1_through_august12
        if paid is not None and paid > self.h2_settlements_remaining_at_june30:
            raise ValueError("Already-paid amount exceeds this specific H2 settlement bucket")
        unavailable = self.unavailable_portion_of_opening_cash
        if unavailable is not None and unavailable > self.opening_cash:
            raise ValueError("Unavailable portion exceeds reported opening cash")

    def unknown_fields(self):
        return [name for name, value in vars(self).items() if value is None]

    def remaining_settlement_cash(self):
        if self.settlements_paid_july1_through_august12 is None:
            return None
        return (self.h2_settlements_remaining_at_june30
                - self.settlements_paid_july1_through_august12)

    def horizon_residual_before_new_facility(self):
        """Conditional cash residual, NOT a maximum loan or an observed gap."""
        if self.unknown_fields():
            return None
        return (
            self.opening_cash
            - self.unavailable_portion_of_opening_cash
            + self.future_customer_cash_collections
            + self.future_committed_financing_draws
            + self.future_other_confirmed_cash_inflows
            - self.remaining_settlement_cash()
            - self.future_operating_cash_outflows_excluding_settlements_and_debt
            - self.future_other_debt_service_excluding_settlements
            - self.required_cash_reserve
        )


@dataclass(frozen=True)
class CashReceipt:
    day: int
    amount: D

    def __post_init__(self):
        if isinstance(self.day, bool) or not isinstance(self.day, int) or self.day < 0:
            raise ValueError("Receipt day must be a nonnegative integer")
        object.__setattr__(self, "amount", nonnegative(self.amount, "receipt amount"))


def delay_receipts(receipts, days):
    if isinstance(days, bool) or not isinstance(days, int) or days < 0:
        raise ValueError("Delay must be a nonnegative integer")
    return tuple(CashReceipt(r.day + days, r.amount) for r in receipts)


def present_value(receipts, effective_annual_discount_rate):
    """Discount from day zero, effective annual rate, ACT/365 convention."""
    rate = nonnegative(effective_annual_discount_rate, "discount rate")
    with localcontext() as ctx:
        ctx.prec = 40
        return sum((r.amount / ctx.power(D("1") + rate, D(r.day) / D("365"))
                    for r in receipts), ZERO)


def principal_exposure_dollar_days(principal_only_receipts):
    """Principal-only fixture: integral of outstanding principal until repaid.

    All principal is advanced at day zero and completely returned in the given
    receipts. Do not apply this to mixed principal/fee payments without an
    explicit allocation rule. No such rule is inferred for the Synergy loan.
    """
    return sum((r.amount * D(r.day) for r in principal_only_receipts), ZERO)


def build_outputs():
    terms = MerchantTerms()
    budget = ConditionalBudget()
    original = (CashReceipt(90, D("100000")),)
    delayed = delay_receipts(original, 30)
    pv_original = present_value(original, D("0.12"))
    pv_delayed = present_value(delayed, D("0.12"))
    checkpoints = []
    for month in (6, 12, 18):
        checkpoints.append({
            "month_from_effective_funding_date": month,
            "cumulative_lender_payment_floor_usd": money(terms.cumulative_floor(month)),
            "cumulative_qualifying_shopify_credits_if_no_manual_top_ups_usd":
                money(terms.qualifying_credit_threshold(month)),
            "check_scope": "necessary cumulative threshold only; separate six-month window test also applies" if month == 12 else "cumulative threshold",
        })
    return {
        "scope": "Offline deterministic reference arithmetic; no actual borrower forecast or credit decision",
        "currency": "USD",
        "numeric_encoding": "Decimal-derived base-10 strings; displayed monetary values rounded to cents",
        "sources": SOURCES,
        "synergy_contract_thresholds": {
            "source_id": "synergy_merchant_agreement_20240501",
            "evidence_class": "source-derived contractual arithmetic",
            "advance_usd": money(terms.advance),
            "fixed_cost_usd": money(terms.fixed_cost),
            "total_payment_usd": money(terms.total_payment),
            "remittance_fraction": str(terms.remittance_rate),
            "fixed_cost_as_fraction_of_advance_not_apr": str(terms.fixed_cost / terms.advance),
            "checkpoints": checkpoints,
            "six_month_window_requirement": {
                "windows": ["effective funding through month 6", "beginning of month 7 through month 12"],
                "payment_floor_each_window_usd": "125430.00",
                "qualifying_shopify_credits_each_window_without_manual_top_ups_usd": "501720.00",
                "mechanics": "Compute current-window daily payments plus confirmed applicable manual credit; do not carry first-window excess automatically; cap top-up at remaining total payment obligation.",
            },
            "window_counterexample": {
                "evidence_class": "contract arithmetic fixture, not observed payments",
                "first_window_daily_payments_usd": "209050.00",
                "second_window_daily_payments_usd": "41810.00",
                "confirmed_second_window_manual_credit_usd": "0.00",
                "cumulative_fraction_received": "0.60",
                "cumulative_shortfall_only_usd": money(terms.cumulative_shortfall_only(12, "250860", "0")),
                "second_window_top_up_required_usd": money(terms.window_top_up(12, "41810", "0", "250860")),
                "explanation": "50% paid in the first six months and 10% in months 7-12 reaches 60% cumulatively, but the second window still needs another 20% ($83,620).",
            },
            "qualifiers": [
                "Apply 25% to the contract-defined Shopify Account Credits, not company-wide sales or net cash receipts.",
                "These credit thresholds assume full collection of remittances by checkpoint and no manual top-ups.",
                "Manual payment can satisfy a floor when remittances are insufficient; threshold shortfall alone is not observed default.",
                "60% cumulative at month 12 is necessary but insufficient: each six-month window has its own 30% payment requirement.",
                "Manual-credit treatment must be confirmed for the applicable window; receipt, reversal and anti-duplication rules apply.",
                "Calendar months run from effective funding date, which is not assumed equal to the agreement date.",
                "No fee/principal allocation, APR, prepayment rebate, actual payment calendar or actual sales is inferred.",
                "The separate May 22 loan is excluded; overlapping channel sweeps need account mapping.",
            ],
        },
        "synergy_conditional_cash_budget": {
            "source_id": "synergy_s1a_20240813",
            "evidence_class": "conditional identity using dated source anchors and unresolved operator inputs",
            "information_cutoff": "2024-08-13",
            "opening_cash_measurement_date": "2024-08-12",
            "opening_cash_usd_approximate": money(budget.opening_cash),
            "opening_cash_availability": "not verified fully unrestricted or available; unavailable portion is an explicit unknown input",
            "horizon_end": "2024-12-31",
            "settlement_schedule_measurement_date": "2024-06-30",
            "h2_settlements_remaining_at_june30_usd": money(budget.h2_settlements_remaining_at_june30),
            "settlement_components_usd": {"HVL_Atrium": "2000000.00", "second_supplier_name_unknown_at_cutoff": "600000.00"},
            "unknown_inputs": {name: None for name in budget.unknown_fields()},
            "horizon_residual_before_new_facility_usd": None,
            "status": "unresolved_required_inputs; no actual August 13 funding gap asserted",
            "identity": "residual = ~2000000 - unavailable_opening_cash + future_customer_cash + future_committed_financing + future_other_confirmed_inflows - (2600000 - settlement_payments_Jul1_Aug12) - future_other_operating_outflows - future_other_debt_service - reserve",
            "conditional_break_even": "settlement_payments_Jul1_Aug12 + future_customer_cash + future_committed_financing + future_other_confirmed_inflows >= ~600000 + unavailable_opening_cash + future_other_operating_outflows + future_other_debt_service + reserve",
            "qualifiers": [
                "The ~600000 algebraic difference is not an observed cash shortfall.",
                "Existing settlement liabilities are scheduled once; do not add them again to balance-sheet debt.",
                "Cash collected and costs paid after August 12 must be separately supplied; EBITDA is not a substitute.",
                "Exclude the hypothetical new facility from committed financing; 2025 refinancing was not committed at this cutoff.",
                "A nonnegative year-end residual does not establish interim liquidity or eligibility; dated cash flows are needed.",
            ],
        },
        "timing_mechanics_fixture": {
            "evidence_class": "explicit arithmetic fixture, not Synergy or any borrower's payment schedule",
            "principal_only_usd": "100000.00",
            "original_receipt_day": 90,
            "delayed_receipt_day": 120,
            "effective_annual_discount_rate_assumption": "0.12",
            "discount_day_count_assumption": "ACT/365",
            "principal_received_original_usd": money(sum((r.amount for r in original), ZERO)),
            "principal_received_delayed_usd": money(sum((r.amount for r in delayed), ZERO)),
            "principal_loss_usd": "0.00",
            "pv_original_usd": money(pv_original),
            "pv_delayed_usd": money(pv_delayed),
            "pv_change_usd": money(pv_delayed - pv_original),
            "extra_principal_exposure_days": 30,
            "extra_principal_exposure_dollar_days": str(
                principal_exposure_dollar_days(delayed) - principal_exposure_dollar_days(original)),
            "note": "A pure delay changes PV and exposure duration without creating a principal loss. No extra interest or late fee is added. PV change is computed before cent rounding, so displayed PV subtraction can differ by one cent.",
        },
    }


if __name__ == "__main__":
    path = Path(__file__).with_name("calculation_outputs.json")
    path.write_text(json.dumps(build_outputs(), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
