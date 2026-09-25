"""Investigation graph: the typed record of how evidence became findings and economic effects.

One graph serves the packet, the independent reviewer, the viewer and the evaluator:

    DecisionDependency -> EvidenceCandidate (+ candidate_screen) -> AtomicFinding (verbatim SourceSpans
    + finding_check observations) -> ReconciliationTask -> EconomicEffectProposal -> finance engine

Authority is asymmetric. A SemanticObservation is Jev's narrow judgment; the agent accepts, reconciles
or overrides it with a reason; the host validates structure; the finance engine validates the effect.
A Jev answer never sets an amount, a date or a decision and never creates a cash stream by itself.
Runtime objects cite snapshot spans and finding IDs, never the builder's facts registry.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.values import EvidenceValue

Disposition = Literal["used_in_finding", "caused_more_context_read", "caused_research_redirect", "flagged_conflict",
                      "challenged_agent_draft", "overridden_by_agent_with_reason", "unused"]
SubjectKind = Literal["obligation", "cash_pool", "activity", "offset", "other"]
ScreenRoute = Literal["direct_evidence", "conflict", "unscreened", "context_only", "instruction_flagged"]
Mechanism = Literal[
    "settlement_payment_timing",  # existing liability assigned to dated outflows
    "noncash_normalization",  # historical earnings adjustment; never a cash stream
    "receipt_delay",
    "operating_interruption",
    "restricted_funds",
    "expense_funding",
    "funding_constraint",
    "resolved_obligation",  # removes an obsolete prospective charge
]
CASH_FREE_MECHANISMS = {"noncash_normalization", "resolved_obligation"}


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class SourceSpan(Frozen):
    """An exact, verified quotation from the admissible snapshot."""

    source_id: str
    section_id: str  # spans resolve against section text (tables appear inline in their section)
    item_id: str  # the section or table the agent cited
    start: int
    end: int
    quote: str


class DecisionDependency(Frozen):
    dependency_id: str
    question: str  # the unanswered question, in plain words
    premises: tuple[str, ...] = ()  # assumptions embedded in the question (screened for conflict)
    affects: str  # the financial quantity or action that could change
    resolvable_by: str = ""  # evidence that could resolve it
    target: str = ""  # entity plus the specific obligation, counterparty, asset or activity (named precisely)


class CandidateScreen(Frozen):
    route: ScreenRoute  # "unscreened": screening failed or a signal is missing; the candidate is still shown
    observation_ids: tuple[str, ...] = ()
    signals: dict[str, float] = Field(default_factory=dict)  # Noul values by question id (not probabilities of truth)
    error: str = ""


class EvidenceCandidate(Frozen):
    candidate_id: str
    dependency_id: str
    search_id: str
    query: str
    rank: int
    item_id: str
    kind: Literal["section", "table"]
    source_id: str
    heading_path: tuple[str, ...]
    snippet: str
    screen: CandidateScreen | None = None  # absent in the agent-only arm


class JevCallRecord(Frozen):
    """One physical provider request (the raw response is stored once, here)."""

    call_id: str
    run_id: str
    profile: str
    subject_ids: tuple[str, ...]  # graph objects the state was built from
    question_ids: tuple[str, ...]
    registry_version: str
    requested_model: str
    returned_model: str | None
    state_sha256: str
    source_content_hashes: tuple[str, ...]
    attempts_used: int  # physical HTTP attempts observed for this request (0 on a cache hit)
    usage: dict[str, Any] | None
    raw_response: dict[str, Any]
    created_at: str
    cache_hit: bool = False


class SemanticObservation(Frozen):
    observation_id: str
    call_id: str
    profile: str
    question_id: str
    question_version: str
    primitive: Literal["choice", "noul"]
    answer: str | bool | None
    probabilities: dict[str, float] | None = None  # Choice distribution; a judgment, never an event probability
    noul_value: float | None = None  # Noul score in [0, 1]
    confidence: float | None = None
    subject_ids: tuple[str, ...] = ()
    downstream_disposition: Disposition = "unused"
    disposition_note: str = ""


class AtomicFinding(Frozen):
    finding_id: str
    dependency_id: str
    proposition: str
    target: str
    subject_kind: SubjectKind = "other"
    subject: str = ""
    is_inference: bool = False  # the agent's own linkage of cited premises, labelled as such
    spans: tuple[SourceSpan, ...] = Field(min_length=1)  # every finding rests on verbatim snapshot text
    observation_ids: tuple[str, ...] = ()
    status: Literal["proposed", "accepted", "rejected"] = "proposed"
    resolution_note: str = ""


class ReconciliationTask(Frozen):
    task_id: str
    finding_ids: tuple[str, ...]
    observation_ids: tuple[str, ...] = ()
    status: Literal["open", "resolved"] = "open"
    note: str = ""


class ParameterRequirement(Frozen):
    """A value the effect needs. Unknown stays unknown; a value carries its basis and citations."""

    name: str
    description: str
    status: Literal["known", "range", "unknown", "assumption_required"] = "unknown"
    value: EvidenceValue | None = None  # spec §5: amount or supported range, with provenance
    finding_ids: tuple[str, ...] = ()  # findings the value is taken from
    resolves_via: str = ""


class EconomicEffectProposal(Frozen):
    """Bridge from accepted findings to one target financial stream (aligned with the kit Effect schema)."""

    effect_id: str
    finding_ids: tuple[str, ...]
    mechanism: Mechanism
    target: str  # obligation, stream or activity the effect applies to
    cash_direction: Literal["inflow", "outflow", "none", "unknown"]
    baseline_treatment: Literal["already_in_baseline_reclassify_timing", "new_to_baseline",
                                "remove_from_baseline", "normalization_only"]
    parameters: tuple[ParameterRequirement, ...] = ()
    linked_effect_ids: tuple[str, ...] = ()
    double_count_guard: str = ""
    model_consequence: str = ""  # plain-language "so what?" for the reviewer
    status: Literal["proposed", "validated", "rejected"] = "proposed"
    validation_messages: tuple[str, ...] = ()


EventKind = Literal[
    "run_started", "dependency_recorded", "search", "candidate_screened", "evidence_read", "jev_call",
    "observation_recorded", "observation_disposition", "finding_proposed", "finding_resolved",
    "reconciliation_opened", "reconciliation_resolved", "effect_proposed", "effect_validated",
    "sensitivity_run", "missing_fact_requested", "packet_submitted", "run_failed",
]


class InvestigationEvent(Frozen):
    seq: int
    at: str
    kind: EventKind
    object_ids: tuple[str, ...] = ()
    payload: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str
    hash: str


def validate_effect(effect: EconomicEffectProposal, findings: dict[str, AtomicFinding],
                    verify_span: Callable[[SourceSpan], None] | None = None) -> list[str]:
    """Host structural validation of an effect proposal. Returns problems (empty when valid).

    Semantic observations are not consulted here: only agent-accepted findings resting on verbatim
    snapshot spans can support an effect, and cash-free mechanisms can never carry a cash direction.
    `verify_span` (app.evidence.spans.verify bound to the run's snapshot) re-checks each cited span.
    """
    problems = []
    if not effect.finding_ids:
        problems.append("An effect needs at least one accepted finding")
    for fid in effect.finding_ids:
        f = findings.get(fid)
        if f is None:
            problems.append(f"Unknown finding {fid}")
        elif f.status != "accepted":
            problems.append(f"Finding {fid} is {f.status}, not accepted")
        elif not f.spans:
            problems.append(f"Finding {fid} cites no source span")
        elif verify_span is not None:
            for span in f.spans:
                try:
                    verify_span(span)
                except Exception as e:  # missing section, moved text or unreadable source are all problems
                    problems.append(f"Finding {fid}: span {span.section_id} could not be verified ({e})")
    for p in effect.parameters:
        if p.status in ("known", "range") and (p.value is None or p.value.status == "unknown"):
            problems.append(f"Parameter {p.name} is {p.status} but carries no value")
        stray = set(p.finding_ids) - set(effect.finding_ids)
        if stray:
            problems.append(f"Parameter {p.name} cites findings outside this effect: {sorted(stray)}")
    if effect.mechanism in CASH_FREE_MECHANISMS and effect.cash_direction != "none":
        problems.append(f"{effect.mechanism} cannot create a cash {effect.cash_direction}")
    if effect.mechanism == "noncash_normalization" and effect.baseline_treatment != "normalization_only":
        problems.append("A noncash normalization only adjusts historical metrics")
    if effect.mechanism == "settlement_payment_timing" and effect.baseline_treatment == "new_to_baseline" \
            and not effect.double_count_guard:
        problems.append("A settlement already on the balance sheet needs a double-count guard before it is new to the baseline")
    return problems
