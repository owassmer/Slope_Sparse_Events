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


def describe(d: DisputeInstance, borrower: str, labels: dict[str, str]) -> str:
    """The obligation in plain words, with who pays and the amount's status (a sought amount is never 'owed')."""
    amount = d.amount.value if d.amount.value is not None else d.amount.upper
    payer, payee = (borrower, d.counterparty) if d.borrower_role == "debtor" else (d.counterparty, borrower)
    status = labels.get(d.amount_status, "status not established")
    return f"{d.obligation or d.title}: {payer} to {payee}, {usd(amount)} ({status})"


def conditions(disputes: list[DisputeInstance], inputs: dict, recommendation: dict | None = None) -> list[dict]:
    """Funding conditions as actions (what to do, what follows if it is satisfied, and if not), computed from the
    tested paths, the dispute model's rules and evidence requests, and the existing lenders."""
    out, borrower = [], inputs["baseline_profile"]["borrower"]
    labels = load_model()["readings"]["amount_status_labels"]
    needs = (recommendation or {}).get("requested_needs")
    if needs:
        short = needs["paths_short_whatever_else"]
        out.append({
            "action": f"The requested amount needs at least {usd(needs['needs_lowest_cash_cents'])} of lowest projected "
                      f"cash on every path; {len(needs['short_combinations'])} tested combinations fall short. "
                      + ("Paths that fall short whatever else happens: "
                         + "; ".join(f"{e['labels']} (short by at least {usd(e['short_by_cents_at_best'])})"
                                     for e in short) + "." if short else ""),
            "if_satisfied": "Evidence that rules out every short path restores the requested amount.",
            "if_not": "Offer the recommended amount.", "computed": True})
    for d in live(disputes):
        subject = describe(d, borrower, labels)
        if any(c.kind == "lock" for p in d.paths for c in p.cash):
            out.append({"action": f"Confirm how {borrower} would secure a stay pending appeal of {subject}: cash "
                                  "collateral locks cash; a letter of credit uses credit-line capacity instead.",
                        "if_satisfied": "A letter of credit removes the cash lock from the appeal paths; confirmed cash "
                                        "collateral replaces the modelled range.",
                        "if_not": "The appeal paths keep 50% to 100% of a bond at 125% of the amount locked "
                                  "(model rule).", "instance_id": d.instance_id})
        if d.borrower_role == "creditor" and any(c.kind == "inflow" for p in d.paths for c in p.cash):
            out.append({"action": f"Count {subject} only once it is received.",
                        "if_satisfied": "Received cash enters the connected-bank data and the next review.",
                        "if_not": "The decision already stands on the paths where it is not received.",
                        "instance_id": d.instance_id})
        for r in d.evidence_requests:
            out.append({"action": r.action, "if_satisfied": r.if_satisfied, "if_not": r.if_not,
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
    last_view = list(summary["recommendation"])[-1]
    unmodelled = [d.title for d in live(disputes) if not d.paths and d.status != "resolved"]
    if unmodelled:  # a dispute the model could not place is never silently treated as having no cash
        summary["recommendation"][last_view]["incomplete"] = unmodelled
    labels = model["readings"]["amount_status_labels"]
    borrower = inputs["baseline_profile"]["borrower"]
    summary.update({
        "request": {"amount_cents": req.amount_cents, "term_id": req.requested_term_id, "funding": req.funding.isoformat(),
                    "invoice_due": req.invoice_due.isoformat()},
        "bank_feed": {"feed_id": feed.feed_id, "provenance": feed.provenance, "as_of": feed.period_end.isoformat()},
        "slope_terms": {"terms_id": terms["terms_id"], "provenance": terms["provenance"]},
        "dispute_model": {"model_id": model["model_id"], "model_version": model["model_version"]},
        "disputes": [{"instance_id": d.instance_id, "title": d.title, "obligation": describe(d, borrower, labels),
                      "stage": d.stage, "status": d.status,
                      "paths": [{"path_id": p.path_id, "labels": list(p.labels), "also": list(p.also),
                                 "points_here": list(p.points_here)} for p in d.paths],
                      "closed": d.closed} for d in live(disputes)],
        "assumptions": ["The financed invoice is a purchase beyond the contract-manufacturing run rate in the bank "
                        "projection, so it is added once on top of it (conservative for the borrower's cash).",
                        "Every combination of the disputes' paths is tested, including combinations that may be "
                        "unlikely together (conservative)."],
        "conditions": conditions(disputes, inputs, summary["recommendation"][last_view]),
        "paths_label": model["paths_label"],
    })
    return summary


def export(summary: dict, out_dir: Path) -> tuple[Path, Path]:
    """Write the decision (JSON) and the collections export: per structure, the contractual schedule and each
    scenario's conditional collections, with its dispute paths and placement (CSV)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    js = out_dir / "scenarios.json"
    js.write_text(json.dumps(summary, indent=1, default=str) + "\n")
    rows = ["structure,view,series,paths,placement,date,amount_cents"]
    for name, entry in summary["structures"].items():
        for view, v in entry["views"].items():
            if "contractual" not in v:
                continue
            rows.append(f"{name},{view},disbursement,,,{summary['request']['funding']},"
                        f"-{sum(r['principal_cents'] for r in v['contractual'])}")
            rows += [f"{name},{view},contractual,,,{r['due']},{r['amount_cents']}" for r in v["contractual"]]
            for sc in v["scenarios"]:
                paths = "+".join(sc["paths"]) or "bank_only"
                rows += [f"{name},{view},conditional,{paths},{sc['placement']},{c['date']},{c['amount_cents']}"
                         for c in sc["collections"]]
    csv = out_dir / "collections.csv"
    csv.write_text("\n".join(rows) + "\n")
    return js, csv
