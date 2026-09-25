"""Scoped MCP tools for one investigation run (spec §7; agent_config tools section).

Every handler is bound to one run: its event store, its admissible snapshot, its locked inputs and (in
the agent-plus-Jev arm) its semantic layer. Arguments are object IDs, queries and agent-authored
propositions; no tool accepts a path, database, snapshot, cutoff, raw Jev state or question ID.

Host rules enforced here:
- search results are always all returned; in the Jev arm each carries its screen label;
- a finding cites verbatim quotes; in the Jev arm every finding gets finding_check before resolution;
- accepting a finding requires a disposition for each linked observation, a reasoned override for
  any unsettled answer, and never accepts an ambiguous entity_scope answer;
- effects are validated against accepted findings and re-verified spans; Jev never activates one;
- sensitivities are deterministic finance-engine calls over explicit inputs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from app.agent.jev import registry_question
from app.agent.jev_profiles import Semantics, is_ambiguous
from app.agent.meanings import NOT_SETTLED, meaning
from app.agent.mission import project_mission
from app.agent.run_store import RunStore
from app.config import ConfigurationError
from app.domain.investigation import (
    AtomicFinding,
    DecisionDependency,
    Disposition,
    EconomicEffectProposal,
    EvidenceCandidate,
    ParameterRequirement,
    ReconciliationTask,
    SemanticObservation,
    validate_effect,
)
from app.domain.values import Basis, EvidenceValue, Provenance, Status, Unit, assumed, documented, unknown
from app.evidence.spans import SpanError, locate, verify
from app.evidence.store import EvidenceAccessError, EvidenceStore
from app.finance.fixed_installment import FixedInstallmentOffer
from app.finance.thresholds import remaining_period_required_net_cash

MAX_SEARCH_RESULTS = 12
DEFAULT_SEARCH_RESULTS = 8
RELATION_CHECKS_PER_ACCEPTANCE = 2
# Category-error guard (code-owned): the semantic status a finding must have to support each mechanism.
CATEGORY_RULES: dict[str, tuple[str, str, set[str], set[str]]] = {
    # mechanism: (subject_kind, status question, allowed statuses, allowed postures)
    "settlement_payment_timing": ("obligation", "obligation_status", {"required"},
                                  {"agreed_contractually", "imposed_by_court_or_authority", "reported_completed"}),
    "resolved_obligation": ("obligation", "obligation_status", {"reported_satisfied"}, {"reported_completed"}),
    "restricted_funds": ("cash_pool", "cash_access", {"access_prohibited", "access_limited"},
                         {"agreed_contractually", "imposed_by_court_or_authority", "reported_completed"}),
    "operating_interruption": ("activity", "activity_status", {"unavailable", "limited"},
                               {"reported_completed", "imposed_by_court_or_authority", "agreed_contractually"}),
    "expense_funding": ("offset", "offset_status", {"committed", "committed_subject_to_condition", "received_or_paid_for_borrower"},
                        {"agreed_contractually", "reported_completed"}),
}
POSTURE_ONLY = {"funding_constraint", "receipt_delay"}  # posture must not be an allegation, plan or unknown
MAX_TEXT = 20_000
OVERRIDE_NOTE_MIN = 20


class ToolError(Exception):
    """A rejected call; the message goes back to the agent."""


@dataclass
class RunContext:
    run: RunStore
    evidence: EvidenceStore
    inputs: dict[str, Any]
    arm: str  # "agent_plus_jev" | "agent_only"
    semantics: Semantics | None = None
    incomplete_reasons: list[str] = field(default_factory=list)
    configuration_failure: str | None = None
    submitted: bool = False
    turns_used: int = 0
    max_turns: int | None = None

    @property
    def case_id(self) -> str:
        return self.inputs["case_id"]


def _obs_view(o: SemanticObservation) -> dict[str, Any]:
    view = {"observation_id": o.observation_id, "question": registry_question(o.question_id)["question"],
            "question_id": o.question_id, "answer": o.answer, "meaning": meaning(o.question_id, o.answer),
            "ambiguous": is_ambiguous(o)}
    if o.probabilities:
        view["judgment_distribution"] = {k: round(v, 2) for k, v in o.probabilities.items()}
    if o.noul_value is not None:
        view["judgment_value"] = round(o.noul_value, 2)
    return view


def _unsettled(o: SemanticObservation) -> bool:
    return is_ambiguous(o) or o.answer in NOT_SETTLED.get(o.question_id, set())


def _record_jev_failure(ctx: RunContext, e: Exception) -> None:
    """Any semantic-layer failure makes the run incomplete; an unexpected Jev model is a configuration failure."""
    if isinstance(e, ConfigurationError):
        ctx.configuration_failure = str(e)
    reason = f"Jev unavailable ({type(e).__name__}): {e}"[:300]
    if reason not in ctx.incomplete_reasons:
        ctx.incomplete_reasons.append(reason)


def _cents_in_text(cents: int, text: str) -> bool:
    """Whether an amount appears in cited text, as written in filings ($2,000,000 / 2,000,000 / $2.0 million)."""
    flat = " ".join(text.replace("$ ", "$").split())
    dollars, rem = divmod(abs(cents), 100)
    forms = {f"{dollars:,}.{rem:02d}"} if rem else {f"{dollars:,}", f"{dollars:,}.00"}
    if rem == 0 and dollars >= 1_000_000:
        forms |= {f"{dollars / 1_000_000:.1f} million", f"{dollars / 1_000_000:g} million"}
    # A digit, comma or decimal point may not touch the match: "600,000" must not match "$2,600,000".
    return any(re.search(rf"(?<![\d,.]){re.escape(f)}(?![\d,]|\.\d)", flat) for f in forms)


def _baseline_item(ctx: RunContext, key: str) -> dict | None:
    """The baseline observation a case names for a role (opening cash, aggregate debt); no case literals in code."""
    wanted = ctx.inputs["baseline_profile"].get(key)
    return next((o for o in ctx.inputs["baseline_profile"]["observations"] if o["observation_id"] == wanted), None)


# --- handlers (plain async functions; testable without the SDK) ------------------------------------

async def get_mission(ctx: RunContext, _args: dict) -> dict:
    mission = project_mission(ctx.case_id, ctx.inputs["run_inputs"])
    sources = [{"source_id": s["source_id"], "title": s["title"], "document_kind": s["document_kind"],
                "publicly_available_at": s["available_at"], "access": s["access"]} for s in ctx.evidence.list_sources()]
    return {"mission": mission, "snapshot_cutoff": ctx.evidence.snapshot_info()["cutoff"],
            "admissible_sources": sources, "evaluation_arm_note": "Tools and evidence are scoped to this review date."}


async def read_baseline_profile(ctx: RunContext, _args: dict) -> dict:
    return {"baseline_profile": ctx.inputs["baseline_profile"], "policy": ctx.inputs["policy"]}


async def read_loan_terms(ctx: RunContext, _args: dict) -> dict:
    offers = []
    for o in ctx.inputs["permitted_offers"]["offers"]:
        offer = FixedInstallmentOffer(proposal_id=o["proposal_id"], advance_cents=o["advance_cents"],
                                      term_months=o["term_months"], fee_fraction=Decimal(o["fixed_total_fee_fraction"]))
        offers.append({**o, "total_repayment_cents": offer.total_repayment_cents,
                       "payments_by_month_index_cents": [p.amount_cents for p in offer.schedule()],
                       "largest_single_payment_cents": offer.minimum_residual_capacity_cents()})
    return {"existing_loans": ctx.inputs["baseline_profile"]["existing_loans"],
            "requested_proposal_id": ctx.inputs["permitted_offers"]["requested_proposal_id"], "permitted_offers": offers,
            "note": "Offers are analytical fixtures with month indices; no funding or payment calendar dates exist yet."}


async def record_dependency(ctx: RunContext, args: dict) -> dict:
    target = args.get("target", "").strip()
    if len(target) < 12:
        raise ToolError("Name the target precisely: the entity plus the specific obligation, counterparty, asset or activity")
    dep = DecisionDependency(dependency_id=ctx.run.new_id("dep"), question=args["question"], target=target,
                             premises=tuple(args.get("premises", [])), affects=args["affects"],
                             resolvable_by=args.get("resolvable_by", ""))
    ctx.run.put("dependency_recorded", dep)
    return {"dependency_id": dep.dependency_id}


async def search_evidence(ctx: RunContext, args: dict) -> dict:
    dep = ctx.run.get("dependencies", args["dependency_id"])
    limit = max(1, min(int(args.get("limit", DEFAULT_SEARCH_RESULTS)), MAX_SEARCH_RESULTS))
    try:
        hits = ctx.evidence.search(args["query"], source_ids=args.get("source_ids") or None, limit=limit)
    except EvidenceAccessError as e:
        raise ToolError(str(e)) from e
    search_id = ctx.run.new_id("search")
    cands = [EvidenceCandidate(candidate_id=ctx.run.new_id("cand"), dependency_id=dep.dependency_id, search_id=search_id,
                               query=args["query"], rank=i, item_id=h["id"], kind=h["kind"], source_id=h["source_id"],
                               heading_path=tuple(h["heading_path"]), snippet=h["snippet"]) for i, h in enumerate(hits)]
    ctx.run.append("search", object_ids=(search_id, dep.dependency_id),
                   payload={"query": args["query"], "source_ids": args.get("source_ids"), "candidate_ids": [c.candidate_id for c in cands]})
    if ctx.semantics is not None and cands:
        cands = await ctx.semantics.screen(dep, cands)
        for err in ctx.semantics.last_screen_errors:
            reason = f"Jev screening failed for a candidate: {err}"[:300]
            if reason not in ctx.incomplete_reasons:
                ctx.incomplete_reasons.append(reason)
            if "ConfigurationError" in err:
                ctx.configuration_failure = err
    else:
        for c in cands:
            ctx.run.put("candidate_screened", c)
    avail = {s["source_id"]: s["available_at"] for s in ctx.evidence.list_sources()}
    out = []
    for c in cands:
        row = {"candidate_id": c.candidate_id, "item_id": c.item_id, "kind": c.kind, "source_id": c.source_id,
               "source_available_at": avail.get(c.source_id), "heading_path": list(c.heading_path), "snippet": c.snippet}
        if c.screen is not None:
            row["screen"] = {"route": c.screen.route, **({"error": c.screen.error} if c.screen.error else {}),
                             "signals": {k: round(v, 2) for k, v in c.screen.signals.items()}}
        out.append(row)
    result = {"search_id": search_id, "results": out}
    if ctx.semantics is not None:
        result["screen_note"] = ("All results are shown. Routes order them (direct_evidence, conflict, unscreened, "
                                 "context_only, instruction_flagged); a low route is not a reason to skip a passage you need.")
    return result


async def read_evidence(ctx: RunContext, args: dict) -> dict:
    try:
        item = ctx.evidence.read(args["item_id"])
    except EvidenceAccessError as e:
        raise ToolError(str(e)) from e
    ctx.run.append("evidence_read", object_ids=(args["item_id"],))
    src = item["source"]
    common = {"item_id": args["item_id"], "source_id": src["source_id"], "source_title": src["title"],
              "source_available_at": src["available_at"], "heading_path": item["heading_path"]}
    if "#t" in args["item_id"]:
        return {**common, "kind": "table", "section_id": item["section_id"], "caption": item["caption"],
                "context_before": item["context_before"], "table": item["rendered"], "context_after": item["context_after"],
                "units_hint": item["units_hint"]}
    text = item["text"]
    return {**common, "kind": "section", "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT,
            "tables_in_section": item["tables"], "previous_section_id": item["previous_section_id"],
            "next_section_id": item["next_section_id"]}


async def judge(ctx: RunContext, args: dict) -> dict:
    if ctx.semantics is None:
        raise ToolError("Semantic judgments are not available in this run")
    profile = args.get("profile")
    # Validate the agent's arguments first: its own mistakes are rejected calls, not Jev failures.
    if profile == "claim_interpretation":
        missing = [k for k in ("item_id", "claim", "target") if not args.get(k)]
        if missing:
            raise ToolError(f"claim_interpretation needs {missing}")
    elif profile == "statement_relation":
        ids = list(dict.fromkeys(args.get("finding_ids") or []))
        if len(ids) != 2 or not args.get("proposed_fact"):
            raise ToolError("statement_relation needs exactly two distinct finding_ids and a proposed_fact")
        a, b = (ctx.run.get("findings", f) for f in ids)
    else:
        raise ToolError("profile must be claim_interpretation or statement_relation")
    try:
        if profile == "claim_interpretation":
            obs = await ctx.semantics.interpret(item_id=args["item_id"], claim=args["claim"], target=args["target"],
                                                subject_kind=args.get("subject_kind", "other"),
                                                subject=args.get("subject", ""), anchor_quote=args.get("anchor_quote"))
        else:
            obs = await ctx.semantics.relate(a, b, args["proposed_fact"])
            rel = obs[0]
            if rel.answer == "conflict" or is_ambiguous(rel):
                task = ReconciliationTask(task_id=ctx.run.new_id("recon"), finding_ids=(a.finding_id, b.finding_id),
                                          observation_ids=(rel.observation_id,),
                                          note=f"{meaning(rel.question_id, rel.answer)}: {args['proposed_fact']}")
                ctx.run.put("reconciliation_opened", task)
    except (EvidenceAccessError, ToolError) as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        _record_jev_failure(ctx, e)
        raise ToolError(f"Semantic judgment unavailable: {e}") from e
    return {"observations": [_obs_view(o) for o in obs],
            "note": "Link interpretation observation IDs when you propose a finding that rests on this claim."}


async def propose_finding(ctx: RunContext, args: dict) -> dict:
    ctx.run.get("dependencies", args["dependency_id"])
    spans = []
    for c in args["citations"]:
        try:
            spans.append(locate(ctx.evidence, c["item_id"], c["quote"], int(c.get("occurrence", 1))))
        except (SpanError, EvidenceAccessError) as e:
            raise ToolError(f"Citation rejected: {e}") from e
    linked = tuple(args.get("interpretation_observation_ids", []))
    for oid in linked:
        o = ctx.run.get("observations", oid)
        if o.profile != "claim_interpretation":
            raise ToolError(f"{oid} is not a claim interpretation")
    if not spans:
        raise ToolError("A finding needs at least one verbatim citation")
    finding = AtomicFinding(finding_id=ctx.run.new_id("fnd"), dependency_id=args["dependency_id"],
                            proposition=args["proposition"], target=args["target"],
                            subject_kind=args.get("subject_kind", "other"), subject=args.get("subject", ""),
                            is_inference=bool(args.get("is_inference", False)), spans=tuple(spans), observation_ids=linked)
    ctx.run.put("finding_proposed", finding)
    _attribute_screen(ctx, finding)
    checks: list[SemanticObservation] = []
    if ctx.semantics is not None:
        try:
            checks = await ctx.semantics.check_finding(finding)
        except Exception as e:
            _record_jev_failure(ctx, e)
            raise ToolError(f"Finding recorded as {finding.finding_id} but not checked, so it cannot be accepted: {e}") from e
        finding = finding.model_copy(update={"observation_ids": linked + tuple(o.observation_id for o in checks)})
        ctx.run.put("finding_proposed", finding)
    all_obs = [ctx.run.get("observations", o) for o in finding.observation_ids]
    return {"finding_id": finding.finding_id, "status": "proposed",
            "spans": [{"item_id": s.item_id, "start": s.start, "end": s.end} for s in spans],
            "observations": [_obs_view(o) for o in all_obs],
            "to_accept": ("Give every observation a disposition. Unsettled or ambiguous answers need "
                          "'overridden_by_agent_with_reason' with a reason; an ambiguous entity_scope answer cannot be accepted "
                          "(re-interpret with a more precise target or more context, or reject)."),
            "unsettled_observation_ids": [o.observation_id for o in all_obs if _unsettled(o)]}


async def resolve_finding(ctx: RunContext, args: dict) -> dict:
    finding: AtomicFinding = ctx.run.get("findings", args["finding_id"])
    if finding.status != "proposed":
        raise ToolError(f"{finding.finding_id} is already {finding.status}")
    decision = args["decision"]
    if decision not in ("accept", "reject"):
        raise ToolError("decision must be accept or reject")
    given = {d["observation_id"]: d for d in args.get("dispositions", [])}
    allowed = set(Disposition.__args__)
    bad = [d for d in given.values() if d.get("disposition") not in allowed]
    if bad:
        raise ToolError(f"Unknown disposition(s) {[d.get('disposition') for d in bad]}; use one of {sorted(allowed)}")
    if decision == "accept" and ctx.semantics is not None and not any(
            ctx.run.get("observations", o).profile == "finding_check" for o in finding.observation_ids):
        raise ToolError(f"{finding.finding_id} has no completed finding check, so it cannot be accepted in this run")
    missing = [o for o in finding.observation_ids if o not in given]
    if missing:
        raise ToolError(f"Give a disposition for every linked observation: missing {missing}")
    if decision == "accept":
        for oid in finding.observation_ids:
            o = ctx.run.get("observations", oid)
            d = given[oid]
            if o.question_id == "entity_scope" and (is_ambiguous(o) or o.answer != "target"):
                raise ToolError(f"{oid}: entity scope is {'ambiguous' if is_ambiguous(o) else o.answer}; a finding cannot be "
                                "accepted on an unresolved entity. Re-interpret with a precise target or more context, or reject.")
            if _unsettled(o) and (d["disposition"] != "overridden_by_agent_with_reason"
                                  or len(d.get("note", "").strip()) < OVERRIDE_NOTE_MIN):
                raise ToolError(f"{oid} ({o.question_id}: {meaning(o.question_id, o.answer)}"
                                f"{', ambiguous' if is_ambiguous(o) else ''}) needs an override with a stated reason")
    for oid, d in given.items():
        if oid not in finding.observation_ids:
            continue
        o = ctx.run.get("observations", oid)
        ctx.run.put("observation_disposition", o.model_copy(update={"downstream_disposition": d["disposition"],
                                                                    "disposition_note": d.get("note", "")}))
    resolved = finding.model_copy(update={"status": "accepted" if decision == "accept" else "rejected",
                                          "resolution_note": args.get("note", "")})
    ctx.run.put("finding_resolved", resolved)
    out: dict[str, Any] = {"finding_id": resolved.finding_id, "status": resolved.status}
    if resolved.status == "accepted" and ctx.semantics is not None:
        out["relation_checks"] = await _auto_relations(ctx, resolved)
    return out


def _parameter(p: dict, findings: dict[str, AtomicFinding]) -> ParameterRequirement:
    """A value is documented only when it appears verbatim in a cited finding's quotes; otherwise it is agent-stated."""
    value = None
    if p.get("value_cents") is not None:
        cents = int(p["value_cents"])
        quotes = " ".join(s.quote for f in p.get("finding_ids", []) if f in findings for s in findings[f].spans)
        if p.get("finding_ids") and _cents_in_text(cents, quotes):
            value = documented(cents, Unit.CENTS, note=p.get("description"))
        else:
            value = EvidenceValue(status=Status.EXACT, unit=Unit.CENTS, value=cents, provenance=Provenance(
                basis=Basis.DERIVED, derivation="agent-stated",
                note="Agent-stated amount not found verbatim in the cited findings' quotes"))
    elif p.get("lower_cents") is not None and p.get("upper_cents") is not None:
        lo, hi = int(p["lower_cents"]), int(p["upper_cents"])
        quotes = " ".join(s.quote for f in p.get("finding_ids", []) if f in findings for s in findings[f].spans)
        quoted = bool(p.get("finding_ids")) and _cents_in_text(lo, quotes) and _cents_in_text(hi, quotes)
        value = EvidenceValue(status=Status.RANGE, unit=Unit.CENTS, lower=lo, upper=hi, provenance=Provenance(
            basis=Basis.DOCUMENTED if quoted else Basis.DERIVED, derivation=None if quoted else "agent-stated",
            note=p.get("description") if quoted else "Agent-stated range not found verbatim in the cited findings' quotes"))
    return ParameterRequirement(name=p["name"], description=p.get("description", ""), status=p.get("status", "unknown"),
                                value=value, finding_ids=tuple(p.get("finding_ids", [])), resolves_via=p.get("resolves_via", ""))


async def propose_effect(ctx: RunContext, args: dict) -> dict:
    try:
        effect = EconomicEffectProposal(
            effect_id=ctx.run.new_id("eff"), finding_ids=tuple(args["finding_ids"]), mechanism=args["mechanism"],
            target=args["target"], cash_direction=args["cash_direction"], baseline_treatment=args["baseline_treatment"],
            parameters=tuple(_parameter(p, ctx.run.graph["findings"]) for p in args.get("parameters", [])),
            linked_effect_ids=tuple(args.get("linked_effect_ids", [])), double_count_guard=args.get("double_count_guard", ""),
            model_consequence=args.get("model_consequence", ""))
    except (ValueError, KeyError) as e:
        raise ToolError(f"Effect rejected: {e}") from e
    ctx.run.put("effect_proposed", effect)
    findings = {k: v for k, v in ctx.run.graph["findings"].items()}
    problems = validate_effect(effect, findings, verify_span=lambda sp: verify(ctx.evidence, sp))
    warnings = []
    if ctx.semantics is not None and effect.baseline_treatment == "already_in_baseline_reclassify_timing" and not problems:
        try:
            item = _baseline_item(ctx, "aggregate_debt_observation_id")
            if item is not None:
                overlap = await ctx.semantics.baseline_overlap(
                    f"{effect.mechanism} for {effect.target}", f"{item['label']} (baseline, {item['precision']} "
                    f"${item['value'] / 100:,.0f}, observed {item['observed_on']})", (effect.effect_id,))
                warnings = [_obs_view(o) for o in overlap]
        except Exception as e:
            _record_jev_failure(ctx, e)
    guard_obs: list[SemanticObservation] = []
    overrides = {k: v.strip() for k, v in (args.get("override_reasons") or {}).items() if isinstance(v, str)}
    if ctx.semantics is not None and not problems:
        try:
            guard_problems, guard_obs = await _category_guard(ctx, effect, findings)
            support_problems, support_obs = await _consequence_support(ctx, effect, findings)
        except ToolError:
            raise
        except Exception as e:
            _record_jev_failure(ctx, e)
            raise ToolError(f"Effect {effect.effect_id} could not be checked, so it is not validated: {e}") from e
        guard_obs += support_obs
        for name, found in (("category_guard", guard_problems), ("consequence_support", support_problems)):
            if found and len(overrides.get(name, "")) >= OVERRIDE_NOTE_MIN:
                warnings.append({"overridden": name, "reason": overrides[name], "problems": found})
                _dispose(ctx, [o for o in guard_obs if _guard_name(o) == name], "overridden_by_agent_with_reason", overrides[name])
            elif found:
                problems += found
        _dispose(ctx, [o for o in guard_obs if ctx.run.get("observations", o.observation_id).downstream_disposition == "unused"],
                 "challenged_agent_draft" if problems else "used_in_finding", f"effect guard for {effect.effect_id}")
    validated = effect.model_copy(update={"status": "rejected" if problems else "validated",
                                          "validation_messages": tuple(problems),
                                          "observation_ids": tuple(o.observation_id for o in guard_obs),
                                          "override_reasons": {w["overridden"]: w["reason"] for w in warnings
                                                               if isinstance(w, dict) and "overridden" in w}})
    ctx.run.put("effect_validated", validated)
    return {"effect_id": validated.effect_id, "status": validated.status, "problems": problems,
            "checks": [_obs_view(o) for o in guard_obs], "warnings": warnings,
            "note": ("Validated means structurally sound, semantically consistent and supported; it does not activate any "
                     "cash flow. To proceed past a failed check, revise, or pass override_reasons "
                     "{category_guard|consequence_support: reason} if you have a stated reason.")}


async def run_sensitivity(ctx: RunContext, args: dict) -> dict:
    """Remaining-period required net cash across explicit paid-since-measurement scenarios."""
    opening = _baseline_item(ctx, "opening_cash_observation_id")
    if opening is None:
        raise ToolError("This case's baseline names no opening-cash observation")
    reserve = ctx.inputs["policy"]["required_cash_reserve"]
    effect_ids = list(dict.fromkeys(args["effect_ids"]))  # the same effect is never counted twice
    buckets, seen_findings = [], {}
    for eid in effect_ids:
        eff: EconomicEffectProposal = ctx.run.get("effects", eid)
        if eff.status != "validated" or eff.mechanism != "settlement_payment_timing":
            raise ToolError(f"{eid} must be a validated settlement_payment_timing effect")
        shared = [f for f in eff.finding_ids if f in seen_findings]
        if shared:
            raise ToolError(f"{eid} rests on {shared}, already used by {seen_findings[shared[0]]}: one obligation, one effect")
        seen_findings.update({f: eid for f in eff.finding_ids})
        bucket = next((p for p in eff.parameters if p.name == "remaining_current_year_bucket_cents"), None)
        if (bucket is None or bucket.value is None or bucket.value.status != "exact"
                or bucket.value.provenance.basis != Basis.DOCUMENTED):
            raise ToolError(f"{eid} needs 'remaining_current_year_bucket_cents' as an exact amount found in its cited quotes")
        buckets.append((eid, bucket.value.value))
    unavailable_arg = args.get("unavailable_opening_cash_cents")
    unavailable = (assumed(int(unavailable_arg), Unit.CENTS, "A_unavailable_opening_cash", "operator assumption for this sensitivity")
                   if unavailable_arg is not None else unknown(Unit.CENTS, "restricted or unavailable share of reported cash not reported"))
    opening_ev = documented(opening["value"], Unit.CENTS, approximate=True, observed_on=None)
    reserve_ev = assumed(reserve["value"], Unit.CENTS, "demo_policy_reserve_v1", reserve["note"])
    try:
        fractions = [Decimal(str(x)) for x in args.get("paid_fractions", ["0", "0.5", "1"])]
    except ArithmeticError as e:
        raise ToolError("paid_fractions must be decimal numbers") from e
    if not fractions or any(not Decimal(0) <= f <= 1 for f in fractions):
        raise ToolError("paid_fractions must be between 0 and 1")
    rows = []
    for fr in fractions:
        ins = [(eid, b, assumed(int(b * fr), Unit.CENTS, f"A_paid_{fr}", "scenario: share of the bucket paid since measurement"))
               for eid, b in buckets]
        res = remaining_period_required_net_cash(ins, opening_cash=opening_ev, unavailable_opening_cash=unavailable,
                                                 reserve=reserve_ev)
        rows.append({"share_of_bucket_paid_since_measurement": str(fr),
                     "required_net_cash_cents": res.value, "status": res.status,
                     "missing": res.provenance.note if res.value is None else None})
    out = {"calculation": "required_net_cash = max(0, sum(bucket - paid) + reserve - (reported cash - unavailable portion))",
           "opening_cash_basis": f"{opening['label']}: {opening['precision']} ${opening['value'] / 100:,.0f} as of {opening['observed_on']}",
           "reserve_basis": reserve["note"], "unavailable_opening_cash": unavailable_arg if unavailable_arg is not None else "unknown",
           "buckets": [{"effect_id": e, "bucket_cents": b} for e, b in buckets], "scenarios": rows,
           "reading": "A necessary cumulative condition, not a dated feasibility test and not an observed shortfall."}
    ctx.run.append("sensitivity_run", object_ids=tuple(effect_ids), payload=out)
    return out


async def request_missing_fact(ctx: RunContext, args: dict) -> dict:
    fact_id = ctx.run.new_id("fact")
    ctx.run.append("missing_fact_requested", object_ids=(fact_id, args.get("dependency_id", "")),
                   payload={k: args.get(k) for k in ("fact", "why_pivotal", "acceptable_evidence", "if_resolved", "dependency_id")})
    return {"fact_request_id": fact_id, "note": "Recorded internally; no message is sent."}


async def read_inventory(ctx: RunContext, _args: dict) -> dict:
    items = sorted(ctx.run.graph["inventory"].values(), key=lambda i: (i.status != "open", i.item_id))
    titles = {s["source_id"]: s["title"] for s in ctx.evidence.list_sources()}
    return {"note": ("The host screened every admissible section for specific legal matters, settlements, debt agreements, "
                     "covenants, cash restrictions and matter-related accounting items. Account for every open item before "
                     "submitting. A flag is a pointer to read, not a finding."),
            "open": sum(i.status == "open" for i in items),
            "items": [{"item_id": i.item_id, "status": i.status, "kind": i.kind, "source": titles.get(i.source_id, i.source_id),
                       "heading": " > ".join(i.heading_path[-2:]) or "(whole document)", "section_ids": list(i.section_ids),
                       "excerpt": i.excerpt[:200], **({"finding_ids": list(i.finding_ids)} if i.finding_ids else {}),
                       **({"note": i.note} if i.note else {})} for i in items]}


async def account_for_items(ctx: RunContext, args: dict) -> dict:
    updates = []
    for entry in args.get("items", []):
        item = ctx.run.get("inventory", entry["item_id"])
        disposition = entry.get("disposition")
        if disposition == "covered_by_findings":
            fids = tuple(entry.get("finding_ids") or [])
            bad = [f for f in fids if f not in ctx.run.graph["findings"] or ctx.run.graph["findings"][f].status != "accepted"]
            if not fids or bad:
                raise ToolError(f"{item.item_id}: covered_by_findings needs accepted finding IDs (not accepted: {bad or 'none given'})")
            updates.append(item.model_copy(update={"status": "covered", "finding_ids": fids, "note": entry.get("reason", "")}))
        elif disposition == "not_decision_relevant":
            reason = (entry.get("reason") or "").strip()
            if len(reason) < OVERRIDE_NOTE_MIN:
                raise ToolError(f"{item.item_id}: say why it does not bear on the financing decision")
            updates.append(item.model_copy(update={"status": "not_decision_relevant", "note": reason}))
        else:
            raise ToolError(f"{entry.get('item_id')}: disposition must be covered_by_findings or not_decision_relevant")
    for u in updates:  # all validated before any write
        ctx.run.put("inventory_accounted", u)
    return {"accounted": [u.item_id for u in updates],
            "open": sum(i.status == "open" for i in ctx.run.graph["inventory"].values())}


async def resolve_reconciliation(ctx: RunContext, args: dict) -> dict:
    task = ctx.run.get("reconciliations", args["task_id"])
    note = (args.get("note") or "").strip()
    if len(note) < OVERRIDE_NOTE_MIN:
        raise ToolError("Explain how the statements relate and which finding stands")
    ctx.run.put("reconciliation_resolved", task.model_copy(update={"status": "resolved", "note": f"{task.note} | {note}"}))
    return {"task_id": task.task_id, "status": "resolved"}


async def submit_packet(ctx: RunContext, args: dict) -> dict:
    if ctx.semantics is not None:
        open_items = [i.item_id for i in ctx.run.graph["inventory"].values() if i.status == "open"]
        if open_items:
            raise ToolError(f"{len(open_items)} inventory items are still open: {open_items[:12]}. Use read_inventory and account_for_items.")
        open_tasks = [t.task_id for t in ctx.run.graph["reconciliations"].values() if t.status == "open"]
        if open_tasks:
            raise ToolError(f"Open reconciliation tasks: {open_tasks}. Resolve them with resolve_reconciliation.")
        await _check_conclusion(ctx, args)
    summary = {k: args.get(k) for k in ("summary", "pivotal_unknowns", "supported_effect_ids", "conclusion")}
    summary["incomplete_reasons"] = ctx.incomplete_reasons
    summary["configuration_failure"] = ctx.configuration_failure
    if args.get("conclusion_override_reason"):
        summary["conclusion_override_reason"] = args["conclusion_override_reason"]
    path = ctx.run.lock(summary)
    ctx.submitted = True
    return {"locked": True, "packet": path.name, "chain_head": ctx.run.head}


# --- 4c host checks --------------------------------------------------------------------------------

def _dispose(ctx: RunContext, observations: list[SemanticObservation], disposition: str, note: str) -> None:
    for o in observations:
        current = ctx.run.get("observations", o.observation_id)
        ctx.run.put("observation_disposition", current.model_copy(update={"downstream_disposition": disposition,
                                                                          "disposition_note": note}))


def _guard_name(o: SemanticObservation) -> str:
    return "consequence_support" if o.question_id == "claims_supported" else "category_guard"


def _attribute_screen(ctx: RunContext, finding: AtomicFinding) -> None:
    """Reliance attribution: screen observations on passages this finding cites are recorded as used."""
    cited = {s.item_id for s in finding.spans} | {s.section_id for s in finding.spans}
    for c in ctx.run.graph["candidates"].values():
        if c.screen is None or c.item_id not in cited:
            continue
        unused = [ctx.run.get("observations", o) for o in c.screen.observation_ids
                  if ctx.run.get("observations", o).downstream_disposition == "unused"]
        _dispose(ctx, unused, "used_in_finding", f"screened passage cited in {finding.finding_id}")


async def _auto_relations(ctx: RunContext, finding: AtomicFinding) -> list[dict]:
    """Relation check against the most recent accepted findings under the same dependency; conflicts open tasks."""
    dep = ctx.run.get("dependencies", finding.dependency_id)
    others = [f for f in ctx.run.graph["findings"].values()
              if f.dependency_id == finding.dependency_id and f.status == "accepted" and f.finding_id != finding.finding_id]
    out = []
    for other in others[-RELATION_CHECKS_PER_ACCEPTANCE:]:
        try:
            [rel] = await ctx.semantics.relate(finding, other, dep.question)
        except Exception as e:
            _record_jev_failure(ctx, e)
            out.append({"with": other.finding_id, "error": str(e)[:200]})
            continue
        row = {"with": other.finding_id, **_obs_view(rel)}
        if rel.answer == "conflict" or is_ambiguous(rel):
            task = ReconciliationTask(task_id=ctx.run.new_id("recon"), finding_ids=(finding.finding_id, other.finding_id),
                                      observation_ids=(rel.observation_id,),
                                      note=f"{meaning(rel.question_id, rel.answer)}: {dep.question}")
            ctx.run.put("reconciliation_opened", task)
            _dispose(ctx, [rel], "flagged_conflict", f"opened {task.task_id}")
            row["reconciliation_task"] = task.task_id
        out.append(row)
    return out


async def _category_guard(ctx: RunContext, effect: EconomicEffectProposal,
                          findings: dict[str, AtomicFinding]) -> tuple[list[str], list[SemanticObservation]]:
    """Posture and status of each supporting finding must fit the effect's mechanism (host-run, not agent-optional)."""
    problems, obs_all = [], []
    rule = CATEGORY_RULES.get(effect.mechanism)
    for fid in effect.finding_ids:
        f = findings[fid]
        if effect.mechanism == "noncash_normalization":
            roles = [ctx.run.get("observations", o) for o in f.observation_ids]
            roles = [o for o in roles if o.question_id == "economic_role"]
            if not any(o.answer == "noncash_accounting_item" and not is_ambiguous(o) for o in roles):
                got = ", ".join(meaning(o.question_id, o.answer) for o in roles) or "no economic-role check"
                problems.append(f"Category guard: {fid} is not established as a noncash accounting item ({got})")
            continue
        if rule is None and effect.mechanism not in POSTURE_ONLY:
            continue
        subject_kind = rule[0] if rule else "other"
        obs = await ctx.semantics.interpret(item_id=f.spans[0].item_id, claim=f.proposition, target=f.target,
                                            subject_kind=subject_kind, subject=effect.target, anchor_quote=f.spans[0].quote)
        obs_all += obs
        by_q = {o.question_id: o for o in obs}
        posture = by_q.get("claim_posture")
        allowed_postures = rule[3] if rule else {"agreed_contractually", "imposed_by_court_or_authority", "reported_completed"}
        if posture is None or is_ambiguous(posture) or posture.answer not in allowed_postures:
            problems.append(f"Category guard: {fid} posture is {meaning('claim_posture', posture.answer if posture else None)}"
                            f"{' (ambiguous)' if posture and is_ambiguous(posture) else ''}; {effect.mechanism} needs "
                            f"{sorted(allowed_postures)}")
        if rule:
            status = by_q.get(rule[1])
            if status is None or is_ambiguous(status) or status.answer not in rule[2]:
                problems.append(f"Category guard: {fid} {rule[1]} is {meaning(rule[1], status.answer if status else None)}"
                                f"{' (ambiguous)' if status and is_ambiguous(status) else ''}; {effect.mechanism} needs "
                                f"{sorted(rule[2])}")
        entity = by_q.get("entity_scope")
        if entity is not None and (is_ambiguous(entity) or entity.answer != "target"):
            problems.append(f"Category guard: {fid} does not clearly concern {effect.target} ({meaning('entity_scope', entity.answer)})")
    return problems, obs_all


async def _consequence_support(ctx: RunContext, effect: EconomicEffectProposal,
                               findings: dict[str, AtomicFinding]) -> tuple[list[str], list[SemanticObservation]]:
    if not effect.model_consequence.strip():
        return ["Model consequence is empty; state what this effect means for the cash model"], []
    cited = [findings[f] for f in effect.finding_ids]
    engine = [f"{p.name}: {p.value.value if p.value and p.value.value is not None else 'unknown'}" for p in effect.parameters]
    [obs] = await ctx.semantics.support(effect.model_consequence, cited, engine, (effect.effect_id,))
    # Reject only a clear "some unsupported"; a near-even answer is recorded (visible in checks) but does not block,
    # because false alarms here cost the agent turns without protecting the cash model.
    if obs.answer == "some_unsupported" and not is_ambiguous(obs):
        return [f"Consequence support: {meaning('claims_supported', obs.answer)}. Revise the model consequence to what the "
                "cited findings support"], [obs]
    return [], [obs]


async def _check_conclusion(ctx: RunContext, args: dict) -> None:
    conclusion = (args.get("conclusion") or "").strip()
    accepted = [f for f in ctx.run.graph["findings"].values() if f.status == "accepted"]
    effects = [f"{e.effect_id} ({e.mechanism}, {e.target}): {e.model_consequence}"
               for e in ctx.run.graph["effects"].values() if e.status == "validated"]
    sens = [json.dumps({"buckets": e.payload.get("buckets"), "unavailable_opening_cash": e.payload.get("unavailable_opening_cash"),
                        "scenarios": [(r["share_of_bucket_paid_since_measurement"], r["required_net_cash_cents"])
                                      for r in e.payload.get("scenarios", [])]})
            for e in ctx.run.events if e.kind == "sensitivity_run"]
    try:
        [obs] = await ctx.semantics.support(conclusion, accepted, effects + sens, ("conclusion",), with_quotes=False)
    except Exception as e:
        _record_jev_failure(ctx, e)
        raise ToolError(f"The conclusion could not be checked: {e}") from e
    ok = not (obs.answer == "some_unsupported" and not is_ambiguous(obs))  # same gating as consequence support
    reason = (args.get("conclusion_override_reason") or "").strip()
    ctx.run.append("conclusion_checked", object_ids=(obs.observation_id,),
                   payload={"answer": obs.answer, "ambiguous": is_ambiguous(obs), "override_reason": reason or None})
    if not ok and len(reason) < OVERRIDE_NOTE_MIN:
        _dispose(ctx, [obs], "challenged_agent_draft", "conclusion check")
        raise ToolError(f"Conclusion check: {meaning('claims_supported', obs.answer)}"
                        f"{' (ambiguous)' if is_ambiguous(obs) else ''}. Revise the conclusion to what accepted findings, "
                        "validated effects and sensitivity results support, or give conclusion_override_reason.")
    _dispose(ctx, [obs], "used_in_finding" if ok else "overridden_by_agent_with_reason",
             "conclusion check" if ok else reason)


# --- tool schemas ------------------------------------------------------------------------------------

S = {"type": "string"}
CITATION = {"type": "object", "properties": {"item_id": S, "quote": S, "occurrence": {"type": "integer"}},
            "required": ["item_id", "quote"]}
PARAM = {"type": "object", "properties": {
    "name": S, "description": S, "status": {"type": "string", "enum": ["known", "range", "unknown", "assumption_required"]},
    "value_cents": {"type": "integer"}, "lower_cents": {"type": "integer"}, "upper_cents": {"type": "integer"},
    "finding_ids": {"type": "array", "items": S}, "resolves_via": S}, "required": ["name", "description", "status"]}


def obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


TOOL_SPECS: list[tuple[str, str, dict, Any]] = [
    ("get_mission", "The financing question, borrower, review date, locked operator request and the admissible evidence sources.",
     obj({}, []), get_mission),
    ("read_baseline_profile", "Ordinary baseline facts (with citations) and the operator's cash-reserve policy.", obj({}, []),
     read_baseline_profile),
    ("read_loan_terms", "Existing loan cover terms and the permitted analytical offers with their contractual payment rows.",
     obj({}, []), read_loan_terms),
    ("record_dependency", "Record one decision-relevant unknown: the question, the precisely named target (entity plus the specific "
     "obligation, counterparty, asset or activity), its embedded premises, and the financial quantity it could change.",
     obj({"question": S, "target": S, "premises": {"type": "array", "items": S}, "affects": S, "resolvable_by": S},
         ["question", "target", "affects"]), record_dependency),
    ("search_evidence", "Full-text search of the admissible evidence for one recorded dependency. Returns every result with IDs, "
     "dated snippets and (when available) a semantic screen label.",
     obj({"dependency_id": S, "query": S, "source_ids": {"type": "array", "items": S}, "limit": {"type": "integer"}},
         ["dependency_id", "query"]), search_evidence),
    ("read_evidence", "Read a section or table by item ID with its heading path, context and source date.",
     obj({"item_id": S}, ["item_id"]), read_evidence),
    ("judge", "Ask for a narrow semantic judgment. profile=claim_interpretation: item_id, one claim, a precise target, optional "
     "subject_kind (obligation|cash_pool|activity|offset|other), subject and anchor_quote (verbatim text the claim rests on). "
     "profile=statement_relation: two finding_ids and the proposed_fact they bear on.",
     obj({"profile": {"type": "string", "enum": ["claim_interpretation", "statement_relation"]}, "item_id": S, "claim": S,
          "target": S, "subject_kind": S, "subject": S, "anchor_quote": S, "finding_ids": {"type": "array", "items": S},
          "proposed_fact": S}, ["profile"]), judge),
    ("propose_finding", "Propose one atomic finding for a dependency, citing verbatim quotes (item_id + exact quote). Mark "
     "is_inference when the finding links cited premises the text does not state together. Link any interpretation observations.",
     obj({"dependency_id": S, "proposition": S, "target": S, "subject_kind": S, "subject": S, "is_inference": {"type": "boolean"},
          "citations": {"type": "array", "items": CITATION}, "interpretation_observation_ids": {"type": "array", "items": S}},
         ["dependency_id", "proposition", "target", "citations"]), propose_finding),
    ("resolve_finding", "Accept or reject a proposed finding, giving a disposition for each linked observation (used_in_finding, "
     "caused_more_context_read, caused_research_redirect, flagged_conflict, challenged_agent_draft, "
     "overridden_by_agent_with_reason, unused) with notes.",
     obj({"finding_id": S, "decision": {"type": "string", "enum": ["accept", "reject"]}, "note": S,
          "dispositions": {"type": "array", "items": obj({"observation_id": S, "disposition": {"type": "string", "enum": list(
              Disposition.__args__)}, "note": S},
                                                          ["observation_id", "disposition"])}},
         ["finding_id", "decision"]), resolve_finding),
    ("propose_effect", "Propose an economic effect from accepted findings: mechanism (settlement_payment_timing, "
     "noncash_normalization, receipt_delay, operating_interruption, restricted_funds, expense_funding, funding_constraint, "
     "resolved_obligation), target, cash_direction, baseline_treatment, parameters (use 'remaining_current_year_bucket_cents' "
     "for a settlement's remaining current-year amount), double-count guard and the plain-language model consequence.",
     obj({"finding_ids": {"type": "array", "items": S}, "mechanism": S, "target": S,
          "cash_direction": {"type": "string", "enum": ["inflow", "outflow", "none", "unknown"]},
          "baseline_treatment": {"type": "string", "enum": ["already_in_baseline_reclassify_timing", "new_to_baseline",
                                                            "remove_from_baseline", "normalization_only",
                                                            "exclude_already_paid_obligation", "modify_available_funding"]},
          "parameters": {"type": "array", "items": PARAM}, "linked_effect_ids": {"type": "array", "items": S},
          "double_count_guard": S, "model_consequence": S,
          "override_reasons": obj({"category_guard": S, "consequence_support": S}, [])},
         ["finding_ids", "mechanism", "target", "cash_direction", "baseline_treatment", "model_consequence"]), propose_effect),
    ("run_sensitivity", "Deterministic remaining-period cash requirement for validated settlement effects across scenarios of "
     "how much was paid since the measurement date. Optionally state an assumed unavailable share of reported cash.",
     obj({"effect_ids": {"type": "array", "items": S}, "paid_fractions": {"type": "array", "items": S},
          "unavailable_opening_cash_cents": {"type": "integer"}}, ["effect_ids"]), run_sensitivity),
    ("request_missing_fact", "Record a pivotal fact the evidence cannot supply: what it is, why it is pivotal, what evidence "
     "would resolve it and what changes if it is resolved. Sends no message.",
     obj({"dependency_id": S, "fact": S, "why_pivotal": S, "acceptable_evidence": S, "if_resolved": S},
         ["fact", "why_pivotal", "acceptable_evidence"]), request_missing_fact),
    ("read_inventory", "List the matters the host sweep flagged in the admissible evidence and their accounting status.",
     obj({}, []), read_inventory),
    ("account_for_items", "Account for inventory items in a batch: covered_by_findings (accepted finding_ids) or "
     "not_decision_relevant (with a reason).",
     obj({"items": {"type": "array", "items": obj({"item_id": S, "disposition": {"type": "string", "enum": [
         "covered_by_findings", "not_decision_relevant"]}, "finding_ids": {"type": "array", "items": S}, "reason": S},
         ["item_id", "disposition"])}}, ["items"]), account_for_items),
    ("resolve_reconciliation", "Resolve an open reconciliation task: explain how the statements relate and which finding stands.",
     obj({"task_id": S, "note": S}, ["task_id", "note"]), resolve_reconciliation),
    ("submit_packet", "Lock the investigation for review with a summary, the pivotal unknowns, the supported effect IDs and the "
     "conclusion. The conclusion is checked against accepted findings, validated effects and sensitivity results. "
     "No lending action is taken.",
     obj({"summary": S, "pivotal_unknowns": {"type": "array", "items": S},
          "supported_effect_ids": {"type": "array", "items": S}, "conclusion": S, "conclusion_override_reason": S},
         ["summary", "conclusion"]), submit_packet),
]


AGENT_ONLY_DESCRIPTIONS = {
    "search_evidence": "Full-text search of the admissible evidence for one recorded dependency. Returns every result with IDs "
                       "and dated snippets.",
    "resolve_finding": "Accept or reject a proposed finding with a note explaining the decision.",
    "propose_finding": "Propose one atomic finding for a dependency, citing verbatim quotes (item_id + exact quote). Mark "
                       "is_inference when the finding links cited premises the text does not state together.",
}


def build_server(ctx: RunContext, allowed: list[str]):
    """An in-process MCP server exposing only the allowed tools, each bound to this run."""
    names = {a.removeprefix("mcp__credit__") for a in allowed}
    tools = []
    for name, desc, schema, handler in TOOL_SPECS:
        if name not in names:
            continue
        if ctx.arm == "agent_only":
            desc = AGENT_ONLY_DESCRIPTIONS.get(name, desc)

        async def call(args: dict, _h=handler, _n=name) -> dict:
            if ctx.submitted and _n != "submit_packet":
                return {"content": [{"type": "text", "text": "The packet is locked; no further actions."}], "is_error": True}
            try:
                result = await _h(ctx, args)
                if ctx.max_turns and ctx.max_turns - ctx.turns_used <= 20:
                    result = {**result, "turn_budget": f"{max(0, ctx.max_turns - ctx.turns_used)} turns remain; propose effects, "
                                                       "record missing facts and submit the packet before the run stops."}
                return {"content": [{"type": "text", "text": json.dumps(result, default=str)}]}
            except (ToolError, KeyError, ValueError) as e:
                return {"content": [{"type": "text", "text": f"Rejected: {e}"}], "is_error": True}

        tools.append(tool(name, desc, schema)(call))
    return create_sdk_mcp_server("credit", version="1.0.0", tools=tools)
