"""Assemble a case's decision: request, bank feed, Slope terms, dispute nodes -> scenarios, conditions, export.

Used by `slope compare --run <id>` (after a recorded investigation) and by the agent's `run_scenarios` tool.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from app.config import ROOT
from app.decisions.scenarios import Request, compare, summarize
from app.domain.investigation import DisputeNode
from app.finance.bank import load_feed
from app.finance.calendar import next_business_day
from app.finance.slope_products import load_terms


def is_slope_case(inputs: dict) -> bool:
    return "terms_file" in (inputs.get("permitted_offers") or {})


def request_from_inputs(inputs: dict, review: date) -> Request:
    plan = inputs["financing_plan"]
    funding = next_business_day(review + timedelta(days=1))
    return Request(amount_cents=inputs["run_inputs"]["requested_amount"]["value"],
                   invoice_due=next_business_day(funding + timedelta(days=plan["invoice_due_days_after_funding"])),
                   funding=funding, requested_term_id=inputs["permitted_offers"]["requested_term_id"])


def conditions(nodes: list[DisputeNode], inputs: dict) -> list[dict]:
    """Funding conditions phrased as actions, from the dispute nodes and the existing lenders (deterministic)."""
    out = []
    for n in nodes:
        if any(c.kind == "lock" for b in n.branches for c in b.cash):
            out.append({"action": "Confirm how the appeal bond or other security will be provided (cash collateral or a "
                                  "letter of credit) and its amount",
                        "why": f"{n.decision_point}: a branch locks cash or credit capacity, which sets the limit",
                        "node_id": n.node_id})
        if any(c.kind == "inflow" for b in n.branches for c in b.cash):
            out.append({"action": "Do not count the judgment receivable as a repayment source until it is received",
                        "why": f"{n.decision_point}: whether and when it arrives is a branch, not a fact",
                        "node_id": n.node_id})
    for loan in inputs["baseline_profile"].get("existing_loans", []):
        out.append({"action": f"Confirm {loan['lender']}'s covenants permit this financing and have room after the "
                              "dispute's cash effects",
                    "why": "an existing lender with liquidity covenants", "loan_id": loan["loan_id"]})
    return out


def decide(snapshot_id: str, inputs: dict, nodes: list[DisputeNode], review: date) -> dict:
    feed = load_feed(snapshot_id)
    terms = load_terms(ROOT / inputs["permitted_offers"]["terms_file"])
    req = request_from_inputs(inputs, review)
    comp = compare(feed, terms, req, [n for n in nodes if n.status == "accepted"])
    summary = summarize(comp, terms)
    summary.update({
        "request": {"amount_cents": req.amount_cents, "term_id": req.requested_term_id, "funding": req.funding.isoformat(),
                    "invoice_due": req.invoice_due.isoformat()},
        "bank_feed": {"feed_id": feed.feed_id, "provenance": feed.provenance, "as_of": feed.period_end.isoformat()},
        "slope_terms": {"terms_id": terms["terms_id"], "provenance": terms["provenance"]},
        "conditions": conditions(comp.nodes, inputs),
        "weights_label": ("Branch weights are Jev's judgment of which branch the record supports (model judgment, "
                          "calibrated to frontier-model consensus, not observed litigation outcomes). Nodes are treated "
                          "as independent."),
    })
    return summary


def export(summary: dict, out_dir: Path) -> tuple[Path, Path]:
    """Write the decision (JSON) and the marginal funding export: dated contractual rows per structure (CSV)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    js = out_dir / "scenarios.json"
    js.write_text(json.dumps(summary, indent=1, default=str) + "\n")
    rows = ["structure,view,series,date,amount_cents,principal_cents,fee_cents,outstanding_principal_cents"]
    for name, entry in summary["structures"].items():
        for view, v in entry["views"].items():
            if "contractual" not in v:
                continue
            outstanding = sum(r["principal_cents"] for r in v["contractual"])
            rows.append(f"{name},{view},disbursement,{summary['request']['funding']},-{outstanding},{outstanding},0,{outstanding}")
            for r in v["contractual"]:
                outstanding -= r["principal_cents"]
                rows.append(f"{name},{view},contractual,{r['due']},{r['amount_cents']},{r['principal_cents']},"
                            f"{r['fee_cents']},{outstanding}")
    csv = out_dir / "collections.csv"
    csv.write_text("\n".join(rows) + "\n")
    return js, csv
