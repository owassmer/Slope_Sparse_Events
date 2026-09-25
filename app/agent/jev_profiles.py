"""Host-owned judgment profiles: which questions to ask, built from which minimal state.

The agent names a profile and object IDs; this module resolves those objects from the run and the
admissible snapshot, builds the smallest state that answers the question, asks Jev, and records the
call and its observations in the run's investigation graph. The agent never supplies raw state or
question IDs, and nothing outside the snapshot and the run can enter a Jev state.

Profiles (registry v3): candidate_screen (host, every search result), claim_interpretation (agent),
finding_check (host, every proposed finding), statement_relation (agent; host for baseline overlap).
"""

from __future__ import annotations

import asyncio
import re

from app.agent.jev import JevAdapter
from app.agent.run_store import RunStore
from app.config import question_registry
from app.domain.investigation import (
    AtomicFinding,
    CandidateScreen,
    DecisionDependency,
    EvidenceCandidate,
    SemanticObservation,
)
from app.evidence.store import EvidenceStore

# Code-owned routing thresholds on Noul values (checked against evals/jev_semantic_cases.json).
RELEVANT, USABLE, CONFLICT = 0.5, 0.5, 0.5
PASSAGE_CHARS = 12_000
STATUS_QUESTION = {"obligation": "obligation_status", "cash_pool": "cash_access", "activity": "activity_status",
                   "offset": "offset_status"}


def profile_questions(profile: str) -> list[str]:
    return list(question_registry()["profiles"][profile]["questions"])


def excerpt(text: str, anchor: str | None = None, limit: int = PASSAGE_CHARS) -> str:
    """Keep a long section within the state budget, centred on the anchor text when one is given."""
    if len(text) <= limit:
        return text
    at = text.find(anchor) if anchor else -1
    start = max(0, min(len(text) - limit, (at if at >= 0 else 0) - limit // 3))
    return ("… " if start else "") + text[start:start + limit] + (" …" if start + limit < len(text) else "")


def route(signals: dict[str, float]) -> str:
    relevant = signals.get("gap_relevance", 0) >= RELEVANT
    if relevant and signals.get("premise_conflict", 0) >= CONFLICT:
        return "conflict"
    if relevant and signals.get("usable_evidence", 0) >= USABLE:
        return "direct_evidence"
    return "context_only"


class Semantics:
    """Binds one run's store, its snapshot and a Jev adapter."""

    def __init__(self, run: RunStore, evidence: EvidenceStore, jev: JevAdapter) -> None:
        self.run, self.evidence, self.jev = run, evidence, jev

    # --- state builders (admissible objects only) ---------------------------------------------

    def _source(self, source_id: str) -> dict:
        s = self.evidence.source(source_id)
        return {"source_id": s["source_id"], "title": s["title"], "publisher": s["publisher"],
                "document_kind": s["document_kind"], "available_at": s["available_at"]}

    def _passage(self, item_id: str, anchor: str | None = None) -> tuple[dict, str]:
        item = self.evidence.read(item_id)
        if "#t" in item_id:
            text = "\n".join(filter(None, [item["context_before"], item["rendered"], item["context_after"]]))
        else:
            text = excerpt(item["text"], anchor)
        passage = {"heading_path": " > ".join(item["heading_path"]), "text": text}
        return passage, item["source"]["sha256"]

    async def _ask(self, profile: str, question_ids: list[str], state: dict, subject_ids: tuple[str, ...],
                   hashes: tuple[str, ...]) -> list[SemanticObservation]:
        call, observations = await self.jev.judge(profile=profile, question_ids=question_ids, state=state,
                                                  subject_ids=subject_ids, source_content_hashes=hashes)
        self.run.put("jev_call", call)
        for o in observations:
            self.run.put("observation_recorded", o)
        return observations

    # --- profiles ------------------------------------------------------------------------------

    async def screen(self, dependency: DecisionDependency, candidates: list[EvidenceCandidate]) -> list[EvidenceCandidate]:
        """candidate_screen: one request per query-passage pair, fanned out in parallel.

        Every candidate is returned, labelled and ordered; none is withheld from the agent.
        """
        unanswered = {"question": dependency.question, "premises": list(dependency.premises),
                      "could_change": dependency.affects}

        async def one(c: EvidenceCandidate) -> EvidenceCandidate:
            hit = re.search(r"\[([^\]]+)\]", c.snippet)  # FTS highlights the first matched term
            passage, sha = self._passage(c.item_id, anchor=hit.group(1) if hit else None)
            obs = await self._ask("candidate_screen", profile_questions("candidate_screen"),
                                  {"unanswered_question": unanswered, "passage": passage},
                                  (dependency.dependency_id, c.candidate_id, c.item_id), (sha,))
            signals = {o.question_id: o.noul_value for o in obs if o.noul_value is not None}
            screened = c.model_copy(update={"screen": CandidateScreen(
                route=route(signals), observation_ids=tuple(o.observation_id for o in obs), signals=signals)})
            self.run.put("candidate_screened", screened)
            return screened

        screened = await asyncio.gather(*(one(c) for c in candidates))
        order = {"direct_evidence": 0, "conflict": 1, "context_only": 2}
        return sorted(screened, key=lambda c: (order[c.screen.route], c.rank))

    async def interpret(self, *, item_id: str, claim: str, target: str, subject_kind: str = "other",
                        subject: str = "") -> list[SemanticObservation]:
        """claim_interpretation: entity scope and posture, plus the status question for the claim's subject."""
        passage, sha = self._passage(item_id, anchor=claim[:40])
        questions = ["entity_scope", "claim_posture"]
        state = {"target": target, "source": self._source(self.evidence.read(item_id)["source"]["source_id"]),
                 "passage": passage, "claim": claim}
        if subject_kind in STATUS_QUESTION:
            questions.append(STATUS_QUESTION[subject_kind])
            state["subject"] = subject
        return await self._ask("claim_interpretation", questions, state, (item_id,), (sha,))

    async def check_finding(self, finding: AtomicFinding) -> list[SemanticObservation]:
        """finding_check on the finding's cited spans (the passages it claims to rest on)."""
        passages, hashes = [], []
        for span in finding.spans:
            p, sha = self._passage(span.item_id, anchor=span.quote)
            passages.append({**p, "cited_quote": span.quote})
            hashes.append(sha)
        source = self._source(finding.spans[0].source_id)
        proposed = finding.proposition + (" (the agent presents this as an inference from the cited passages)"
                                          if finding.is_inference else "")
        state = {"target": finding.target, "source": source,
                 "passage": passages[0] if len(passages) == 1 else passages, "proposed_finding": proposed}
        return await self._ask("finding_check", profile_questions("finding_check"), state,
                               (finding.finding_id,), tuple(dict.fromkeys(hashes)))

    async def relate(self, a: AtomicFinding, b: AtomicFinding, proposed_fact: str) -> list[SemanticObservation]:
        """statement_relation between two findings with respect to one proposed fact."""
        def statement(f: AtomicFinding) -> dict:
            return {"statement": f.proposition, "source": self._source(f.spans[0].source_id),
                    "quotes": [s.quote for s in f.spans]}
        state = {"proposed_fact": proposed_fact, "statement_a": statement(a), "statement_b": statement(b)}
        hashes = tuple(dict.fromkeys(self.evidence.source(f.spans[0].source_id)["sha256"] for f in (a, b)))
        return await self._ask("statement_relation", ["statement_relation"], state,
                               (a.finding_id, b.finding_id), hashes)

    async def baseline_overlap(self, effect_description: str, baseline_item: str,
                               subject_ids: tuple[str, ...]) -> list[SemanticObservation]:
        """Warning signal only; obligation IDs and host validation decide double counting."""
        return await self._ask("statement_relation", ["baseline_overlap"],
                               {"effect_description": effect_description, "baseline_item": baseline_item},
                               subject_ids, ())
