"""Host snapshot sweep: Jev screens every admissible section once for specific matters.

Jev is cheap, independent of the agent's framing and consistent, so it is the right tool to read
everything. Two generic questions per chunk (matter_inventory, matter_kind) produce a per-snapshot
inventory of sections that describe a specific legal matter, settlement, debt agreement, covenant,
cash restriction or matter-related accounting item. The agent must account for every flagged section
before submitting. A flag is a pointer to read, never a finding.

The sweep depends only on the admissible snapshot, the borrower's name and the registry version, so it
is built once, cached at var/sweeps/<snapshot>.json with its call records, and shared by runs.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agent.jev import NOUL_THRESHOLD, JevAdapter, canonical_sha256
from app.config import VAR, agent_config, question_registry
from app.evidence.store import EvidenceStore

SWEEP_DIR = VAR / "sweeps"
CHUNK_CHARS = 8_000
CONCURRENCY = 16
FLAG_THRESHOLD = NOUL_THRESHOLD  # code-owned; checked against labelled cases in evals/


def chunks(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Split at paragraph breaks into pieces of at most `limit` characters (hard-cut only if a paragraph is longer)."""
    out, cur = [], ""
    for para in text.split("\n\n"):
        while len(para) > limit:
            if cur:
                out.append(cur)
                cur = ""
            out.append(para[:limit])
            para = para[limit:]
        if cur and len(cur) + len(para) + 2 > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out or [""]


async def _sweep(evidence: EvidenceStore, adapter: JevAdapter, company: str) -> tuple[list[dict], list[dict]]:
    sem = asyncio.Semaphore(CONCURRENCY)
    sources = {s["source_id"]: s for s in evidence.list_sources()}
    calls: list[dict] = []

    async def one(sec: dict) -> dict:
        results = []
        for i, piece in enumerate(chunks(sec["text"])):
            state = {"company": company, "passage": {"heading_path": " > ".join(sec["heading_path"]), "text": piece}}
            async with sem:
                call, obs = await adapter.judge(profile="snapshot_sweep", question_ids=["matter_inventory", "matter_kind"],
                                                state=state, subject_ids=(sec["section_id"], f"chunk_{i}"),
                                                source_content_hashes=(sources[sec["source_id"]]["sha256"],))
            calls.append(call.model_dump(mode="json"))
            by_q = {o.question_id: o for o in obs}
            results.append({"chunk": i, "signal": by_q["matter_inventory"].noul_value,
                            "kind": by_q["matter_kind"].answer, "kind_distribution": by_q["matter_kind"].probabilities,
                            "call_id": call.call_id, "excerpt": piece[:240]})
        known = [r for r in results if r["signal"] is not None]
        top = max(known, key=lambda r: r["signal"]) if known else results[0]
        return {"section_id": sec["section_id"], "source_id": sec["source_id"], "heading_path": sec["heading_path"],
                "chunks": len(results), "signal": top["signal"], "kind": top["kind"], "excerpt": top["excerpt"],
                "flagged": top["signal"] is not None and top["signal"] >= FLAG_THRESHOLD and top["kind"] != "none_or_generic",
                "unscreened": top["signal"] is None, "chunk_results": results}

    records = await asyncio.gather(*(one(s) for s in evidence.list_sections()))
    return records, calls


def sweep_path(snapshot_id: str) -> Path:
    return SWEEP_DIR / f"{snapshot_id}.json"


def build_sweep(snapshot_id: str, company: str, *, force: bool = False) -> dict[str, Any]:
    """Build (or load) the snapshot sweep. Raises JevBudgetExceeded/ConfigurationError on failure."""
    path = sweep_path(snapshot_id)
    registry = question_registry()["registry_version"]
    if path.exists() and not force:
        cached = json.loads(path.read_text())
        manifest = EvidenceStore(snapshot_id).snapshot_info()["evidence_manifest_hash"]
        if (cached["registry_version"] == registry and cached["company"] == company
                and cached.get("evidence_manifest_hash") == manifest):
            return cached
    b = agent_config()["budgets"]
    evidence = EvidenceStore(snapshot_id)
    adapter = JevAdapter(run_id=f"sweep-{snapshot_id}", max_attempts=b["sweep_max_physical_attempts_per_snapshot"],
                         spend_cap_usd=b["sweep_spend_cap_usd_per_snapshot"])
    records, calls = asyncio.run(_sweep(evidence, adapter, company))
    summary = {"sections": len(records), "flagged": sum(r["flagged"] for r in records),
               "unscreened": sum(r["unscreened"] for r in records), "chunks": sum(r["chunks"] for r in records)}
    sweep = {"snapshot_id": snapshot_id, "company": company, "registry_version": registry,
             "evidence_manifest_hash": evidence.snapshot_info()["evidence_manifest_hash"],
             "built_at": datetime.now(UTC).isoformat(), "flag_threshold": FLAG_THRESHOLD, "summary": summary,
             "jev_usage": adapter.usage_summary(), "records": records, "calls": calls}
    sweep["sweep_sha256"] = canonical_sha256({"records": records, "registry": registry,
                                              "manifest": sweep["evidence_manifest_hash"]})
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sweep, indent=1, default=str) + "\n")
    return sweep


def inventory_groups(sweep: dict[str, Any]) -> list[dict[str, Any]]:
    """Group flagged sections into matters: one per source, heading path and kind (paginated sources by source and kind)."""
    groups: dict[tuple, dict[str, Any]] = {}
    for r in sweep["records"]:
        if not (r["flagged"] or r.get("unscreened")):  # an unscreened section must be read, not silently dropped
            continue
        if r.get("unscreened"):
            r = {**r, "kind": "unscreened", "signal": 0.0}
        hp = tuple(r["heading_path"])
        paginated = not hp or (hp[-1].startswith("Page ") and hp[-1][5:].isdigit())
        key = (r["source_id"], () if paginated else hp, r["kind"])
        g = groups.setdefault(key, {"source_id": r["source_id"], "heading_path": list(key[1]), "kind": r["kind"],
                                    "section_ids": [], "section_excerpts": [], "signal": 0.0, "excerpt": ""})
        g["section_ids"].append(r["section_id"])
        g["section_excerpts"].append(r["excerpt"])
        if r["signal"] > g["signal"]:
            g["signal"], g["excerpt"] = r["signal"], r["excerpt"]
    return list(groups.values())
