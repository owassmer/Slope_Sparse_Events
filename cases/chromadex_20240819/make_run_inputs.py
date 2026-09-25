"""Generate the locked run inputs for the ChromaDex lead case from the built snapshot.

Run after `uv run slope evidence build chromadex_20240819`: `uv run python cases/chromadex_20240819/make_run_inputs.py`.
Every baseline observation and revolver cover term is checked verbatim against the stored section text.
"""

import json
import sqlite3
from pathlib import Path

from app.evidence.snapshot import EVIDENCE_DIR

db = sqlite3.connect(EVIDENCE_DIR / 'chromadex_20240819.sqlite')


def section_of(item_id: str) -> tuple[str, str]:
    if '#t' in item_id:
        sid = db.execute('SELECT section_id FROM tables WHERE table_id=?', (item_id,)).fetchone()[0]
    else:
        sid = item_id
    return sid, db.execute('SELECT text FROM sections WHERE section_id=?', (sid,)).fetchone()[0]


def find_quote(item_id: str, needle: str) -> tuple[str, str]:
    sid, text = section_of(item_id)
    for line in text.split('\n'):
        if needle in line:
            return sid, line.strip()
    raise SystemExit(f'{needle!r} not in {sid}')


def obs(oid, label, value, precision, observed_on, item, needle, note=None):
    sid, quote = find_quote(item, needle)
    assert quote in section_of(sid)[1]
    return {"observation_id": oid, "label": label, "value": value, "unit": "USD_cents", "precision": precision,
            "observed_on": observed_on, "basis": "documented_evidence", "note": note,
            "citation": {"source_id": sid.split('#')[0], "item_id": sid, "quote": quote}}


Q2 = 'cdxc_2024q2_10q'
bs = f'{Q2}#t0002'
cf = f'{Q2}#t0008'
rel = 'cdxc_2024q2_release#s0002'
observations = [
    obs("cash_20240630", "Cash and cash equivalents, including $152 thousand restricted", 2_788_500_000, "exact", "2024-06-30",
        bs, "| Cash and cash equivalents, including restricted cash of $152"),
    obs("restricted_cash_20240630", "Restricted cash (included in cash above)", 15_200_000, "exact", "2024-06-30",
        bs, "| Cash and cash equivalents, including restricted cash of $152"),
    obs("trade_receivables_20240630", "Trade receivables, net (including $3.5 million from a related party)", 781_800_000,
        "exact", "2024-06-30", bs, "| Trade receivables, net of allowances"),
    obs("inventories_20240630", "Inventories", 1_151_100_000, "exact", "2024-06-30", bs, "| Inventories |"),
    obs("accounts_payable_20240630", "Accounts payable", 810_500_000, "exact", "2024-06-30", bs, "| Accounts payable |"),
    obs("accrued_expenses_20240630", "Accrued expenses", 862_100_000, "exact", "2024-06-30", bs, "| Accrued expenses |"),
    obs("operating_cash_flow_h1_2024", "Net cash provided by operating activities, six months to June 30, 2024", 3_100_000,
        "exact", "2024-06-30", cf, "| Net cash provided by operating activities |"),
    obs("net_sales_q2_2024", "Total net sales, three months to June 30, 2024", 2_270_000_000, "approximate", "2024-06-30",
        rel, "Total net sales were $22.7 million"),
]
sid, quote = find_quote('cdxc_2023_12_credit_8k#s0002', 'revolving credit line of up to $10.0 million')
existing = [{
    "loan_id": "chromadex_western_alliance_revolver", "basis": "executed_contract", "lender": "Western Alliance Bank",
    "cover_terms": [
        {"label": "Facility size", "citation": {"item_id": sid, "quote": "The Credit Agreement provides for a revolving credit line of up to $10.0 million."}},
        {"label": "Maturity", "citation": {"item_id": sid, "quote": "The Amendment extended the maturity date of the revolving credit line under the Credit Agreement to November 12, 2025"}},
        {"label": "Balance at amendment", "citation": {"item_id": sid, "quote": "There are no amounts outstanding under the Credit Agreement, as amended, as of the date of this Current Report on Form 8-K."}},
    ],
    "note": ("Existing lender. The amendment also sets covenants on cash kept at the lender, the quick ratio and minimum "
             "liquidity (read the source for the terms that are disclosed)."),
}]
text = section_of(sid)[1]
for c in existing[0]["cover_terms"]:
    assert c["citation"]["quote"] in text, c

inputs = {
    "mission_id": "chromadex_2024_supplier_bill_financing",
    "case_id": "chromadex_elysium_2024",
    "snapshot_id": "chromadex_20240819",
    "status": "locked_operator_inputs",
    "note": ("Operator request and ordinary baseline for the 19 August 2024 review, reconstructed as if ChromaDex applied "
             "to Slope to finance a contract-manufacturer invoice. The baseline holds only ordinary underwriting facts; "
             "the litigation is for the investigation to establish."),
    "run_inputs": {
        "mission_id": "chromadex_2024_supplier_bill_financing",
        "requested_use": {"value": "Pay a contract manufacturer's invoice for Tru Niagen finished goods", "basis": "operator_request"},
        "requested_amount": {"value": 200_000_000, "unit": "USD_cents", "basis": "operator_request"},
        "requested_term": {"value": "90 days, monthly installments", "basis": "operator_request"},
        "requested_pricing": {"value": "Slope price card for the borrower's risk tier", "basis": "operator_request"},
        "baseline_profile_id": "chromadex_ordinary_baseline_v1",
        "existing_loan_record_ids": ["chromadex_western_alliance_revolver"],
        "permitted_offer_set_id": "slope_menu_v1",
        "policy_config_id": "slope_policy_v1",
        "initial_event_seed": ("As of 19 August 2024, assess Slope financing of a USD 2.0 million contract-manufacturer "
                               "invoice for ChromaDex Corporation. ChromaDex's public filings describe long-running "
                               "litigation with Elysium Health, a former customer, in several federal courts."),
    },
    "financing_plan": {
        "funding": "next business day after the review date (Slope pays the supplier)",
        "invoice_due_days_after_funding": 30,
        "basis": "operator_request",
        "note": "Without Slope, ChromaDex would pay this invoice itself on its due date.",
    },
    "permitted_offers": {"permitted_offer_set_id": "slope_menu_v1", "terms_file": "cases/slope_terms.json",
                         "requested_term_id": "inst_90"},
    "policy": {"policy_config_id": "slope_policy_v1", "terms_file": "cases/slope_terms.json"},
    "bank_feed": "cases/chromadex_20240819/bank_feed.json",
    "baseline_profile": {
        "baseline_profile_id": "chromadex_ordinary_baseline_v1",
        "borrower": "ChromaDex Corporation",
        "opening_cash_observation_id": "cash_20240630",
        "observations": observations,
        "existing_loans": existing,
    },
}
out = Path(__file__).resolve().parent / 'run_inputs.json'
out.write_text(json.dumps(inputs, indent=2, ensure_ascii=False) + '\n')
print('wrote', out, len(observations), 'observations')
for o in observations:
    print(' ', o['observation_id'], o['citation']['item_id'], '|', o['citation']['quote'][:100])
