"""Read-only view model of a recorded investigation, rebuilt from its verified event log."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.agent.jev import registry_question
from app.agent.jev_profiles import is_ambiguous
from app.agent.meanings import meaning
from app.agent.run_store import RunStore
from app.config import CASES_DIR, RECORDED
from app.domain.values import usd

MECHANISM_LABELS = {
    "settlement_payment_timing": "Existing settlement debt placed on a payment calendar",
    "noncash_normalization": "Noncash accounting item (normalization only)",
    "receipt_delay": "Customer receipts delayed",
    "operating_interruption": "Operating interruption",
    "restricted_funds": "Restricted funds",
    "expense_funding": "Expense funding or reimbursement",
    "funding_constraint": "Funding constraint",
    "resolved_obligation": "Resolved obligation: no future payment",
}
ROUTE_LABELS = {"direct_evidence": "Direct evidence", "conflict": "Conflicts with the question", "unscreened": "Unscreened",
                "context_only": "Context only", "instruction_flagged": "Instruction-like text (flagged)"}


def list_runs(root: Path = RECORDED) -> list[dict[str, Any]]:
    runs = []
    for d in sorted(root.glob("*/run.json"), reverse=True):
        r = json.loads(d.read_text())
        runs.append({"run_id": r.get("run_id"), "status": r.get("status"), "arm": r.get("arm"),
                     "started_at": r.get("started_at"), "snapshot_id": r.get("snapshot_id"), "counts": r.get("graph_counts", {})})
    return runs


def _money(ev: dict | None) -> str:
    if not ev:
        return "unknown"
    if ev.get("status") == "range":
        return f"{usd(ev['lower'])} to {usd(ev['upper'])}"
    if ev.get("value") is None:
        return "unknown"
    return ("approx. " if ev.get("status") == "approximate" else "") + usd(int(ev["value"]))


def build(run_id: str, root: Path = RECORDED) -> dict[str, Any]:
    run = RunStore(run_id, root=root)  # verifies the hash chain and the locked packet on load
    g = run.graph
    record = json.loads((root / run_id / "run.json").read_text()) if (root / run_id / "run.json").exists() else {}
    meta = run.events[0].payload if run.events else {}
    if "run_inputs" in meta:  # locked in the hash chain
        inputs, inputs_locked = meta["run_inputs"], True
    else:  # runs recorded before inputs were chained: show the current case file, marked as such
        inputs_path = CASES_DIR / meta.get("snapshot_id", "") / "run_inputs.json"
        inputs, inputs_locked = (json.loads(inputs_path.read_text()) if inputs_path.exists() else {}), False
    submitted = next((e.payload for e in reversed(run.events) if e.kind == "packet_submitted"), {})
    # Status is derived from the chained packet, not from the unchained run.json.
    if submitted.get("configuration_failure"):
        status = "FAILED_CONFIGURATION"
    elif submitted.get("summary") and not submitted.get("incomplete_reasons"):
        status = "CANDIDATE_READY"
    else:
        status = "INCOMPLETE_REVIEW"

    def obs_view(oid: str) -> dict[str, Any]:
        o = g["observations"][oid]
        call = g["jev_calls"].get(o.call_id)
        return {"id": oid, "question_id": o.question_id, "question": registry_question(o.question_id)["question"],
                "meaning": meaning(o.question_id, o.answer), "ambiguous": is_ambiguous(o),
                "disposition": o.downstream_disposition.replace("_", " "), "note": o.disposition_note,
                "details": {"answer": o.answer, "distribution": o.probabilities, "value": o.noul_value,
                            "question_version": o.question_version, "call_id": o.call_id,
                            "model": call.returned_model if call else None, "cache_hit": call.cache_hit if call else None}}

    findings = {}
    for f in g["findings"].values():
        findings[f.finding_id] = {
            "id": f.finding_id, "proposition": f.proposition, "target": f.target, "status": f.status,
            "is_inference": f.is_inference, "resolution_note": f.resolution_note,
            "quotes": [{"quote": s.quote, "source": run_source_title(inputs, s.source_id), "item_id": s.item_id} for s in f.spans],
            "jev": [obs_view(o) for o in f.observation_ids if o in g["observations"]]}

    effects = []
    for e in g["effects"].values():
        effects.append({"id": e.effect_id, "mechanism": MECHANISM_LABELS.get(e.mechanism, e.mechanism), "target": e.target,
                        "cash_direction": e.cash_direction, "baseline_treatment": e.baseline_treatment.replace("_", " "),
                        "consequence": e.model_consequence, "status": e.status, "problems": list(e.validation_messages),
                        "guard": e.double_count_guard,
                        "parameters": [{"name": p.name.replace("_", " "), "description": p.description, "status": p.status,
                                        "value": _money(p.value.model_dump(mode="json")) if p.value else "unknown",
                                        "basis": ("in cited quote" if p.value and p.value.provenance.basis == "documented_evidence"
                                                  else "agent-stated, not in cited quote" if p.value else "")}
                                       for p in e.parameters],
                        "findings": [findings[f] for f in e.finding_ids if f in findings],
                        "checks": [obs_view(o) for o in e.observation_ids if o in g["observations"]],
                        "overrides": dict(e.override_reasons), "dispute": e.dispute})

    deps = []
    for d in g["dependencies"].values():
        cands = [c for c in g["candidates"].values() if c.dependency_id == d.dependency_id]
        searches: dict[str, list] = {}
        for c in sorted(cands, key=lambda c: (c.search_id, c.rank)):
            searches.setdefault(f"{c.search_id}: “{c.query}”", []).append({
                "item_id": c.item_id, "heading": " › ".join(c.heading_path[-2:]), "snippet": c.snippet,
                "route": ROUTE_LABELS.get(c.screen.route, c.screen.route) if c.screen else None,
                "signals": c.screen.signals if c.screen else {}})
        deps.append({"id": d.dependency_id, "question": d.question, "target": d.target, "affects": d.affects,
                     "searches": searches, "findings": [v for v in findings.values()
                                                        if g["findings"][v["id"]].dependency_id == d.dependency_id]})

    ledger = Counter(o.downstream_disposition for o in g["observations"].values())
    titles = {sid: run_source_title(inputs, sid) for sid in {i.source_id for i in g["inventory"].values()}}
    coverage_by_item: dict[str, list] = {}
    check_labels = {"coverage_supported": "Coverage", "adds_matter": "Duplicate", "decision_relevance": "Relevance",
                    "matter_relevance": "Exclusion (earlier design)"}
    for o in g["observations"].values():
        if o.question_id in check_labels and o.subject_ids:
            coverage_by_item.setdefault(o.subject_ids[0], []).append({
                "label": check_labels[o.question_id], "id": o.observation_id,
                "meaning": meaning(o.question_id, o.answer), "answer": o.answer, "ambiguous": is_ambiguous(o),
                "disposition": o.downstream_disposition.replace("_", " "), "note": o.disposition_note,
                "findings": list(o.subject_ids[1:])})
    inventory = [{"id": i.item_id, "kind": i.kind.replace("_", " "), "source": titles.get(i.source_id, i.source_id),
                  "heading": " › ".join(i.heading_path[-2:]) or "(whole document)", "sections": len(i.section_ids),
                  "status": i.status.replace("_", " "), "findings": list(i.finding_ids), "note": i.note,
                  "duplicate_of": i.duplicate_of, "unit_kind": i.unit_kind.replace("_", " "), "excerpt": i.excerpt,
                  "coverage_checks": coverage_by_item.get(i.item_id, [])}
                 for i in sorted(g["inventory"].values(), key=lambda i: i.item_id)]
    cited_checks = [e.payload for e in run.events if e.kind == "cited_units_checked"]
    conclusion_checks = [e.payload for e in run.events if e.kind == "conclusion_checked"]
    conclusion_check = conclusion_checks[-1] if conclusion_checks else None
    return {
        "late_failure": (record.get("status") not in (None, status)) and (record.get("failure") or record.get("status")),
        "run_id": run_id, "verified_head": run.head[:16], "record": record, "meta": meta, "status": status,
        "inputs_locked": inputs_locked,
        "borrower": inputs.get("baseline_profile", {}).get("borrower", meta.get("case_id")),
        "review_date": (meta.get("snapshot_cutoff") or inputs.get("snapshot_id", ""))[:10],
        "request": inputs.get("run_inputs", {}), "submitted": submitted, "effects": effects, "dependencies": deps,
        "missing_facts": [e.payload for e in run.events if e.kind == "missing_fact_requested"],
        "sensitivities": [e.payload for e in run.events if e.kind == "sensitivity_run"],
        "reconciliations": [t.model_dump() for t in g["reconciliations"].values()],
        "jev_ledger": dict(ledger), "jev_calls": len(g["jev_calls"]), "observations": len(g["observations"]),
        "inventory": inventory, "conclusion_check": conclusion_check, "conclusion_checks": conclusion_checks,
        "decision": _decision(root / run_id),
        "disputes": _disputes(g, inputs.get("baseline_profile", {}).get("borrower", "")),
        "atomic_inventory": any(i.unit_start >= 0 for i in g["inventory"].values()),
        "cited_checks": [{**c, "checked": [{**r, "meaning": meaning("coverage_supported", r["answer"])} for r in c["checked"]]}
                         for c in cited_checks],
        "reviewer_checklist": submitted.get("reviewer_checklist"),
        "sweep": next((e.payload for e in run.events if e.kind == "inventory_loaded"), None),
    }


def _decision(run_dir: Path) -> dict | None:
    """The deterministic decision written by `slope compare` next to the recorded run, if present."""
    path = run_dir / "scenarios.json"
    if not path.exists():
        return None
    s = json.loads(path.read_text())
    views = list(s["recommendation"])
    rows = []
    shown = {r["structure"] for r in s["recommendation"].values()}
    requested = s["request"]["amount_cents"] // 100
    for name, e in s["structures"].items():
        if name != "decline" and name not in shown and not name.endswith(f"_{requested}"):
            continue  # amounts the sizing search tried stay in scenarios.json, not on the page
        row = {"name": name, "label": e["label"]}
        for view in views:
            v = e["views"][view]
            scen = v["scenarios"]
            econ = scen[0].get("economics") if scen else None
            full = sum(1 for sc in scen if sc.get("economics") and sc["economics"]["uncollected_cents"] == 0)
            row[view] = {"passes": v["policy"]["passes"], "lowest": usd(v["policy"]["lowest_projected_cash_cents"]),
                         "reasons": v["policy"]["reasons"],
                         "fee": usd(econ["fee_cents"]) if econ else "",
                         "apr": f"{econ['apr_equivalent_bps'] / 100:.1f}%" if econ else "",
                         "collected": f"repaid in full in {full} of {len(scen)} scenarios" if econ else ""}
        rows.append(row)
    def fig(f: dict | None) -> dict | None:
        if not f:
            return None
        return {**f, "amount": usd(f["amount_cents"]), "limit": usd(f["limit_cents"]),
                "order_limit": usd(f["order_limit_cents"]), "lowest": usd(f["lowest_projected_cash_cents"])}

    rec = {view: {**r, "at_requested": fig(r["at_requested"]), "at_recommended": fig(r["at_recommended"]),
                  "next_amount_up": fig(r["next_amount_up"]), "incomplete": r.get("incomplete", []),
                  "label": s["structures"][r["structure"]]["label"] if r["structure"] in s["structures"] else r["structure"]}
           for view, r in s["recommendation"].items()}
    return {"views": views, "rows": rows, "recommendation": rec, "conditions": s["conditions"], "tier": s["tier"],
            "bank_feed": s["bank_feed"], "slope_terms": s["slope_terms"], "paths_label": s["paths_label"],
            "request": s["request"]}


def _disputes(g: dict, borrower: str) -> list[dict]:
    """Each compiled dispute: who pays and the amount's status (Jev's readings), the established events and factors with
    their decisive quotes, the closed paths, and every remaining path with its rule-derived cash."""
    from app.decisions.case import describe
    from app.disputes.rules import load_model

    model = load_model()
    stages, labels, events = model["stages"], model["readings"]["amount_status_labels"], model["readings"]["events"]
    out = []
    for d in g.get("disputes", {}).values():
        if d.status == "superseded":
            continue
        out.append({
            "id": d.instance_id, "title": d.title, "obligation": describe(d, borrower, labels),
            "stage": stages[d.stage]["label"] if d.stage else "not established", "status": d.status,
            "events": [{"event": events[k], "finding": v.finding_id, "quote": v.quote} for k, v in d.established.items()],
            "factors": [{"label": f.label, "level": f.level_label, "finding": f.decisive.finding_id if f.decisive else "",
                         "quote": f.decisive.quote if f.decisive else ""} for f in d.factors if f.level is not None or f.conflict],
            "closed": d.closed,
            "paths": [{"labels": " → ".join(p.labels), "also": list(p.also), "points": list(p.points_here),
                       "cash": [{"kind": c.kind, "label": c.label, "amount": _money(c.amount.model_dump(mode="json")),
                                 "window": f"{c.window_start.isoformat()} to {c.window_end.isoformat()}", "rule": c.rule}
                                for c in p.cash]} for p in d.paths],
            "requests": [r.action for r in d.evidence_requests], "extension": d.proposed_extension})
    return out


def run_source_title(inputs: dict, source_id: str) -> str:
    cfg = CASES_DIR / (inputs.get("snapshot_id") or "") / "snapshot.json"
    if cfg.exists():
        return json.loads(cfg.read_text()).get("source_display", {}).get(source_id, {}).get("title", source_id)
    return source_id
