"""Host snapshot sweep: Jev screens every admissible section once for specific matters, then every paragraph and
table row of the flagged sections, so each reading-list item is one atomic unit.

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


async def _sweep_units(evidence: EvidenceStore, adapter: JevAdapter, company: str,
                       records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Second pass: every paragraph and table row of each flagged (or unscreened) section, judged on its own."""
    sem = asyncio.Semaphore(CONCURRENCY)
    sources = {s["source_id"]: s for s in evidence.list_sources()}
    calls: list[dict] = []

    async def one(r: dict, u: dict) -> dict:
        state = {"company": company, "passage": {"heading_path": " > ".join(r["heading_path"]), "text": u["text"]}}
        async with sem:
            call, obs = await adapter.judge(profile="snapshot_sweep", question_ids=["matter_inventory", "matter_kind"],
                                            state=state, subject_ids=(r["section_id"], f"unit_{u['start']}"),
                                            source_content_hashes=(sources[r["source_id"]]["sha256"],))
        calls.append(call.model_dump(mode="json"))
        by_q = {o.question_id: o for o in obs}
        signal, kind = by_q["matter_inventory"].noul_value, by_q["matter_kind"].answer
        return {"section_id": r["section_id"], "source_id": r["source_id"], "heading_path": r["heading_path"],
                "unit_kind": u["kind"], "start": u["start"], "end": u["end"], "excerpt": u["text"][:240],
                "signal": signal, "kind": kind, "call_id": call.call_id, "unscreened": signal is None,
                "flagged": signal is not None and signal >= FLAG_THRESHOLD and kind != "none_or_generic"}

    work = [(r, u) for r in records if r["flagged"] or r["unscreened"]
            for u in units(evidence.read_section(r["section_id"])["text"])]
    unit_records = await asyncio.gather(*(one(r, u) for r, u in work))
    return list(unit_records), calls


MIN_UNIT_CHARS = 40  # headings and fragments shorter than this are not units


def units(text: str, min_chars: int = MIN_UNIT_CHARS) -> list[dict[str, Any]]:
    """Split a section into atomic evidence units: one per paragraph and one per table row.

    A table row carries its table's header row so it keeps its column context. Offsets are into the section text, so a
    finding's verbatim span can be located in exactly one unit. Paragraphs longer than CHUNK_CHARS are hard-split.
    """
    out: list[dict[str, Any]] = []
    pos, previous = 0, ""
    for block in text.split("\n\n"):
        start = text.find(block, pos)
        start = pos if start < 0 else start
        pos = start + len(block)
        lines = block.split("\n")
        rows = [ln for ln in lines if ln.startswith("|")]
        if rows:  # a table: each data row is one unit, with the header row (first cell empty) or the introducing line
            has_header = len(rows) >= 2 and rows[0].strip("|").split("|")[0].strip() == ""
            context = rows[0] if has_header else (previous if 0 < len(previous) <= 300 else "")
            line_pos = start
            for ln in lines:
                at = text.find(ln, line_pos)
                line_pos = at + len(ln)
                if ln.startswith("|") and not (has_header and ln is rows[0]) and len(ln.strip("| ")) >= 3:
                    out.append({"start": at, "end": at + len(ln), "kind": "table_row",
                                "text": f"{context}\n{ln}" if context else ln})
            continue
        previous = block.strip()
        if len(previous) < min_chars or not previous:
            continue
        for i in range(0, len(block), CHUNK_CHARS):
            piece = block[i:i + CHUNK_CHARS]
            out.append({"start": start + i, "end": start + i + len(piece), "kind": "paragraph", "text": piece})
    return out


def unit_of(section_text: str, offset: int) -> dict[str, Any] | None:
    """The unit containing a character offset (a finding span's start), if any."""
    return next((u for u in units(section_text) if u["start"] <= offset < u["end"]), None)


def units_touching(section_text: str, start: int, end: int) -> list[dict[str, Any]]:
    """Every unit a quote [start, end) touches, short paragraphs included: a cited quote is never left unchecked."""
    return [u for u in units(section_text, min_chars=1) if u["start"] < max(end, start + 1) and start < u["end"]]


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
    async def both() -> tuple[list[dict], list[dict], list[dict], list[dict]]:
        # One event loop for both passes: the adapter's HTTP client is bound to the loop it first runs on.
        recs, cls = await _sweep(evidence, adapter, company)
        units, ucls = await _sweep_units(evidence, adapter, company, recs)
        return recs, cls, units, ucls

    records, calls, unit_records, unit_calls = asyncio.run(both())
    calls += unit_calls
    summary = {"sections": len(records), "flagged": sum(r["flagged"] for r in records),
               "unscreened": sum(r["unscreened"] for r in records), "chunks": sum(r["chunks"] for r in records),
               "units": len(unit_records), "flagged_units": sum(u["flagged"] for u in unit_records),
               "unscreened_units": sum(u["unscreened"] for u in unit_records)}
    sweep = {"snapshot_id": snapshot_id, "company": company, "registry_version": registry,
             "evidence_manifest_hash": evidence.snapshot_info()["evidence_manifest_hash"],
             "built_at": datetime.now(UTC).isoformat(), "flag_threshold": FLAG_THRESHOLD, "summary": summary,
             "jev_usage": adapter.usage_summary(), "records": records, "unit_records": unit_records, "calls": calls}
    sweep["sweep_sha256"] = canonical_sha256({"records": records, "unit_records": unit_records, "registry": registry,
                                              "manifest": sweep["evidence_manifest_hash"]})
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sweep, indent=1, default=str) + "\n")
    return sweep


def inventory_units(sweep: dict[str, Any]) -> list[dict[str, Any]]:
    """The reading list: every flagged (or unscreened) atomic unit, in document order."""
    return [u for u in sweep.get("unit_records", []) if u["flagged"] or u["unscreened"]]
