"""Three analytical proposal fixtures; not historical or actual Slope quotes."""
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parent
offers = []
for identifier, advance, months, fee in [
    ("analysis_100k_6m", 10000000, 6, "0.06"),
    ("analysis_250k_6m", 25000000, 6, "0.06"),
    ("analysis_250k_3m", 25000000, 3, "0.03"),
]:
    cost = int(Decimal(advance) * Decimal(fee))
    total = advance + cost
    regular = int((Decimal(total) / months).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    rows = [{"month_index": m, "borrower_payment_cents": regular if m < months else total - regular * (months-1)} for m in range(1, months+1)]
    assert sum(x["borrower_payment_cents"] for x in rows) == total
    offers.append({"proposal_id": identifier, "basis": "operator_assumption",
                   "advance_cents": advance, "term_months": months,
                   "fixed_total_fee_fraction": fee, "fixed_total_fee_cents": cost,
                   "total_repayment_cents": total, "funding_month_index": 0,
                   "payment_rows": rows,
                   "capacity_condition": "At every payment date, residual spendable cash after operating needs, existing merchant remittances/catch-ups, settlement installments, and policy reserve must cover this payment under the declared allocation scenario.",
                   "operating_plan_condition": "Changing the advance must change the financed purchase and its receipts where appropriate. These offers are not interchangeable injections into an unchanged operating plan."})

packet = {"schema_version": "0.1.0", "case_id": "synergy_chc_2024", "mission_id": "synergy_20240813",
          "artifact_status": "analytical_proposals_not_historical_applications_or_slope_quotes",
          "currency": "USD", "timing_basis": "monthly_indices_from_operator_selected_funding_date",
          "actual_existing_loan_id": "synergy_webbank_shopify_2024-05-01",
          "calendar_date_rule": "No funding or payment calendar dates invented. To generate dates, operator must choose funding date, monthly anchoring, end-of-month and business-day conventions; record them as assumptions.",
          "pricing_rule": "Fixed fees shown are operator sensitivity choices, not actual Slope pricing, APR, or the terms of the existing $370k WebBank loan.",
          "rounding_rule": "Regular payment rounded to nearest cent, half up; final payment adjusted to conserve exact total repayment.",
          "cross_year_calendar_rule": "When a proposal extends into 2025, include the 2025 settlement obligations and explicit installment timing assumptions. An H2-2024 aggregate cash threshold does not establish full-term affordability.",
          "existing_debt_rule": "Retain the existing $370k merchant contract separately. Its remittances follow account credits, two separate six-month minimum windows, recognized manual payments, and outstanding-total caps.",
          "offers": offers}

schema = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Analytical proposal fixtures", "type": "object",
    "required": list(packet), "additionalProperties": False,
    "properties": {key: {"type": "string"} for key in packet if key != "offers"},
}
schema["properties"]["artifact_status"] = {"const": packet["artifact_status"]}
schema["properties"]["currency"] = {"const": "USD"}
offerprops = {"proposal_id": {"type": "string"}, "basis": {"const": "operator_assumption"},
              "advance_cents": {"type": "integer", "minimum": 1}, "term_months": {"type": "integer", "minimum": 1},
              "fixed_total_fee_fraction": {"type": "string", "pattern": "^0\\.[0-9]+$"},
              "fixed_total_fee_cents": {"type": "integer", "minimum": 0},
              "total_repayment_cents": {"type": "integer", "minimum": 1}, "funding_month_index": {"const": 0},
              "capacity_condition": {"type": "string"}, "operating_plan_condition": {"type": "string"},
              "payment_rows": {"type": "array", "minItems": 1, "items": {"type": "object", "additionalProperties": False,
                                 "required": ["month_index", "borrower_payment_cents"],
                                 "properties": {"month_index": {"type": "integer", "minimum": 1}, "borrower_payment_cents": {"type": "integer", "minimum": 0}}}}}
schema["properties"]["offers"] = {"type": "array", "minItems": 1, "items": {"type": "object", "required": list(offerprops), "additionalProperties": False, "properties": offerprops}}
(ROOT / "proposal-fixtures.json").write_text(json.dumps(packet, indent=2) + "\n")
(ROOT / "proposal-fixtures.schema.json").write_text(json.dumps(schema, indent=2) + "\n")
print("Wrote three analytical amount/term/payment fixtures.")
