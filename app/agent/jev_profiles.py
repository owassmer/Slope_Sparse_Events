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

from app.agent.jev import NOUL_THRESHOLD, JevAdapter, JevBudgetExceeded
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
RELEVANT = USABLE = CONFLICT = INSTRUCTION = NOUL_THRESHOLD
# A Choice answer whose top two options are closer than this is ambiguous: read more context or
# reconcile; never accept it as settled. (Judgment ambiguity, not an event probability.)
AMBIGUITY_MARGIN = 0.25
ROUTE_ORDER = {"direct_evidence": 0, "conflict": 1, "unscreened": 2, "context_only": 3, "instruction_flagged": 4}
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
    """Order-only routing. A missing signal is unscreened, never silently context-only."""
    if any(signals.get(q) is None for q in ("gap_relevance", "usable_evidence", "premise_conflict")):
        return "unscreened"
    if signals.get("instruction_like_text", 0) >= INSTRUCTION:
        return "instruction_flagged"  # still shown, ranked last
    relevant = signals["gap_relevance"] >= RELEVANT
    if relevant and signals["premise_conflict"] >= CONFLICT:
        return "conflict"
    if relevant and signals["usable_evidence"] >= USABLE:
        return "direct_evidence"
    return "context_only"


def is_ambiguous(observation: SemanticObservation) -> bool:
    """True when a Choice distribution is too flat to treat as settled (or an answer is missing)."""
    if observation.primitive == "noul":
        return observation.noul_value is None or abs(observation.noul_value - NOUL_THRESHOLD) < AMBIGUITY_MARGIN / 2
    probs = sorted((observation.probabilities or {}).values(), reverse=True)
    return len(probs) < 2 or probs[0] - probs[1] < AMBIGUITY_MARGIN


class Semantics:
    """Binds one run's store, its snapshot and a Jev adapter."""

    def __init__(self, run: RunStore, evidence: EvidenceStore, jev: JevAdapter) -> None:
        self.run, self.evidence, self.jev = run, evidence, jev
        self.last_screen_errors: list[str] = []
        self.budget_exhausted = False

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
            try:
                hit = re.search(r"\[([^\]]+)\]", c.snippet)  # FTS highlights the first matched term
                passage, sha = self._passage(c.item_id, anchor=hit.group(1) if hit else None)
                obs = await self._ask("candidate_screen", profile_questions("candidate_screen"),
                                      {"unanswered_question": unanswered, "passage": passage},
                                      (dependency.dependency_id, c.candidate_id, c.item_id), (sha,))
                signals = {o.question_id: o.noul_value for o in obs if o.noul_value is not None}
                screen = CandidateScreen(route=route(signals), observation_ids=tuple(o.observation_id for o in obs),
                                         signals=signals)
            except Exception as e:  # a failed screen never hides the candidate
                if isinstance(e, JevBudgetExceeded):
                    self.budget_exhausted = True  # the runner turns this into INCOMPLETE_REVIEW
                screen = CandidateScreen(route="unscreened", error=f"{type(e).__name__}: {e}"[:300])
            screened = c.model_copy(update={"screen": screen})
            self.run.put("candidate_screened", screened)
            return screened

        screened = await asyncio.gather(*(one(c) for c in candidates))
        self.last_screen_errors = [c.screen.error for c in screened if c.screen.error]
        return sorted(screened, key=lambda c: (ROUTE_ORDER[c.screen.route], c.rank))

    async def interpret(self, *, item_id: str, claim: str, target: str, subject_kind: str = "other",
                        subject: str = "", anchor_quote: str | None = None) -> list[SemanticObservation]:
        """claim_interpretation: entity scope and posture, plus the status question for the claim's subject.

        `anchor_quote` (verbatim text the claim rests on) keeps a long section's excerpt on the right text.
        """
        passage, sha = self._passage(item_id, anchor=anchor_quote)
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
        passage = passages[0] if len(passages) == 1 else passages
        # One request: the finding-quality questions read state.proposed_finding, and the host-mandated entity check
        # reads state.claim (the same proposition, stated without the inference note), over the same passage.
        state = {"target": finding.target, "source": source, "passage": passage, "proposed_finding": proposed,
                 "claim": finding.proposition}
        return await self._ask("finding_check", [*profile_questions("finding_check"), "entity_scope"], state,
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

    async def support(self, statement: str, cited: list[AtomicFinding], engine_results: list[str],
                      subject_ids: tuple[str, ...], *, with_quotes: bool = True) -> list[SemanticObservation]:
        """statement_support: are the factual claims in an agent-written statement supported by what it cites?"""
        cited_state = [{"finding_id": f.finding_id, "proposition": f.proposition,
                        **({"quotes": [s.quote for s in f.spans]} if with_quotes else {})} for f in cited]
        hashes = tuple(dict.fromkeys(self.evidence.source(s.source_id)["sha256"] for f in cited for s in f.spans))
        return await self._ask("statement_support", ["claims_supported"],
                               {"statement": statement, "cited_findings": cited_state, "engine_results": engine_results},
                               subject_ids, hashes)

    async def coverage(self, passage: dict, cited: list[AtomicFinding], subject_ids: tuple[str, ...],
                       source_hashes: tuple[str, ...]) -> list[SemanticObservation]:
        """inventory_coverage: do the cited findings account for the flagged matter?"""
        cited_state = [{"finding_id": f.finding_id, "proposition": f.proposition} for f in cited]
        return await self._ask("inventory_coverage", ["coverage_supported"],
                               {"passage": passage, "cited_findings": cited_state}, subject_ids, source_hashes)

    async def baseline_overlap(self, effect_description: str, baseline_item: str, subject_ids: tuple[str, ...],
                               source_hashes: tuple[str, ...] = ()) -> list[SemanticObservation]:
        """Warning signal only; obligation IDs and host validation decide double counting."""
        return await self._ask("statement_relation", ["baseline_overlap"],
                               {"effect_description": effect_description, "baseline_item": baseline_item},
                               subject_ids, source_hashes)
