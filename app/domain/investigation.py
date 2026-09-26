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
from datetime import date
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
# Baseline treatments that fit each mechanism (kit Effect schema vocabulary plus the two added here).
TREATMENTS = {
    "settlement_payment_timing": {"already_in_baseline_reclassify_timing", "new_to_baseline"},
    "noncash_normalization": {"normalization_only"},
    "resolved_obligation": {"exclude_already_paid_obligation", "remove_from_baseline"},
    "funding_constraint": {"modify_available_funding"},
    "restricted_funds": {"modify_available_funding", "new_to_baseline"},
    "expense_funding": {"modify_available_funding", "new_to_baseline"},
    "receipt_delay": {"already_in_baseline_reclassify_timing", "new_to_baseline"},
    "operating_interruption": {"new_to_baseline", "already_in_baseline_reclassify_timing"},
}


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
    primitive: Literal["choice", "noul", "score"]
    answer: str | bool | None  # Score: the most supported rubric level, as its index
    probabilities: dict[str, float] | None = None  # Choice/Score distribution; a judgment, never an event probability
    noul_value: float | None = None  # Noul score in [0, 1]
    score_value: float | None = None  # Score: expected rubric level
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


class InventoryItem(Frozen):
    """A reading-list pointer: one atomic evidence unit (a paragraph or a table row) the host sweep flagged as describing a
    specific matter. The agent is not required to account for it; at submission the host records whether an accepted
    finding cites it, and uncited units go to the independent reviewer's checklist. (Runs recorded before the atomic
    redesign hold section-level items with the older accounting statuses.)"""

    item_id: str
    section_ids: tuple[str, ...]
    source_id: str
    heading_path: tuple[str, ...]
    kind: str  # matter_kind answer
    signal: float  # matter_inventory Noul value (a judgment, not a probability of anything)
    excerpt: str = ""
    section_excerpts: tuple[str, ...] = ()  # the flagged chunk start for each section, in section_ids order
    # disputed: a host check failed and the agent escalated it; it stays open for the human reviewer
    status: Literal["open", "covered", "not_decision_relevant", "disputed", "cited", "uncited"] = "open"
    finding_ids: tuple[str, ...] = ()
    duplicate_of: str = ""  # covered as a duplicate of this (covered) item
    unit_kind: str = ""  # paragraph | table_row (atomic items)
    unit_start: int = -1  # character offsets of the unit in its section text
    unit_end: int = -1
    observation_ids: tuple[str, ...] = ()  # the host checks that decided the status
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
    baseline_treatment: Literal["already_in_baseline_reclassify_timing", "new_to_baseline", "remove_from_baseline",
                                "normalization_only", "exclude_already_paid_obligation", "modify_available_funding"]
    parameters: tuple[ParameterRequirement, ...] = ()
    linked_effect_ids: tuple[str, ...] = ()
    double_count_guard: str = ""
    model_consequence: str = ""  # plain-language "so what?" for the reviewer
    observation_ids: tuple[str, ...] = ()  # host guard checks (posture/status per finding, statement support)
    override_reasons: dict[str, str] = Field(default_factory=dict)  # guard -> agent's reply to a failed check
    # disputed: rejected by the category guard and escalated by the agent; never a cash stream, open for the reviewer
    status: Literal["proposed", "validated", "rejected", "disputed"] = "proposed"
    dispute: str = ""
    validation_messages: tuple[str, ...] = ()


class Decisive(Frozen):
    """The passage a reading rests on: which finding, its source date and the verbatim quote."""

    finding_id: str
    source_date: str
    quote: str


class FindingReading(Frozen):
    """Jev's present-state reading of one cited finding, judged against one obligation with the passage's surrounding
    evidence (never a forecast). Full distributions are kept; code derives structural facts from them."""

    finding_id: str
    source_date: str
    payer: dict[str, float] = Field(default_factory=dict)  # borrower | counterparty | not_stated -> probability
    amount_status: dict[str, float] = Field(default_factory=dict)  # sought | estimated | fixed | paid | not_stated
    includes_interest: float | None = None  # Noul value
    events: dict[str, float | None] = Field(default_factory=dict)  # procedural event -> Noul value
    bears_on: dict[str, float | None] = Field(default_factory=dict)  # factor -> Noul value (presence, for a present factor)
    levels: dict[str, dict[str, float]] = Field(default_factory=dict)  # graded factor -> rubric level -> probability
    observation_ids: tuple[str, ...] = ()


class FactorResult(Frozen):
    """One factor, aggregated from the passages routed to it by the factor's own rule, with its full distribution.
    Unknown stays unknown; passages of the same date that disagree are kept as a conflict."""

    factor_id: str
    label: str
    kind: Literal["graded", "present"]
    aggregate: str
    distribution: dict[str, float] = Field(default_factory=dict)  # rubric level label -> probability (graded)
    probability: float | None = None  # present-or-absent factors: the strongest routed Noul value
    level_label: str = "unknown"  # the most probable level, for display
    conflict: bool = False
    decisive: Decisive | None = None
    finding_ids: tuple[str, ...] = ()


class EvidenceRequest(Frozen):
    """What to obtain, and what follows either way."""

    factor_id: str
    action: str
    if_satisfied: str
    if_not: str


ComponentKind = Literal["compensatory", "exemplary", "patent", "trebling", "fees", "prejudgment_interest", "costs"]
MotionKind = Literal["rule_50b", "rule_52b", "rule_59a", "rule_59e", "rule_54_fees", "injunction"]


class Component(Frozen):
    """One money component of a judgment (dispute model 4.0.0 `components_schema`): a quoted amount (awarded or
    requested), a statutory computation code performs from quoted law, or a typed unknown. Code checks every figure
    against the cited quotes."""

    component_id: str
    label: str
    kind: ComponentKind
    status: Literal["awarded", "requested"]
    amount_cents: int | None = None
    statutory: str = ""  # a rule id in the dispute model's `rules`
    unknown: bool = False
    remittitur_cents: int | None = None  # the most the evidence supports (a declared scenario), where the record gives one
    motion: str = ""  # motion_id of the pending motion that decides it
    basis: str = ""  # finding id(s) and the quote the figure rests on


class PendingMotion(Frozen):
    """A pending post-judgment motion: its kind and the close of its briefing (quoted). Code draws its ruling date
    from the close of briefing (dispute model `ruling_lag_days`) and applies FRAP 4(a)(4)(A) tolling by kind."""

    motion_id: str  # the docket reference, e.g. "D.I. 613"
    kind: MotionKind
    briefing_close: date
    decides: tuple[str, ...] = ()  # component ids (or "liability", "injunction")


class FinancingInstrument(Frozen):
    """A financing instrument whose terms the dispute can trigger (spec §7). The agent quotes the terms each chain
    cites; every figure and date is checked against the quotes. Code applies the terms; Jev never decides what a
    contract means."""

    instrument_id: str
    dependency_id: str
    kind: Literal["convertible_notes"]
    title: str
    issuer: str
    finding_ids: tuple[str, ...] = Field(min_length=1)
    principal_cents: int
    coupon_cents: int | None = None  # one interest payment
    interest_dates: tuple[date, ...] = ()
    judgment_default_threshold_cents: int | None = None
    judgment_default_days: int | None = None
    judgment_default_notice: bool = True  # only after notice by the trustee or the holders
    insured_cents: int = 0  # amounts covered by insurance, excluded from the judgment default
    listing_deadline: date | None = None  # the listing-compliance deadline (bid price)
    repurchase_notice_business_days: int | None = None
    repurchase_business_days: tuple[int, int] | None = None  # repurchase this many business days after notice
    dispute_ids: tuple[str, ...] = ()  # the disputes whose judgments the default terms reach
    status: Literal["instantiated", "superseded"] = "instantiated"
    superseded_by: str = ""


class DisputeInstance(Frozen):
    """A live dispute grouped by the agent and read by Jev. The agent supplies the findings, a docket reference, the
    obligation's nature, the counterparty, the quoted amount and any judgment date; Jev reads each finding with its
    surrounding evidence (who pays, the amount's status, procedural events, factors); code places the stage.
    Forecasts and cash paths are built by the analysis."""

    instance_id: str
    dependency_id: str
    model_id: str
    model_version: str
    title: str
    order_reference: str
    nature: str
    counterparty: str
    finding_ids: tuple[str, ...] = Field(min_length=1)
    amount: EvidenceValue
    judgment_date: date | None = None
    forum: Literal["court", "arbitration"] = "court"
    commenced: date | None = None  # the action's commencement (N.C. Gen. Stat. §24-5(b) interest start)
    components: tuple[Component, ...] = ()
    motions: tuple[PendingMotion, ...] = ()
    financing: tuple[FinancingInstrument, ...] = ()  # instruments whose terms this judgment triggers (host-attached)
    borrower_role: Literal["debtor", "creditor"] | None = None  # from Jev's readings (the agent's, in the agent-only arm)
    amount_status: str = "unknown"
    amount_includes_interest: bool = False
    stage: str | None = None
    established: dict[str, Decisive] = Field(default_factory=dict)  # procedural event -> the passage establishing it
    readings: tuple[FindingReading, ...] = ()
    factors: tuple[FactorResult, ...] = ()
    evidence_requests: tuple[EvidenceRequest, ...] = ()
    proposed_extension: str = ""  # flagged; never used by the host
    status: Literal["interpreted", "outside_model", "not_judged", "superseded", "resolved"] = "interpreted"
    superseded_by: str = ""
    observation_ids: tuple[str, ...] = ()


EventKind = Literal[
    "run_started", "dependency_recorded", "search", "candidate_screened", "evidence_read", "jev_call",
    "observation_recorded", "observation_disposition", "finding_proposed", "finding_resolved",
    "reconciliation_opened", "reconciliation_resolved", "effect_proposed", "effect_validated",
    "sensitivity_run", "missing_fact_requested", "packet_submitted", "run_failed",
    "inventory_loaded", "inventory_accounted", "conclusion_checked", "effect_disputed", "cited_units_checked",
    "dispute_instantiated", "financing_instantiated",
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
    if effect.baseline_treatment not in TREATMENTS.get(effect.mechanism, set()):
        problems.append(f"Baseline treatment {effect.baseline_treatment} does not fit {effect.mechanism}; use one of "
                        f"{sorted(TREATMENTS.get(effect.mechanism, set()))}")
    if effect.mechanism in CASH_FREE_MECHANISMS and effect.cash_direction != "none":
        problems.append(f"{effect.mechanism} cannot create a cash {effect.cash_direction}")
    if effect.mechanism == "noncash_normalization" and effect.baseline_treatment != "normalization_only":
        problems.append("A noncash normalization only adjusts historical metrics")
    if effect.mechanism == "settlement_payment_timing" and effect.baseline_treatment == "new_to_baseline" \
            and not effect.double_count_guard:
        problems.append("A settlement already on the balance sheet needs a double-count guard before it is new to the baseline")
    return problems
