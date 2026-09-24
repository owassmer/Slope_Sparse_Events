"""Read-only access to one snapshot's evidence database.

This is the only evidence interface the investigation tools use. The store opens its snapshot
database read-only and never takes a path, database name or cutoff from the caller. An ID that
is not in the snapshot (nonexistent, other case or later than the cutoff) gets the same
"not available" error, so the error does not reveal that a later document exists.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from app.evidence.snapshot import db_path


class EvidenceAccessError(LookupError):
    pass


class EvidenceStore:
    def __init__(self, snapshot_id: str, path: Path | None = None) -> None:
        path = path or db_path(snapshot_id)
        if not path.is_file():
            raise EvidenceAccessError(f"Snapshot {snapshot_id!r} has not been built (uv run slope evidence build)")
        self.con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row
        self.meta = {r["key"]: r["value"] for r in self.con.execute("SELECT key, value FROM meta")}
        if self.meta.get("snapshot_id") != snapshot_id:
            raise EvidenceAccessError(f"Database does not belong to snapshot {snapshot_id!r}")
        self.snapshot_id = snapshot_id

    # --- catalog -----------------------------------------------------------------------------

    def _source(self, source_id: str) -> dict[str, Any]:
        row = self.con.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,)).fetchone()
        if row is None:
            raise EvidenceAccessError(f"{source_id!r} is not available in this snapshot")
        d = dict(row)
        d["event_dates"] = json.loads(d["event_dates"])
        return d

    def list_sources(self) -> list[dict[str, Any]]:
        rows = self.con.execute(
            "SELECT s.*, (SELECT count(*) FROM sections x WHERE x.source_id = s.source_id) AS n_sections, "
            "(SELECT count(*) FROM tables t WHERE t.source_id = s.source_id) AS n_tables "
            "FROM sources s ORDER BY available_at").fetchall()
        return [{**dict(r), "event_dates": json.loads(r["event_dates"])} for r in rows]

    def snapshot_info(self) -> dict[str, str]:
        return {k: self.meta[k] for k in ("snapshot_id", "case_id", "cutoff", "evidence_manifest_hash")}

    # --- search ------------------------------------------------------------------------------

    @staticmethod
    def _fts_query(text: str, mode: str) -> str:
        terms = re.findall(r"[\w$.,%-]+", text)
        terms = [t.strip(".,") for t in terms if t.strip(".,")]
        return f" {mode} ".join('"' + t.replace('"', "") + '"' for t in terms)

    def search(self, query: str, *, source_ids: list[str] | None = None, limit: int = 8) -> list[dict[str, Any]]:
        """Full-text search over sections and tables. All terms must match; if nothing does,
        any term may match. Results carry IDs, heading path, source date and a snippet."""
        for sid in source_ids or []:
            self._source(sid)  # rejects IDs outside the snapshot
        results: list[dict[str, Any]] = []
        for mode in ("AND", "OR"):
            fts = self._fts_query(query, mode)
            if not fts:
                return []
            results = self._run_search(fts, source_ids, limit)
            if results:
                break
        return results

    def _run_search(self, fts: str, source_ids: list[str] | None, limit: int) -> list[dict[str, Any]]:
        where = ""
        args: list[Any] = [fts]
        if source_ids:
            where = f" AND x.source_id IN ({','.join('?' * len(source_ids))})"
            args += source_ids
        sec_rows = self.con.execute(
            "SELECT 'section' AS kind, x.section_id AS id, x.source_id, x.heading_path, x.page, "
            "snippet(sections_fts, 2, '[', ']', ' … ', 32) AS snippet, bm25(sections_fts) AS score "
            "FROM sections_fts JOIN sections x USING (section_id) "
            f"WHERE sections_fts MATCH ?{where} ORDER BY score LIMIT ?", (*args, limit)).fetchall()
        tab_rows = self.con.execute(
            "SELECT 'table' AS kind, x.table_id AS id, x.source_id, s.heading_path, s.page, "
            "snippet(tables_fts, 1, '[', ']', ' … ', 24) || ' :: ' || "
            "snippet(tables_fts, 3, '[', ']', ' … ', 24) AS snippet, bm25(tables_fts) AS score "
            "FROM tables_fts JOIN tables x USING (table_id) JOIN sections s ON s.section_id = x.section_id "
            f"WHERE tables_fts MATCH ?{where} ORDER BY score LIMIT ?", (*args, limit)).fetchall()
        rows = sorted([*sec_rows, *tab_rows], key=lambda r: r["score"])[:limit]
        out = []
        for r in rows:
            src = self._source(r["source_id"])
            out.append({"kind": r["kind"], "id": r["id"], "source_id": r["source_id"],
                        "source_title": src["title"], "source_available_at": src["available_at"],
                        "heading_path": json.loads(r["heading_path"]), "page": r["page"],
                        "snippet": r["snippet"]})
        return out

    # --- reads -------------------------------------------------------------------------------

    def read_section(self, section_id: str) -> dict[str, Any]:
        row = self.con.execute("SELECT * FROM sections WHERE section_id = ?", (section_id,)).fetchone()
        if row is None:
            raise EvidenceAccessError(f"{section_id!r} is not available in this snapshot")
        sec = dict(row)
        sec["heading_path"] = json.loads(sec["heading_path"])
        sec["source"] = self._source(sec["source_id"])
        sec["tables"] = [r["table_id"] for r in self.con.execute(
            "SELECT table_id FROM tables WHERE section_id = ? ORDER BY ordinal", (section_id,))]
        nav = self.con.execute(
            "SELECT section_id, ordinal FROM sections WHERE source_id = ? AND ordinal IN (?, ?)",
            (sec["source_id"], sec["ordinal"] - 1, sec["ordinal"] + 1)).fetchall()
        sec["previous_section_id"] = next((r[0] for r in nav if r[1] < sec["ordinal"]), None)
        sec["next_section_id"] = next((r[0] for r in nav if r[1] > sec["ordinal"]), None)
        return sec

    def read_table(self, table_id: str) -> dict[str, Any]:
        row = self.con.execute("SELECT * FROM tables WHERE table_id = ?", (table_id,)).fetchone()
        if row is None:
            raise EvidenceAccessError(f"{table_id!r} is not available in this snapshot")
        t = dict(row)
        t["rows"] = json.loads(t["rows"])
        sec = self.con.execute("SELECT heading_path, page FROM sections WHERE section_id = ?",
                               (t["section_id"],)).fetchone()
        t["heading_path"], t["page"] = json.loads(sec["heading_path"]), sec["page"]
        t["source"] = self._source(t["source_id"])
        return t

    def read(self, item_id: str) -> dict[str, Any]:
        return self.read_table(item_id) if "#t" in item_id else self.read_section(item_id)
