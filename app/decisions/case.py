"""Assemble a case's decision: request, bank feed, Slope terms, dispute paths -> scenarios, conditions, export.

Used by `slope compare --run <id>` (after a recorded investigation) and by the agent's `run_scenarios` tool.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from app.config import ROOT
from app.decisions.scenarios import Request, compare, summarize
from app.disputes.rules import load_model
from app.domain.investigation import DisputeInstance
from app.domain.values import usd
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


def live(disputes: list[DisputeInstance]) -> list[DisputeInstance]:
    return [d for d in disputes if d.status != "superseded"]


def modelled(disputes: list[DisputeInstance]) -> list[DisputeInstance]:
    return [d for d in live(disputes) if d.paths]


def conditions(disputes: list[DisputeInstance], inputs: dict) -> list[dict]:
    """Funding conditions as actions (what to do, what follows if it is satisfied, and if not), generated from the
    dispute model's rules and evidence requests and from the existing lenders."""
    out, borrower = [], inputs["baseline_profile"]["borrower"]
    for d in live(disputes):
        amount = d.amount.value if d.amount.value is not None else d.amount.upper
        if any(c.kind == "lock" for p in d.paths for c in p.cash):
            out.append({"action": f"Confirm how {borrower} would secure a stay pending appeal of the {usd(amount)} owed "
                                  f"to {d.counterparty} (cash collateral or a letter of credit), and for how much.",
                        "if_satisfied": "The confirmed collateral replaces the modelled lock on the appeal paths.",
                        "if_not": "The appeal paths keep 50% to 100% of a bond at 125% of the judgment locked "
                                  "(model rule).", "instance_id": d.instance_id})
        if d.borrower_role == "creditor" and any(c.kind == "inflow" for p in d.paths for c in p.cash):
            out.append({"action": f"Count the {usd(amount)} owed by {d.counterparty} only once it is received.",
                        "if_satisfied": "Received cash enters the connected-bank data and the next review.",
                        "if_not": "The decision already stands on the paths where it is not received.",
                        "instance_id": d.instance_id})
        subject = f"the {usd(amount)} {'owed to' if d.borrower_role == 'debtor' else 'owed by'} {d.counterparty}"
        for r in d.evidence_requests:
            out.append({"action": f"{r.action.rstrip('.')} (for {subject}).", "if_satisfied": r.if_satisfied, "if_not": r.if_not,
                        "instance_id": d.instance_id, "factor_id": r.factor_id})
    for loan in inputs["baseline_profile"].get("existing_loans", []):
        out.append({"action": f"Confirm {loan['lender']}'s covenants permit this financing after the disputes' cash "
                              "effects.",
                    "if_satisfied": "No further condition.",
                    "if_not": "Decline until the lender consents.", "loan_id": loan["loan_id"]})
    return out


def decide(snapshot_id: str, inputs: dict, disputes: list[DisputeInstance], review: date) -> dict:
    feed = load_feed(snapshot_id)
    terms = load_terms(ROOT / inputs["permitted_offers"]["terms_file"])
    req = request_from_inputs(inputs, review)
    used = modelled(disputes)
    comp = compare(feed, terms, req, [list(d.paths) for d in used])
    summary = summarize(comp, terms)
    model = load_model()
    unmodelled = [d.title for d in live(disputes) if not d.paths]
    if unmodelled:  # a dispute the model could not place is never silently treated as having no cash
        summary["recommendation"][list(summary["recommendation"])[-1]]["incomplete"] = unmodelled
    summary.update({
        "request": {"amount_cents": req.amount_cents, "term_id": req.requested_term_id, "funding": req.funding.isoformat(),
                    "invoice_due": req.invoice_due.isoformat()},
        "bank_feed": {"feed_id": feed.feed_id, "provenance": feed.provenance, "as_of": feed.period_end.isoformat()},
        "slope_terms": {"terms_id": terms["terms_id"], "provenance": terms["provenance"]},
        "dispute_model": {"model_id": model["model_id"], "model_version": model["model_version"]},
        "disputes": [{"instance_id": d.instance_id, "title": d.title, "stage": d.stage, "status": d.status,
                      "paths": [{"path_id": p.path_id, "labels": list(p.labels), "weight_bps": p.weight_bps}
                                for p in d.paths], "pruned_weight_bps": d.pruned_weight_bps} for d in live(disputes)],
        "assumptions": ["The financed invoice is a purchase beyond the contract-manufacturing run rate in the bank "
                        "projection, so it is added once on top of it (conservative for the borrower's cash)."],
        "conditions": conditions(disputes, inputs),
        "weights_label": model["weights_label"] + (" Distinct disputes are combined as independent." if len(used) > 1
                                                   else ""),
    })
    return summary


def export(summary: dict, out_dir: Path) -> tuple[Path, Path]:
    """Write the decision (JSON) and the collections export: per structure, the contractual schedule, each scenario's
    conditional collections (with its dispute paths, placement and weight) and the weighted expected series (CSV)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    js = out_dir / "scenarios.json"
    js.write_text(json.dumps(summary, indent=1, default=str) + "\n")
    rows = ["structure,view,series,paths,placement,weight_bps,weight_basis,date,amount_cents"]
    basis = "model_judgment" if summary["disputes"] else ""
    for name, entry in summary["structures"].items():
        for view, v in entry["views"].items():
            if "contractual" not in v:
                continue
            rows.append(f"{name},{view},disbursement,,,,,{summary['request']['funding']},"
                        f"-{sum(r['principal_cents'] for r in v['contractual'])}")
            rows += [f"{name},{view},contractual,,,,,{r['due']},{r['amount_cents']}" for r in v["contractual"]]
            for sc in v["scenarios"]:
                paths = "+".join(sc["paths"]) or "bank_only"
                central = sc["placement"] == "central" and sc["weight_bps"] is not None
                weight, wb = (sc["weight_bps"], basis if sc["paths"] else "") if central else ("", "")
                rows += [f"{name},{view},conditional,{paths},{sc['placement']},{weight},{wb},{c['date']},"
                         f"{c['amount_cents']}" for c in sc["collections"]]
            rows += [f"{name},{view},expected,,central,,{basis},{c['date']},{c['amount_cents']}"
                     for c in v.get("expected_collections") or []]
    csv = out_dir / "collections.csv"
    csv.write_text("\n".join(rows) + "\n")
    return js, csv
