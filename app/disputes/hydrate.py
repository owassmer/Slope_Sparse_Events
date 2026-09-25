"""Hydrate a finding into Jev evidence: the verbatim quotes plus the section text around them, the source and its date.

Atomic means one judgment target, not minimal context: a waiver needs its scope, an intention needs its qualification.
Only admissible snapshot text enters; the agent's proposition never does.
"""

from __future__ import annotations

from app.domain.investigation import AtomicFinding
from app.evidence.store import EvidenceStore

CONTEXT_CHARS = 900  # on each side of the cited span


def finding_date(f: AtomicFinding, sources: dict[str, tuple[str, str]]) -> str:
    return max(sources.get(s.source_id, ("", ""))[1] for s in f.spans)


def passage(evidence: EvidenceStore, f: AtomicFinding, sources: dict[str, tuple[str, str]],
            context_chars: int = CONTEXT_CHARS) -> dict:
    """The judged passage: quotes, the surrounding section text, source title, heading and date."""
    span = f.spans[0]
    section = evidence.read_section(span.section_id)
    text = section["text"]
    lo, hi = max(0, span.start - context_chars), min(len(text), span.end + context_chars)
    context = ("… " if lo else "") + text[lo:hi] + (" …" if hi < len(text) else "")
    return {"source": sources.get(span.source_id, (span.source_id, ""))[0], "date": finding_date(f, sources),
            "heading": " > ".join(section["heading_path"][-3:]), "quotes": [s.quote for s in f.spans],
            "context": " ".join(context.split())}


def related(f: AtomicFinding, sources: dict[str, tuple[str, str]]) -> dict:
    """A related passage, quotes only (no context), for understanding the judged one."""
    return {"source": sources.get(f.spans[0].source_id, (f.spans[0].source_id, ""))[0],
            "date": finding_date(f, sources), "quotes": [s.quote for s in f.spans]}


def evidence_state(evidence: EvidenceStore, f: AtomicFinding, others: list[AtomicFinding],
                   sources: dict[str, tuple[str, str]]) -> dict:
    return {"passage": passage(evidence, f, sources),
            "related_passages": [related(o, sources) for o in others if o.finding_id != f.finding_id]}
