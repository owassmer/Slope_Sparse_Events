"""Generate the locked run inputs for the Akoustis lead case reviewed on 14 May 2024 (spec §16) from the built snapshot and the bank feed.

Run after `uv run slope evidence build akoustis_20240514` and `make_bank_feed.py`:
`uv run python cases/akoustis_20240514/make_run_inputs.py`.

Slope's line (Model Extensions Spec §2.1): limit = 15% x (mean monthly customer receipts - mean monthly debt service)
over the trailing three complete months of the connected-bank feed, rounded down to the cent; 3.7% fee; each draw
repaid in 3 equal monthly installments. The reusable-line engine is the next step, so the current engine still receives
one supplied draw: the largest supplier invoice in the trailing window that fits under the limit. Every baseline
observation and loan cover term is checked verbatim against the stored section text.
"""

import json
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from app.evidence.snapshot import EVIDENCE_DIR
from app.finance.bank import load_feed, window

SNAP = "akoustis_20240514"
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

# The line was opened before the review (spec §16.2): the trial and the 13 May 10-Q trigger a review of an existing
# line. Slope sizes a line on three complete months of connected-bank history; the feed reconstructs the account from
# 1 Jan 2024, so 1 Apr 2024 (a Monday) is the first day the published rule can be applied to it.
OPENED = date(2024, 4, 1)
open_months = ["2024-01", "2024-02", "2024-03"]
open_receipts = Decimal(sum(t["amount_cents"] for t in feed.transactions if t["category"] == "customer_receipts"
                            and t["date"][:7] in open_months)) / 3
open_debt = Decimal(-sum(t["amount_cents"] for t in feed.transactions if t["category"] == "debt_service"
                         and t["date"][:7] in open_months)) / 3
open_limit = int((LIMIT_SHARE * (open_receipts - open_debt)).to_integral_value(ROUND_DOWN))
assert open_limit > 25_000_000  # above Slope's USD 250k automatic approval: a manually reviewed line
opened = {
    "date": OPENED.isoformat(),
    "limit_cents": open_limit,
    "basis": ("The earliest date with three complete months of connected-bank history in the feed (January to March "
              "2024): Slope's rule gives 15% of mean monthly receipts net of debt service, USD "
              f"{open_limit / 100:,.2f}, above the USD 250k automatic approval, so a manually reviewed line. It opens "
              "five weeks before the jury trial began on 6 May (docket as of 14 May); the trial and the 13 May 10-Q "
              "then trigger this review. Eligibility (Slope's published criteria): in business since May 2014 (FY2023 "
              "10-K: 'since its inception in May 2014'), so more than 3 years for a line above USD 100k; banking "
              "history of more than 1 year (the company's own accounts; the feed reconstructs only the months the "
              "rule reads); no bankruptcy in the last 5 years."),
}

# The common financial model's scenario settings (spec §16.3), shared by every path.
q3 = [t for t in feed.transactions if "2024-01-01" <= t["date"] <= "2024-03-31"]
burn = -sum(t["amount_cents"] for t in q3 if t["category"] in ("customer_receipts", "payroll", "legal_fees",
                                                                  "supplier_invoice"))
cut_base = -sum(t["amount_cents"] for t in q3 if t["amount_cents"] < 0 and t["category"] not in (
    "customer_receipts", "debt_service", "legal_fees", "equity_proceeds"))
assert burn == 779_100_000, burn  # the 10-Q's March-quarter operating cash burn (the call: "$7.8 million")
CUT_BPS = int((Decimal(3) * burn * 1000 / cut_base).to_integral_value())  # 30% of the burn, over the outflows cut
# CHIPS investment tax credit, low end prorated over the horizon (15 May to 10 Nov 2024): USD 2.8M over 12 months,
# booked at each calendar month's last day inside the horizon (the horizon's last day for November).
REVIEW, HORIZON = date(2024, 5, 14), date(2024, 11, 10)
ITC_LOW = 280_000_000
itc, d, acc = [], REVIEW + timedelta(days=1), 0
while d <= HORIZON:
    end = min(date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1), HORIZON)
    days_so_far = (end - REVIEW).days
    amt = ITC_LOW * days_so_far // 365 - acc
    acc += amt
    itc.append({"date": end.isoformat(), "amount_cents": amt, "kind": "receipt", "service": []})
    d = end + timedelta(days=1)
assert acc == ITC_LOW * 180 // 365 == 138_082_191, acc

common_model = {
    "note": ("Settings every path shares (spec §16.3). Central: the feed's historical continuation, no financing, a "
             "30-day operating reserve. Each scenario changes one setting and states its basis."),
    "central": {"need_days": 30, "financing": [], "cost_plan": None},
    "scenarios": {
        "equity_injection": {
            "financing": [{"date": "2024-06-14", "amount_cents": 500_000_000, "kind": "equity", "service": []}],
            "basis": ("A declared scenario, not the record: a plain equity injection of USD 5.0M booked on 14 Jun 2024, "
                      "one month after the review. On 14 May the company was re-activating its at-the-market program "
                      "(ATM Sales Agreement of 2 May 2022, USD 48.0M remaining, 10-Q of 13 May 2024); the agents have "
                      "no obligation to sell, so the program fixes no amount or date. Usable capacity is probably "
                      "limited by the baby-shelf cap (about one third of public float per 12 months, net of the "
                      "January raise: roughly USD 9.7M, an inference, not stated before the cutoff); USD 5.0M sits "
                      "inside it. The central case books no financing."),
        },
        "cost_plan": {
            "cost_plan": {"start": "2024-05-15", "share_bps": CUT_BPS},
            "basis": ("Earnings call of 13 May 2024 (CFO): aggressive expense reduction and cost-saving measures, as "
                      "well as pursuing the investment tax credits, 'all of which we estimate will reduce our operating "
                      "cash flow burn rate by an additional 30% sequentially in the June quarter'. The March quarter's "
                      f"operating burn was USD {burn / 100:,.0f}; 30% of it is taken off every operating outflow except "
                      f"the litigation-spend proxy and debt service ({cut_base / 100:,.0f} in the March quarter), a "
                      f"{CUT_BPS / 100:.2f}% cut, from the day after the review to the horizon, so the June quarter's "
                      "operating burn at the March run rate is about 30% below the March quarter's (USD 7.8M, itself "
                      "31% below December). Opex guided to USD 10-11M a quarter is an accrual figure and is not used. The call does not split "
                      "the tax-credit refund from the cost cuts, so the whole 30% is read as lower outflows; revenue "
                      "stays on its own path (guided flat to down 5%)."),
        },
        "chips_itc_low": {
            "financing": itc,
            "basis": ("A labelled sensitivity. Earnings call of 13 May 2024 (CEO): 'We currently estimate the amount of "
                      "the refundable tax credit applicable to [Akoustis] to be between $2.8 and $4 million over the next "
                      "nine to 12 months.' No date is given, so the central case books none inside the horizon. Here the "
                      "low end is prorated evenly over 12 months and the part falling inside the horizon (USD "
                      f"{acc / 100:,.0f}) is booked month by month, as a one-off receipt outside the operating need."),
        },
        "reserve_60_days": {
            "need_days": 60,
            "basis": "The operating reserve at 60 days of operating need instead of 30 (the one alternative setting).",
        },
    },
    "ordinary_obligations": {
        "note": ("Existing debt on every path (spec §16.3), carried as case inputs; the dispute model owns what triggers "
                 "a change. Nothing here is booked by the operating simulation."),
        "items": [
            {"instrument": "6.0% convertible senior notes due 2027", "due": "2024-06-15", "amount_cents": 132_000_000,
             "form": "in cash and/or shares at the company's option",
             "basis": ("USD 44.0M x 6% / 2 (10-Q Note 10; notes 8-K of June 2022: payable 15 Jun and 15 Dec). The "
                       "13 May S-3 registers 5,000,000 more note-interest shares for resale. 15 Jun 2024 is a Saturday. "
                       "The next coupon, 15 Dec 2024, is after the horizon.")},
            {"instrument": "GDSI secured promissory note (USD 4.0M, no interest)", "due": "2025-01-01",
             "amount_cents": 270_000_000, "form": "cash",
             "basis": ("Step-down to USD 1.3M on the second anniversary of the 1 Jan 2023 closing (10-Q Note 10; FY2023 "
                       "10-K Note 7): no GDSI cash falls due inside the horizon unless accelerated.")},
        ],
    },
    "runway_statement": ("10-Q of 13 May 2024, Note 2 and MD&A overview: cash 'is sufficient to fund its operations into "
                         "the third quarter of fiscal 2025', with substantial doubt about going concern. The MD&A's "
                         "'at least the next twelve months' sentence contradicts both and is not used."),
    "legal_spend": ("The legal_fees category is a professional-fee PROXY (the March quarter's year-on-year rise in "
                    "professional fees and property tax, 10-Q of 13 May 2024). It is the spend attributed to the "
                    "dispute: it stops from the day the dispute ends (payment, settlement or vacatur); other spend "
                    "continues."),
}

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
    "opened": opened,
    "supplied_draw": {"transaction_id": draw_txn["transaction_id"], "date": draw_txn["date"],
                      "counterparty": draw_txn["counterparty"], "amount_cents": draw,
                      "basis": "largest supplier invoice in the trailing window that fits under the limit"},
}

inputs = {
    "mission_id": "akoustis_2024_supplier_line",
    "case_id": "akoustis_qorvo_2024",
    "snapshot_id": SNAP,
    "status": "locked_operator_inputs",
    "note": ("Operator request and ordinary baseline for the 14 May 2024 review, reconstructed as if Akoustis applied "
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
        "baseline_profile_id": "akoustis_ordinary_baseline_20240514",
        "existing_loan_record_ids": [x["loan_id"] for x in existing],
        "permitted_offer_set_id": "slope_supplied_terms_v1",
        "policy_config_id": "analysis_only",
        "initial_event_seed": ("As of 14 May 2024, analyse how external events could affect a Slope reusable line "
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
    "common_model": common_model,
    "baseline_profile": {
        "baseline_profile_id": "akoustis_ordinary_baseline_20240514",
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
