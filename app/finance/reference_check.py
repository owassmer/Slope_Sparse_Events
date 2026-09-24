"""Reproduce the kit's reference arithmetic with the finance engine (spec §9 step 3 exit condition).

References: reference/calculation_outputs.json (contract thresholds, window counterexample,
timing fixture), contracts/proposal-fixtures.json (payment rows), and the T_H2 threshold in
contracts/economic-effects.json.
"""

from __future__ import annotations

import json
from decimal import Decimal

from app.config import CONTRACTS, KIT
from app.domain.values import Unit, assumed, documented
from app.finance.fixed_installment import load_fixture_offers
from app.finance.merchant import MerchantTerms
from app.finance.thresholds import remaining_period_required_net_cash
from app.finance.valuation import present_value_cents, principal_dollar_days

REFERENCE = KIT / "research/revision_v2/reference/calculation_outputs.json"


def _cents(usd: str) -> int:
    return int((Decimal(usd) * 100).to_integral_exact())


def checks() -> list[tuple[str, object, object]]:
    """(name, engine value, reference value) triples; each pair must be equal."""
    ref = json.loads(REFERENCE.read_text())
    out: list[tuple[str, object, object]] = []
    terms = MerchantTerms()
    ct = ref["synergy_contract_thresholds"]
    out.append(("merchant total payment", terms.total_payment_cents, _cents(ct["total_payment_usd"])))
    for cp in ct["checkpoints"]:
        m = cp["month_from_effective_funding_date"]
        out.append((f"month {m} cumulative floor", terms.cumulative_floor_cents(m),
                    _cents(cp["cumulative_lender_payment_floor_usd"])))
        out.append((f"month {m} qualifying Account Credits", terms.qualifying_credit_threshold_cents(m),
                    _cents(cp["cumulative_qualifying_shopify_credits_if_no_manual_top_ups_usd"])))
    win = ct["six_month_window_requirement"]
    out.append(("window minimum payment", terms.minimum_payment_cents, _cents(win["payment_floor_each_window_usd"])))
    out.append(("window qualifying Account Credits", terms.qualifying_credit_threshold_cents(6),
                _cents(win["qualifying_shopify_credits_each_window_without_manual_top_ups_usd"])))
    ce = ct["window_counterexample"]
    first, second = _cents(ce["first_window_daily_payments_usd"]), _cents(ce["second_window_daily_payments_usd"])
    out.append(("50%/10% cumulative-only shortfall", terms.cumulative_shortfall_only_cents(12, first + second, 0),
                _cents(ce["cumulative_shortfall_only_usd"])))
    out.append(("50%/10% second-window top-up", terms.window_top_up_cents(2, second, 0, first + second),
                _cents(ce["second_window_top_up_required_usd"])))

    tf = ref["timing_mechanics_fixture"]
    principal, rate = _cents(tf["principal_only_usd"]), Decimal(tf["effective_annual_discount_rate_assumption"])
    pv0 = present_value_cents([(tf["original_receipt_day"], principal)], rate)
    pv1 = present_value_cents([(tf["delayed_receipt_day"], principal)], rate)
    q = Decimal(1)
    out.append(("PV original receipt", int(pv0.quantize(q)), _cents(tf["pv_original_usd"])))
    out.append(("PV delayed receipt", int(pv1.quantize(q)), _cents(tf["pv_delayed_usd"])))
    out.append(("PV change from delay", int((pv1 - pv0).quantize(q)), _cents(tf["pv_change_usd"])))
    extra = (principal_dollar_days([(0, principal)], [(tf["delayed_receipt_day"], principal)])
             - principal_dollar_days([(0, principal)], [(tf["original_receipt_day"], principal)]))
    out.append(("extra principal dollar-days", extra // 100, int(tf["extra_principal_exposure_dollar_days"])))

    for offer, fixture in load_fixture_offers(CONTRACTS / "proposal-fixtures.json"):
        out.append((f"{offer.proposal_id} fee", offer.fee_cents, fixture["fixed_total_fee_cents"]))
        out.append((f"{offer.proposal_id} total", offer.total_repayment_cents, fixture["total_repayment_cents"]))
        out.append((f"{offer.proposal_id} payment rows", [p.amount_cents for p in offer.schedule()],
                    [r["borrower_payment_cents"] for r in fixture["payment_rows"]]))

    effects = json.loads((CONTRACTS / "economic-effects.json").read_text())
    t = next(x for x in effects["decision_result"]["threshold_results"] if x["threshold_id"] == "T_H2_incremental_free_cash")
    inputs = {i["name"]: i["value"]["value"] for i in t["inputs"]}
    engine = remaining_period_required_net_cash(
        [("hvl_atrium", inputs["HVL_H2_bucket_at_June30"],
          assumed(inputs["A_post_june_hvl_paid_cents"], Unit.CENTS, "A_post_june_hvl_paid_cents", "worked sensitivity")),
         ("march_supplier", inputs["supplier_H2_bucket_at_June30"],
          assumed(inputs["A_post_june_supplier_paid_cents"], Unit.CENTS, "A_post_june_supplier_paid_cents",
                  "worked sensitivity"))],
        opening_cash=documented(inputs["A_opening_cash_for_threshold_cents"], Unit.CENTS, approximate=True),
        unavailable_opening_cash=assumed(0, Unit.CENTS, "A_unavailable_opening_cash_zero", "worked sensitivity"),
        reserve=assumed(inputs["A_policy_cash_reserve_cents"], Unit.CENTS, "A_policy_cash_reserve_cents", "illustrative"))
    out.append(("H2 required net cash (T_H2)", engine.value, t["result"]["value"]))
    return out
