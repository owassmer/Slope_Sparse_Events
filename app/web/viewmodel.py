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
                        "findings": [findings[f] for f in e.finding_ids if f in findings]})

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
    return {
        "run_id": run_id, "verified_head": run.head[:16], "record": record, "meta": meta, "status": status,
        "inputs_locked": inputs_locked,
        "borrower": inputs.get("baseline_profile", {}).get("borrower", meta.get("case_id")),
        "review_date": (meta.get("snapshot_cutoff") or inputs.get("snapshot_id", ""))[:10],
        "request": inputs.get("run_inputs", {}), "submitted": submitted, "effects": effects, "dependencies": deps,
        "missing_facts": [e.payload for e in run.events if e.kind == "missing_fact_requested"],
        "sensitivities": [e.payload for e in run.events if e.kind == "sensitivity_run"],
        "reconciliations": [t.model_dump() for t in g["reconciliations"].values()],
        "jev_ledger": dict(ledger), "jev_calls": len(g["jev_calls"]), "observations": len(g["observations"]),
    }


def run_source_title(inputs: dict, source_id: str) -> str:
    cfg = CASES_DIR / (inputs.get("snapshot_id") or "") / "snapshot.json"
    if cfg.exists():
        return json.loads(cfg.read_text()).get("source_display", {}).get(source_id, {}).get("title", source_id)
    return source_id
