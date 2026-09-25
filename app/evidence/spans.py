"""Verbatim source spans: a finding may only cite text that exists in the admissible snapshot.

The agent supplies an item ID (section or table) and an exact quote; the host locates the quote in the
section text and records the character offsets. Tables resolve against the section that contains them,
where they appear inline. A quote that is absent, or ambiguous without an occurrence index, is rejected.
"""

from __future__ import annotations

from app.domain.investigation import SourceSpan
from app.evidence.store import EvidenceStore


class SpanError(ValueError):
    pass


def _norm(text: str) -> str:
    return " ".join(text.split())


def locate(store: EvidenceStore, item_id: str, quote: str, occurrence: int = 1) -> SourceSpan:
    item = store.read(item_id)  # raises EvidenceAccessError outside the snapshot
    section = item if "#s" in item_id else store.read_section(item["section_id"])
    text = section["text"]
    if len(_norm(quote)) < 12:
        raise SpanError("Quote too short to verify; cite at least a full phrase")
    hits, start = [], text.find(quote)
    while start != -1:
        hits.append(start)
        start = text.find(quote, start + 1)
    if not hits:
        raise SpanError(f"Quote not found verbatim in {item_id}; copy it exactly from the evidence")
    if occurrence > len(hits):  # the first occurrence is used unless the agent names another
        raise SpanError(f"Quote occurs {len(hits)} time(s) in {item_id}")
    s = hits[occurrence - 1]
    return SourceSpan(source_id=section["source_id"], section_id=section["section_id"], item_id=item_id,
                      start=s, end=s + len(quote), quote=quote)


def verify(store: EvidenceStore, span: SourceSpan) -> None:
    text = store.read_section(span.section_id)["text"]
    if text[span.start:span.end] != span.quote:
        raise SpanError(f"Span {span.section_id}[{span.start}:{span.end}] does not match its quote")
