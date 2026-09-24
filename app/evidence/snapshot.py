"""Build one dated, case-scoped SQLite FTS5 evidence database per snapshot.

Only admissible sources enter the database. A source is admitted when all of these hold:
its catalog mission membership is `eligible` (full text) or `metadata_only` (catalog row, no text);
it belongs to the snapshot's case; its public availability is known and at or before the cutoff
(date-only availability counts at end of day in America/New_York); and its file hash matches
the catalog. Excluded sources are absent from the database entirely, including their IDs.
The host-side manifest records what was excluded and why; it is never given to the agent.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import KIT, ROOT, VAR, ConfigurationError
from app.evidence.parse import PARSER_VERSION, ParsedTable, blocks_for, sectionize

SOURCES_JSON = KIT / "research/revision_v2/data/sources.json"
CASES = ROOT / "cases"
EVIDENCE_DIR = VAR / "evidence"
NY = ZoneInfo("America/New_York")
BUILDER_VERSION = f"snapshot-1.0.0/parser-{PARSER_VERSION}"

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sources (
  source_id TEXT PRIMARY KEY, title TEXT NOT NULL, document_kind TEXT, publisher TEXT,
  primary_url TEXT, sha256 TEXT NOT NULL, available_at TEXT NOT NULL, availability_precision TEXT,
  access TEXT NOT NULL CHECK (access IN ('full', 'metadata_only')));
CREATE TABLE sections (
  section_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources, ordinal INTEGER NOT NULL,
  heading TEXT NOT NULL, heading_path TEXT NOT NULL, page INTEGER, part INTEGER NOT NULL,
  parts INTEGER NOT NULL, text TEXT NOT NULL);
CREATE TABLE tables (
  table_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources,
  section_id TEXT NOT NULL REFERENCES sections, ordinal INTEGER NOT NULL, caption TEXT NOT NULL,
  context_before TEXT NOT NULL, context_after TEXT NOT NULL, header_rows INTEGER NOT NULL,
  rows TEXT NOT NULL, rendered TEXT NOT NULL, units_hint TEXT);
CREATE VIRTUAL TABLE sections_fts USING fts5(
  section_id UNINDEXED, heading_path, text, tokenize='porter unicode61');
CREATE VIRTUAL TABLE tables_fts USING fts5(
  table_id UNINDEXED, caption, context, rendered, tokenize='porter unicode61');
"""


class CatalogError(ConfigurationError):
    """The source catalog is internally inconsistent or a file does not match its hash."""


def _catalog() -> dict[str, Any]:
    return json.loads(SOURCES_JSON.read_text())


def snapshot_config(snapshot_id: str) -> dict[str, Any]:
    path = CASES / snapshot_id / "snapshot.json"
    if not path.is_file():
        raise ConfigurationError(f"No snapshot config at cases/{snapshot_id}/snapshot.json")
    return json.loads(path.read_text())


def cutoff(snapshot_id: str) -> datetime:
    missions = _catalog()["missions"]
    if snapshot_id not in missions:
        raise ConfigurationError(f"Snapshot {snapshot_id!r} is not a mission in sources.json")
    return datetime.fromisoformat(missions[snapshot_id]["cutoff"])


def available_at(source: dict[str, Any]) -> datetime | None:
    """Earliest public availability; date-only precision counts at the end of that New York day."""
    avail = source.get("availability") or {}
    if avail.get("accepted_at"):
        return datetime.fromisoformat(avail["accepted_at"].replace("Z", "+00:00"))
    if avail.get("date"):
        day = datetime.fromisoformat(avail["date"]).date()
        return datetime.combine(day, time(23, 59, 59), NY)
    return None


def admission(snapshot_id: str) -> tuple[list[dict], list[dict]]:
    """Return (admitted, excluded). Each admitted record carries `access` and `available_at`."""
    cfg = snapshot_config(snapshot_id)
    limit = cutoff(snapshot_id)
    admitted, excluded = [], []
    for src in _catalog()["sources"]:
        sid = src["source_id"]
        membership = (src.get("mission_membership") or {}).get(snapshot_id, "not_listed")
        when = available_at(src)
        reason = None
        if src.get("case_id") != cfg["case_id"]:
            reason = "other_case"
        elif membership not in ("eligible", "metadata_only"):
            reason = f"membership_{membership}"
        elif when is None:
            reason = "availability_unknown_quarantined"
        elif when > limit:
            raise CatalogError(f"{sid} is marked {membership} for {snapshot_id} but available {when} > {limit}")
        if reason:
            excluded.append({"source_id": sid, "reason": reason})
            continue
        path = KIT / src["package_relative_path"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != src["sha256"]:
            raise CatalogError(f"Hash mismatch for {sid}: catalog {src['sha256'][:12]} file {digest[:12]}")
        admitted.append({**src, "access": "full" if membership == "eligible" else "metadata_only",
                         "available_at": when.isoformat(), "path": path})
    return admitted, excluded


def db_path(snapshot_id: str) -> Path:
    from app.evidence import SNAPSHOTS

    if snapshot_id not in SNAPSHOTS:
        raise ConfigurationError(f"Unknown snapshot {snapshot_id!r}")
    return EVIDENCE_DIR / f"{snapshot_id}.sqlite"


def build_snapshot(snapshot_id: str, out_dir: Path | None = None) -> dict[str, Any]:
    cfg = snapshot_config(snapshot_id)
    admitted, excluded = admission(snapshot_id)
    display = cfg.get("source_display", {})

    out_dir = out_dir or EVIDENCE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    db = out_dir / f"{snapshot_id}.sqlite"
    db.unlink(missing_ok=True)
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)

    counts = {}
    for src in admitted:
        sid = src["source_id"]
        meta = display.get(sid, {})
        # Catalog annotations (event_dates, availability basis, membership) are curated research
        # hints, not source text; they stay in the host manifest and never enter the agent's DB.
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?,?)", (
            sid, meta.get("title", sid), meta.get("document_kind"), meta.get("publisher"),
            src.get("primary_url"), src["sha256"], src["available_at"],
            src["availability"].get("precision"), src["access"]))
        if src["access"] != "full":
            counts[sid] = {"sections": 0, "tables": 0}
            continue
        sections = sectionize(blocks_for(src["path"]))
        n_tables = 0
        for sec in sections:
            sec_id = f"{sid}#s{sec.ordinal:04d}"
            tables = [i for i in sec.items if isinstance(i, ParsedTable)]
            table_ids = {t.ordinal: f"{sid}#t{t.ordinal:04d}" for t in tables}
            con.execute("INSERT INTO sections VALUES (?,?,?,?,?,?,?,?,?)", (
                sec_id, sid, sec.ordinal, sec.heading, json.dumps(sec.heading_path), sec.page,
                sec.part, sec.parts, sec.text(table_ids)))
            con.execute("INSERT INTO sections_fts VALUES (?,?,?)",
                        (sec_id, " > ".join(sec.heading_path), sec.text(table_ids)))
            for t in tables:
                con.execute("INSERT INTO tables VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                    table_ids[t.ordinal], sid, sec_id, t.ordinal, t.caption, t.context_before,
                    t.context_after, t.header_rows, json.dumps(t.rows), t.rendered(), t.units_hint))
                con.execute("INSERT INTO tables_fts VALUES (?,?,?,?)", (
                    table_ids[t.ordinal], t.caption, t.context_before + "\n" + t.context_after, t.rendered()))
                n_tables += 1
        counts[sid] = {"sections": len(sections), "tables": n_tables}

    manifest_core = {
        "snapshot_id": snapshot_id, "case_id": cfg["case_id"], "cutoff": cutoff(snapshot_id).isoformat(),
        "builder_version": BUILDER_VERSION,
        "sources": sorted((s["source_id"], s["sha256"], s["access"]) for s in admitted),
    }
    manifest_hash = hashlib.sha256(json.dumps(manifest_core, sort_keys=True).encode()).hexdigest()
    for k, v in {**{k: v for k, v in manifest_core.items() if k != "sources"},
                 "evidence_manifest_hash": manifest_hash}.items():
        con.execute("INSERT INTO meta VALUES (?,?)", (k, str(v)))
    con.commit()
    con.close()

    manifest = {**manifest_core, "evidence_manifest_hash": manifest_hash, "counts": counts,
                "catalog_event_dates": {s["source_id"]: s.get("event_dates", []) for s in admitted},
                "excluded": excluded, "database": db.name}
    (out_dir / f"{snapshot_id}.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
