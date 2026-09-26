"""Generate the locked run inputs for the Akoustis lead case from the built snapshot and the bank feed.

Run after `uv run slope evidence build akoustis_20240620` and `make_bank_feed.py`:
`uv run python cases/akoustis_20240620/make_run_inputs.py`.

Slope's line (Model Extensions Spec §2.1): limit = 15% x (mean monthly customer receipts - mean monthly debt service)
over the trailing three complete months of the connected-bank feed, rounded down to the cent; 3.7% fee; each draw
repaid in 3 equal monthly installments. The reusable-line engine is the next step, so the current engine still receives
one supplied draw: the largest supplier invoice in the trailing window that fits under the limit. Every baseline
observation and loan cover term is checked verbatim against the stored section text.
"""

import json
import sqlite3
from collections import defaultdict
from datetime import date
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from app.evidence.snapshot import EVIDENCE_DIR
from app.finance.bank import load_feed, window

SNAP = "akoustis_20240620"
LIMIT_SHARE = Decimal("0.15")  # the low end of Slope's published 15-33% range, for a stressed profile
FEE_BPS, INSTALLMENTS, DAYS = 370, 3, 90
LABEL = "Terms reconstructed by applying Slope's published sizing rule to the connected-bank baseline."

db = sqlite3.connect(EVIDENCE_DIR / f"{SNAP}.sqlite")


def section_text(sid: str) -> str:
    return db.execute("SELECT text FROM sections WHERE section_id=?", (sid,)).fetchone()[0]


def find_line(sid: str, needle: str) -> str:
    for line in section_text(sid).split("\n"):
        if needle in line:
            return line.strip()
    raise SystemExit(f"{needle!r} not in {sid}")


def obs(oid, label, value, precision, observed_on, sid, needle, note=None):
    quote = find_line(sid, needle)
    return {"observation_id": oid, "label": label, "value": value, "unit": "USD_cents", "precision": precision,
            "observed_on": observed_on, "basis": "documented_evidence", "note": note,
            "citation": {"source_id": sid.split("#")[0], "item_id": sid, "quote": quote}}


def term(label: str, sid: str, quote: str) -> dict:
    assert quote in section_text(sid), (sid, quote)
    return {"label": label, "citation": {"item_id": sid, "quote": quote}}


Q = "akts_2024q3_10q"
observations = [
    obs("cash_20240331", "Cash and cash equivalents", 1_520_000_000, "exact", "2024-03-31", f"{Q}#s0006",
        "| Cash and cash equivalents |"),
    obs("accounts_receivable_20240331", "Accounts receivable, net", 444_800_000, "exact", "2024-03-31", f"{Q}#s0006",
        "| Accounts receivable, net |"),
    obs("inventory_20240331", "Inventory", 510_400_000, "exact", "2024-03-31", f"{Q}#s0006", "| Inventory |"),
    obs("accounts_payable_20240331", "Accounts payable", 479_600_000, "exact", "2024-03-31", f"{Q}#s0023",
        "| Accounts payable |"),
    obs("accrued_professional_fees_20240331", "Accrued professional fees", 679_700_000, "exact", "2024-03-31",
        f"{Q}#s0023", "| Accrued professional fees |"),
    obs("operating_cash_flow_9m_20240331", "Net cash used in operating activities, nine months to March 31, 2024",
        -3_223_800_000, "exact", "2024-03-31", f"{Q}#s0010", "| Net Cash Used in Operating Activities |"),
    obs("revenue_q3_fy2024", "Revenue, three months to March 31, 2024", 751_000_000, "exact", "2024-03-31",
        f"{Q}#s0007", "| Revenue | $7,510 |"),
    obs("offering_gross_20240524", "Registered direct offering, gross proceeds (closed May 24, 2024)", 1_000_000_000,
        "approximate", "2024-05-24", "akts_2024_05_24_8k#s0005", "Gross proceeds from the Offering were approximately"),
]
notes_8k, gdsi = "akts_2022_06_notes_8k#s0005", f"{Q}#s0025"
existing = [
    {"loan_id": "akoustis_convertible_notes_2027", "basis": "executed_contract",
     "lender": "Holders of the 6.0% Convertible Senior Notes due 2027 (The Bank of New York Mellon Trust Company, "
               "N.A., trustee)",
     "cover_terms": [
         term("Principal", notes_8k, "issued $44.0 million aggregate principal amount of its 6.0% Convertible Senior "
                                     "Notes due 2027"),
         term("Maturity", notes_8k, "The Notes bear interest at a rate of 6.0% per year until maturity on June 15, 2027"),
         term("Interest dates", notes_8k, "payable semi-annually in arrears on June 15 and December 15 of each year"),
         term("Interest form", notes_8k, "At the Company’s option, interest may be paid in cash and/or freely "
                                         "tradable shares of the Company’s common stock"),
     ],
     "note": "Existing notes. The indenture (akts_2022_06_notes_indenture) sets the events of default and repurchase "
             "rights; read the source for those terms."},
    {"loan_id": "akoustis_gdsi_promissory_note", "basis": "executed_contract",
     "lender": "Sellers' representative, GDSI acquisition (January 2023)",
     "cover_terms": [
         term("Principal", gdsi, "issued a secured promissory note (the “Promissory Note”) in the original principal "
                                 "amount of $ 4.0 million"),
         term("Interest and schedule", gdsi, "The Promissory Note does not bear interest, is subject to partial "
                                             "prepayment (reduction of the outstanding principal amount down to $ 1.3 "
                                             "million) on the second anniversary of the Closing Date, and is payable "
                                             "in full on the third anniversary of the Closing Date."),
     ],
     "note": "Existing seller note, secured by assets of the purchaser and GDSI."},
]

# Slope's line rule on the connected-bank feed.
feed = load_feed(SNAP)
start, end = window(feed)
receipts, debt = defaultdict(int), defaultdict(int)
for t in feed.transactions:
    d = date.fromisoformat(t["date"])
    if start <= d <= end:
        if t["category"] == "customer_receipts":
            receipts[t["date"][:7]] += t["amount_cents"]
        elif t["category"] == "debt_service":
            debt[t["date"][:7]] -= t["amount_cents"]
months = sorted({t["date"][:7] for t in feed.transactions if start <= date.fromisoformat(t["date"]) <= end})
assert len(months) == 3, months
mean_receipts = Decimal(sum(receipts[m] for m in months)) / 3
mean_debt = Decimal(sum(debt[m] for m in months)) / 3
limit = int((LIMIT_SHARE * (mean_receipts - mean_debt)).to_integral_value(ROUND_DOWN))
invoices = [-t["amount_cents"] for t in feed.transactions if t["category"] == "supplier_invoice"
            and start <= date.fromisoformat(t["date"]) <= end and -t["amount_cents"] <= limit]
draw = max(invoices)
draw_txn = next(t for t in feed.transactions if t["category"] == "supplier_invoice" and -t["amount_cents"] == draw
                and start <= date.fromisoformat(t["date"]) <= end)

line = {
    "label": LABEL,
    "rule": "limit = 15% x (mean monthly customer_receipts - mean monthly debt_service), trailing three complete months",
    "limit_share_bps": int(LIMIT_SHARE * 10_000),
    "window": {"start": start.isoformat(), "end": end.isoformat()},
    "monthly_customer_receipts_cents": {m: receipts[m] for m in months},
    "monthly_debt_service_cents": {m: debt[m] for m in months},
    "mean_monthly_customer_receipts_cents": int(mean_receipts.to_integral_value(ROUND_DOWN)),
    "mean_monthly_debt_service_cents": int(mean_debt.to_integral_value(ROUND_DOWN)),
    "limit_cents": limit,
    "fee_bps": FEE_BPS, "installments": INSTALLMENTS, "days": DAYS,
    "reassessment": "same rule at every draw date, on each trajectory's trailing receipts (next engine step)",
    "draw_rule": "each supplier_invoice outflow is routed while outstanding + invoice <= the day's limit, no installment "
                 "is overdue and no petition has been filed",
    "line_usage_bps": 10_000,
    "supplied_draw": {"transaction_id": draw_txn["transaction_id"], "date": draw_txn["date"],
                      "counterparty": draw_txn["counterparty"], "amount_cents": draw,
                      "basis": "largest supplier invoice in the trailing window that fits under the limit"},
}

inputs = {
    "mission_id": "akoustis_2024_supplier_line",
    "case_id": "akoustis_qorvo_2024",
    "snapshot_id": SNAP,
    "status": "locked_operator_inputs",
    "note": ("Operator request and ordinary baseline for the 20 June 2024 review, reconstructed as if Akoustis applied "
             "to Slope for a reusable line that pays supplier invoices. " + LABEL + " The baseline holds only ordinary "
             "underwriting facts; the litigation is for the investigation to establish."),
    "run_inputs": {
        "mission_id": "akoustis_2024_supplier_line",
        "requested_use": {"value": "Draws on a Slope reusable line to pay supplier invoices (fab materials and contract "
                                   "manufacturing)", "basis": "operator_request"},
        "requested_amount": {"value": draw, "unit": "USD_cents", "basis": "operator_request"},
        "requested_term": {"value": "Reusable line; each draw repaid in three monthly installments",
                           "basis": "operator_request"},
        "requested_pricing": {"value": "3.7% fee on each amount financed", "basis": "operator_request"},
        "baseline_profile_id": "akoustis_ordinary_baseline_v1",
        "existing_loan_record_ids": [x["loan_id"] for x in existing],
        "permitted_offer_set_id": "slope_supplied_terms_v1",
        "policy_config_id": "analysis_only",
        "initial_event_seed": ("As of 20 June 2024, analyse how external events could affect a Slope reusable line "
                               "that pays supplier invoices for Akoustis Technologies, Inc., each draw repaid in three "
                               "monthly installments. Akoustis's public filings describe trade-secret and patent "
                               "litigation with Qorvo, Inc., a competitor, in the U.S. District Court for the District "
                               "of Delaware."),
    },
    "financing_plan": {
        "funding": "next business day after the review date (Slope pays the supplier)",
        "invoice_due_days_after_funding": 30,
        "basis": "operator_request",
        "note": "Slope pays the supplier up to the financed amount; Akoustis pays any remainder of the invoice itself.",
        "supplied_terms": {"invoice_cents": draw, "amount_cents": draw, "installments": INSTALLMENTS, "days": DAYS,
                           "fee_bps": FEE_BPS, "discount_rate_bps": 800,
                           "product": "Slope reusable line: Slope pays supplier invoices up to the limit; each draw is "
                                      "repaid in monthly installments by ACH"},
        "line": line,
    },
    "permitted_offers": {"permitted_offer_set_id": "slope_supplied_terms_v1"},
    "policy": {"policy_config_id": "analysis_only"},
    "bank_feed": f"cases/{SNAP}/bank_feed.json",
    "baseline_profile": {
        "baseline_profile_id": "akoustis_ordinary_baseline_v1",
        "borrower": "Akoustis Technologies, Inc.",
        "opening_cash_observation_id": "cash_20240331",
        "observations": observations,
        "existing_loans": existing,
    },
}
out = Path(__file__).resolve().parent / "run_inputs.json"
out.write_text(json.dumps(inputs, indent=2, ensure_ascii=False) + "\n")
print("wrote", out, len(observations), "observations")
print("cash at", feed.period_end, feed.closing_cents / 100, "| mean receipts", line["mean_monthly_customer_receipts_cents"] / 100,
      "| mean debt service", line["mean_monthly_debt_service_cents"] / 100, "| limit", limit / 100, "| draw", draw / 100,
      draw_txn["date"], draw_txn["counterparty"])
