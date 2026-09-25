"""Step 4b tool handlers over the real snapshot with a stubbed Jev: isolation, acceptance rules,
effect validation, sensitivity arithmetic and the agent-only arm. No network."""

import asyncio
import json

import pytest

import app.agent.jev as jev_module
from app.agent import tools as T
from app.agent.jev import JevAdapter
from app.agent.jev_profiles import Semantics
from app.agent.run_store import RunStore
from app.config import CASES_DIR, CONTRACTS
from app.evidence import snapshot
from app.evidence.store import EvidenceStore

SNAP = "synergy_20240813"
INPUTS = json.loads((CASES_DIR / SNAP / "run_inputs.json").read_text())


DEFAULT_ANSWERS = {"claim_posture": "agreed_contractually", "obligation_status": "required", "entity_scope": "target",
                   "claims_supported": "all_supported", "finding_support": "supports", "finding_atomicity": "one_claim",
                   "context_sufficiency": "enough", "economic_role": "existing_cash_obligation", "statement_relation": "agree",
                   "coverage_supported": "covered", "adds_matter": "nothing_new", "decision_relevance": "could_not_change"}


class FakeJev:
    """Realistic default answers (others: first criterion) with 0.8, unless `flat` names a question to answer
    ambiguously; `answers` overrides specific questions."""

    def __init__(self, adapter, flat=(), answers=None):
        self.adapter, self.flat = adapter, set(flat)
        self.answers = {**DEFAULT_ANSWERS, **(answers or {})}

    async def system_one(self, *, state, questions, model, response_model):
        self.adapter.physical_attempts += 1
        answers = {}
        for qid, q in questions.items():
            if q.type == "noul":
                answers[qid] = {"type": "noul", "noul": 0.9}
            else:
                keys = list(q.criteria)
                if self.answers.get(qid) in keys:
                    keys.remove(self.answers[qid])
                    keys.insert(0, self.answers[qid])
                top = 0.45 if qid in self.flat else 0.8
                probs = {k: (top if i == 0 else (1 - top) / (len(keys) - 1)) for i, k in enumerate(keys)}
                if qid in self.flat:
                    probs[keys[1]] = 0.4
                answers[qid] = {"type": "choice", "choice": keys[0], "confidence": top, "probabilities": probs}
        return response_model.model_validate({"model": "typesafe/jev-1.13-test", "answers": answers, "usage": {}})


@pytest.fixture(scope="module")
def evidence(tmp_path_factory):
    out = tmp_path_factory.mktemp("ev")
    snapshot.build_snapshot(SNAP, out_dir=out)
    return EvidenceStore(SNAP, out / f"{SNAP}.sqlite")


@pytest.fixture
def make_ctx(evidence, tmp_path, monkeypatch):
    monkeypatch.setattr(jev_module, "jev_credential", lambda p: "k")
    monkeypatch.setattr(jev_module, "CACHE_DIR", tmp_path / "cache")

    def make(arm="agent_plus_jev", flat=(), answers=None, name=""):
        run = RunStore(f"run-{arm}-{len(flat)}-{name}", root=tmp_path, meta={"arm": arm})
        sem = None
        if arm == "agent_plus_jev":
            a = JevAdapter(run_id=run.run_id, new_id=run.new_id)
            a.client = FakeJev(a, flat, answers)
            sem = Semantics(run, evidence, a)
        return T.RunContext(run=run, evidence=evidence, inputs=INPUTS, arm=arm, semantics=sem)
    return make


def call(handler, ctx, args=None):
    return asyncio.run(handler(ctx, args or {}))


def test_initial_context_is_ordinary_and_free_of_evaluator_content(make_ctx):
    ctx = make_ctx()
    mission = json.dumps(call(T.get_mission, ctx))
    baseline = json.dumps([call(T.read_baseline_profile, ctx), call(T.read_loan_terms, ctx)])
    private = json.loads((CONTRACTS / "case_eval_private.json").read_text())
    for s in ["Vitabest", "Delayed Draw", "expected_findings", *private["cases"]["synergy_chc_2024"]["outcome_source_ids"]]:
        assert s not in mission + baseline, s
    # Event mechanisms are for the investigation to establish; the ordinary baseline must not pre-digest them.
    # (Document titles in the source list may name parties, as the admissible court caption does.)
    for s in ["L.O.D.C", "Atrium", "HVL", "2,235,986", "802,445", "600,000", "settlement"]:
        assert s not in baseline, s


def test_tools_take_no_paths_snapshots_or_raw_jev_state():
    banned = {"path", "file", "db", "database", "snapshot", "snapshot_id", "cutoff", "as_of", "state", "question_ids", "questions"}
    for _name, _desc, schema, _h in T.TOOL_SPECS:
        assert not banned & set(schema.get("properties", {}))


def _settlement_flow(ctx):
    dep = call(T.record_dependency, ctx, {"question": "What settlement payments remain in 2024 on the supplier settlement loan?",
                                          "target": "Synergy CHC Corp. — the December 28, 2023 supplier settlement loan",
                                          "affects": "dated settlement outflows"})["dependency_id"]
    res = call(T.search_evidence, ctx, {"dependency_id": dep, "query": "required to make future payments settlement former supplier"})
    hvl = next(h for h in ctx.evidence.search("December 28, 2023 settlement 802,445") if h["kind"] == "section")
    item = hvl["id"]
    quote = "| 2024 | $2,000,000 |"
    interp = call(T.judge, ctx, {"profile": "claim_interpretation", "item_id": item, "claim": "The Company must make future payments on the settlement loan.",
                                 "target": "Synergy CHC Corp. — the December 28, 2023 supplier settlement loan",
                                 "subject_kind": "obligation", "subject": "the settlement loan", "anchor_quote": quote})
    prop = call(T.propose_finding, ctx, {"dependency_id": dep, "proposition": "The settlement loan requires future payments.",
                                         "target": "Synergy CHC Corp. — the December 28, 2023 supplier settlement loan",
                                         "citations": [{"item_id": item, "quote": quote}],
                                         "interpretation_observation_ids": [o["observation_id"] for o in interp["observations"]]})
    return dep, res, prop


def test_search_shows_everything_and_findings_need_dispositions(make_ctx):
    ctx = make_ctx()
    dep, res, prop = _settlement_flow(ctx)
    assert res["results"] and all("screen" in r for r in res["results"])
    with pytest.raises(T.ToolError, match="Citation rejected"):
        call(T.propose_finding, ctx, {"dependency_id": dep, "proposition": "x", "target": "Synergy CHC Corp. — x",
                                      "citations": [{"item_id": prop["spans"][0]["item_id"], "quote": "text that is not in the filing at all"}]})
    with pytest.raises(T.ToolError, match="disposition"):
        call(T.resolve_finding, ctx, {"finding_id": prop["finding_id"], "decision": "accept", "dispositions": []})
    disp = [{"observation_id": o["observation_id"], "disposition": "used_in_finding"} for o in prop["observations"]]
    assert call(T.resolve_finding, ctx, {"finding_id": prop["finding_id"], "decision": "accept", "dispositions": disp})["status"] == "accepted"
    obs = ctx.run.graph["observations"]
    assert all(obs[d["observation_id"]].downstream_disposition == "used_in_finding" for d in disp)


def test_ambiguous_entity_scope_can_never_be_accepted(make_ctx):
    ctx = make_ctx(flat=("entity_scope",))
    _, _, prop = _settlement_flow(ctx)
    disp = [{"observation_id": o["observation_id"], "disposition": "overridden_by_agent_with_reason",
             "note": "The note identifies the loan by its date and amount."} for o in prop["observations"]]
    with pytest.raises(T.ToolError, match="entity scope is ambiguous"):
        call(T.resolve_finding, ctx, {"finding_id": prop["finding_id"], "decision": "accept", "dispositions": disp})
    assert call(T.resolve_finding, ctx, {"finding_id": prop["finding_id"], "decision": "reject", "dispositions": disp})["status"] == "rejected"


def test_effects_are_validated_and_sensitivity_is_deterministic(make_ctx):
    ctx = make_ctx()
    _, _, prop = _settlement_flow(ctx)
    disp = [{"observation_id": o["observation_id"], "disposition": "used_in_finding"} for o in prop["observations"]]
    call(T.resolve_finding, ctx, {"finding_id": prop["finding_id"], "decision": "accept", "dispositions": disp})
    bad = call(T.propose_effect, ctx, {"finding_ids": [prop["finding_id"]], "mechanism": "noncash_normalization", "target": "x",
                                       "cash_direction": "outflow", "baseline_treatment": "normalization_only",
                                       "model_consequence": "No cash effect."})
    assert bad["status"] == "rejected"
    eff = call(T.propose_effect, ctx, {"finding_ids": [prop["finding_id"]], "mechanism": "settlement_payment_timing",
                                       "target": "supplier settlement loan", "cash_direction": "outflow",
                                       "baseline_treatment": "already_in_baseline_reclassify_timing",
                                       "parameters": [{"name": "remaining_current_year_bucket_cents", "description": "2024 bucket at June 30",
                                                       "status": "known", "value_cents": 200_000_000, "finding_ids": [prop["finding_id"]]},
                                                      {"name": "paid_since_june_30", "description": "payments since June 30", "status": "unknown"}],
                                       "double_count_guard": "Existing liability; scheduled once", "model_consequence": "Dated outflows"})
    assert eff["status"] == "validated" and eff["checks"]
    stated = call(T.propose_effect, ctx, {"finding_ids": [prop["finding_id"]], "mechanism": "settlement_payment_timing",
                                          "target": "same loan, typed amount", "cash_direction": "outflow",
                                          "baseline_treatment": "already_in_baseline_reclassify_timing",
                                          "model_consequence": "Up to $2,000,000 of 2024 settlement payments.",
                                          "parameters": [{"name": "remaining_current_year_bucket_cents", "description": "typed",
                                                          "status": "known", "value_cents": 123_456_700, "finding_ids": [prop["finding_id"]]}]})
    with pytest.raises(T.ToolError, match="found in its cited quotes"):  # agent-stated amounts never feed the engine
        call(T.run_sensitivity, ctx, {"effect_ids": [stated["effect_id"]]})
    twice = call(T.run_sensitivity, ctx, {"effect_ids": [eff["effect_id"], eff["effect_id"]], "unavailable_opening_cash_cents": 0})
    assert len(twice["buckets"]) == 1  # the same effect is counted once
    wrong = call(T.propose_effect, ctx, {"finding_ids": [prop["finding_id"]], "mechanism": "resolved_obligation", "target": "x",
                                         "cash_direction": "none", "baseline_treatment": "normalization_only",
                                         "model_consequence": "No future cash effect."})
    assert wrong["status"] == "rejected" and any("does not fit" in m for m in wrong["problems"])
    unknown_share = call(T.run_sensitivity, ctx, {"effect_ids": [eff["effect_id"]]})
    assert all(r["required_net_cash_cents"] is None for r in unknown_share["scenarios"])  # unknown stays unknown
    s = call(T.run_sensitivity, ctx, {"effect_ids": [eff["effect_id"]], "unavailable_opening_cash_cents": 0})
    by = {r["share_of_bucket_paid_since_measurement"]: r["required_net_cash_cents"] for r in s["scenarios"]}
    assert by == {"0": 20_000_000, "0.5": 0, "1": 0}  # $2.0m bucket + $0.2m reserve - $2.0m reported cash


def test_dispositions_are_validated_before_anything_is_written(make_ctx):
    ctx = make_ctx()
    _, _, prop = _settlement_flow(ctx)
    before = len(ctx.run.events)
    bad = [{"observation_id": o["observation_id"], "disposition": "used"} for o in prop["observations"]]
    with pytest.raises(T.ToolError, match="Unknown disposition"):
        call(T.resolve_finding, ctx, {"finding_id": prop["finding_id"], "decision": "accept", "dispositions": bad})
    assert len(ctx.run.events) == before
    RunStoreReload = type(ctx.run)
    assert RunStoreReload(ctx.run.run_id, root=ctx.run.dir.parent).head == ctx.run.head  # the log still loads


def test_unchecked_findings_cannot_be_accepted_and_jev_failures_are_recorded(make_ctx):
    ctx = make_ctx()

    async def broken(*a, **k):
        raise T.ConfigurationError("Unexpected Jev model 'other'")
    dep = call(T.record_dependency, ctx, {"question": "q", "target": "Synergy CHC Corp. — settlement loan", "affects": "a"})["dependency_id"]
    hvl = next(h for h in ctx.evidence.search("December 28, 2023 settlement 802,445") if h["kind"] == "section")
    ctx.semantics.check_finding = broken
    with pytest.raises(T.ToolError, match="not checked"):
        call(T.propose_finding, ctx, {"dependency_id": dep, "proposition": "p", "target": "Synergy CHC Corp. — settlement loan",
                                      "citations": [{"item_id": hvl["id"], "quote": "| 2024 | $2,000,000 |"}]})
    fid = next(iter(ctx.run.graph["findings"]))
    with pytest.raises(T.ToolError, match="no completed finding check"):
        call(T.resolve_finding, ctx, {"finding_id": fid, "decision": "accept", "dispositions": []})
    assert ctx.configuration_failure and ctx.incomplete_reasons


def test_agent_only_arm_has_no_screen_or_judge(make_ctx):
    ctx = make_ctx(arm="agent_only")
    dep = call(T.record_dependency, ctx, {"question": "q", "target": "Synergy CHC Corp. — settlement loan", "affects": "a"})["dependency_id"]
    res = call(T.search_evidence, ctx, {"dependency_id": dep, "query": "settlement"})
    assert res["results"] and not any("screen" in r for r in res["results"])
    with pytest.raises(T.ToolError):
        call(T.judge, ctx, {"profile": "claim_interpretation"})


# --- step 4c host checks -----------------------------------------------------------------------------

def _accepted_settlement_finding(ctx):
    dep, _, prop = _settlement_flow(ctx)
    disp = [{"observation_id": o["observation_id"], "disposition": "used_in_finding"} for o in prop["observations"]]
    call(T.resolve_finding, ctx, {"finding_id": prop["finding_id"], "decision": "accept", "dispositions": disp})
    return dep, prop["finding_id"]


SETTLEMENT = {"mechanism": "settlement_payment_timing", "target": "the December 28, 2023 supplier settlement loan",
              "cash_direction": "outflow", "baseline_treatment": "already_in_baseline_reclassify_timing",
              "model_consequence": "Up to $2,000,000 of 2024 settlement payments."}


def test_category_guard_blocks_a_disputed_amount_from_becoming_a_payment(make_ctx):
    ctx = make_ctx(answers={"obligation_status": "claimed_or_disputed"}, name="disputed")
    _, fid = _accepted_settlement_finding(ctx)
    eff = call(T.propose_effect, ctx, {"finding_ids": [fid], **SETTLEMENT})
    assert eff["status"] == "rejected" and any("Claimed or disputed" in p for p in eff["problems"])
    # no free-text override: disagreement escalates, the effect is disputed, creates no cash stream, and the run is incomplete
    again = call(T.propose_effect, ctx, {"finding_ids": [fid], **SETTLEMENT, "override_reasons": {"category_guard": "x" * 40}})
    assert again["status"] == "rejected"
    with pytest.raises(T.ToolError, match="Say why"):
        call(T.escalate_effect, ctx, {"effect_id": eff["effect_id"], "reason": "no"})
    call(T.escalate_effect, ctx, {"effect_id": eff["effect_id"],
                                  "reason": "The schedule is an agreed settlement loan; the disputed reading concerns the claim."})
    assert ctx.run.get("effects", eff["effect_id"]).status == "disputed"
    with pytest.raises(T.ToolError, match="disputed; list only validated"):
        call(T.submit_packet, ctx, {"summary": "s", "conclusion": "c", "supported_effect_ids": [eff["effect_id"]]})
    call(T.submit_packet, ctx, {"summary": "s", "conclusion": "The settlement loan requires future payments."})
    assert any("disputed effect" in r for r in ctx.incomplete_reasons)


def test_unsupported_consequence_is_rejected(make_ctx):
    ctx = make_ctx(answers={"claims_supported": "some_unsupported"}, name="unsupported")
    _, fid = _accepted_settlement_finding(ctx)
    eff = call(T.propose_effect, ctx, {"finding_ids": [fid], **SETTLEMENT})
    assert eff["status"] == "rejected" and any("Consequence support" in p for p in eff["problems"])
    failed = next(c["observation_id"] for c in eff["checks"] if c["question_id"] == "claims_supported")
    reason = "The finding quotes the $2,000,000 2024 row of the settlement schedule."
    with pytest.raises(T.ToolError, match="keep the model consequence"):
        call(T.propose_effect, ctx, {"finding_ids": [fid], **SETTLEMENT, "model_consequence": "Something else entirely.",
                                     "reply_to_failed_check": {"observation_id": failed, "reason": reason}})
    ok = call(T.propose_effect, ctx, {"finding_ids": [fid], **SETTLEMENT,
                                      "reply_to_failed_check": {"observation_id": failed, "reason": reason}})
    assert ok["status"] == "validated" and ctx.run.get("observations", failed).downstream_disposition == "overridden_by_agent_with_reason"


def test_near_even_unsupported_passes_but_is_not_counted_as_used(make_ctx):
    ctx = make_ctx(flat=("claims_supported",), answers={"claims_supported": "some_unsupported"}, name="neareven")
    _, fid = _accepted_settlement_finding(ctx)
    eff = call(T.propose_effect, ctx, {"finding_ids": [fid], **SETTLEMENT})
    support = [o for o in ctx.run.graph["observations"].values() if o.question_id == "claims_supported"]
    assert eff["status"] == "validated" and support and all(o.downstream_disposition == "unused" for o in support)


def test_inventory_and_reconciliations_gate_submission(make_ctx):
    from app.domain.investigation import InventoryItem
    ctx = make_ctx(answers={"statement_relation": "conflict"}, name="gates")
    note11 = next(h for h in ctx.evidence.search("December 28, 2023 settlement 802,445") if h["kind"] == "section")["id"]
    ctx.run.put("inventory_loaded", InventoryItem(item_id="inv_001", section_ids=(note11,), source_id="synergy_s1a_20240813",
                                                 heading_path=("Note 11",), kind="debt_or_financing_agreement", signal=0.9))
    dep, fid = _accepted_settlement_finding(ctx)
    hvl = next(h for h in ctx.evidence.search("December 28, 2023 settlement 802,445") if h["kind"] == "section")
    second = call(T.propose_finding, ctx, {"dependency_id": dep, "proposition": "The settlement loan balance was $4,802,445.",
                                           "target": "Synergy CHC Corp. — the December 28, 2023 supplier settlement loan",
                                           "citations": [{"item_id": hvl["id"], "quote": "The outstanding loan balance at both June 30, 2024 and December 31, 2023 was $4,802,445"}]})
    disp = [{"observation_id": o["observation_id"], "disposition": "used_in_finding"} for o in second["observations"]]
    res = call(T.resolve_finding, ctx, {"finding_id": second["finding_id"], "decision": "accept", "dispositions": disp})
    task = res["relation_checks"][0]["reconciliation_task"]
    submit = {"summary": "s", "conclusion": "The settlement loan requires future payments."}
    with pytest.raises(T.ToolError, match="inventory items are still open"):
        call(T.submit_packet, ctx, submit)
    bad = call(T.account_for_items, ctx, {"items": [{"item_id": "inv_001", "disposition": "covered_by_findings", "finding_ids": ["fnd_999"]}]})
    assert bad["accounted"] == [] and "needs accepted" in bad["rejected"][0]["reason"]
    call(T.account_for_items, ctx, {"items": [{"item_id": "inv_001", "disposition": "covered_by_findings", "finding_ids": [fid]}]})
    with pytest.raises(T.ToolError, match="Open reconciliation"):
        call(T.submit_packet, ctx, submit)
    call(T.resolve_reconciliation, ctx, {"task_id": task, "note": "Different measures of the same loan: balance vs schedule."})
    assert call(T.submit_packet, ctx, submit)["locked"]


def test_cited_screened_passages_are_recorded_as_used(make_ctx):
    ctx = make_ctx(name="attrib")
    _settlement_flow(ctx)
    cited = {s.section_id for f in ctx.run.graph["findings"].values() if f.status == "accepted" for s in f.spans}
    screened = [c for c in ctx.run.graph["candidates"].values() if c.screen and c.item_id in cited]
    if screened:
        assert all(ctx.run.get("observations", o).downstream_disposition == "used_in_finding"
                   for c in screened for o in c.screen.observation_ids)


def test_submit_refuses_unvalidated_supported_effects(make_ctx):
    ctx = make_ctx(name="supported")
    with pytest.raises(T.ToolError, match="eff_999 is unknown"):
        asyncio.run(T.submit_packet(ctx, {"conclusion": "x", "supported_effect_ids": ["eff_999"]}))


def test_sweep_chunks_and_groups():
    from app.agent.sweep import chunks, inventory_groups
    parts = chunks("a" * 9000 + "\n\n" + "b" * 10)
    assert all(len(x) <= 8000 for x in parts) and "".join(parts).replace("\n", "").count("a") == 9000
    sweep = {"records": [
        {"flagged": True, "source_id": "court", "heading_path": ["Page 1"], "kind": "legal_matter_or_settlement", "section_id": "c#1", "signal": 0.9, "excerpt": "x"},
        {"flagged": True, "source_id": "court", "heading_path": ["Page 2"], "kind": "legal_matter_or_settlement", "section_id": "c#2", "signal": 0.95, "excerpt": "y"},
        {"flagged": True, "source_id": "s1a", "heading_path": ["NOTES", "Note 11"], "kind": "debt_or_financing_agreement", "section_id": "s#1", "signal": 0.9, "excerpt": "z"},
        {"flagged": False, "source_id": "s1a", "heading_path": ["RISK"], "kind": "none_or_generic", "section_id": "s#2", "signal": 0.2, "excerpt": ""}]}
    groups = inventory_groups(sweep)
    assert len(groups) == 2 and groups[0]["section_ids"] == ["c#1", "c#2"] and groups[0]["excerpt"] == "y"


def test_covered_claims_are_checked(make_ctx):
    from app.domain.investigation import InventoryItem
    ctx = make_ctx(answers={"coverage_supported": "not_covered"}, name="coverage")
    note11 = next(h for h in ctx.evidence.search("December 28, 2023 settlement 802,445") if h["kind"] == "section")["id"]
    ctx.run.put("inventory_loaded", InventoryItem(item_id="inv_001", section_ids=(note11,), source_id="synergy_s1a_20240813",
                                                 heading_path=("Note 11",), kind="accounting_item_from_a_matter", signal=0.9))
    _, fid = _accepted_settlement_finding(ctx)
    entry = {"item_id": "inv_001", "disposition": "covered_by_findings", "finding_ids": [fid]}
    out = call(T.account_for_items, ctx, {"items": [entry]})
    assert out["accounted"] == [] and "different matter" in out["rejected"][0]["reason"]
    assert ctx.run.get("inventory", "inv_001").status == "open"
    # no free-text override of a coverage gap
    still = call(T.account_for_items, ctx, {"items": [{**entry, "override_reason": "The schedule is the matter; the gain is elsewhere."}]})
    assert still["open"] == 1
    failed = next(o.observation_id for o in ctx.run.graph["observations"].values() if o.question_id == "coverage_supported")
    missing = "The accounting gain on the settlement is not accounted for by any finding."
    bad = call(T.account_for_items, ctx, {"items": [{"item_id": "inv_001", "disposition": "escalate", "observation_id": "obs_999",
                                                     "missing": missing}]})
    assert "failed host check" in bad["rejected"][0]["reason"]
    call(T.account_for_items, ctx, {"items": [{"item_id": "inv_001", "disposition": "escalate", "observation_id": failed,
                                               "missing": missing}]})
    assert ctx.run.get("inventory", "inv_001").status == "disputed"
    call(T.submit_packet, ctx, {"summary": "s", "conclusion": "The settlement loan requires future payments."})
    assert any("disputed inventory item inv_001" in r for r in ctx.incomplete_reasons)  # a matter kind: INCOMPLETE_REVIEW


def test_duplicate_and_relevance_claims_are_checked(make_ctx):
    from app.domain.investigation import InventoryItem
    ctx = make_ctx(answers={"adds_matter": "adds_item", "decision_relevance": "could_change"}, name="dup")
    note11 = next(h for h in ctx.evidence.search("December 28, 2023 settlement 802,445") if h["kind"] == "section")["id"]
    for iid in ("inv_001", "inv_002", "inv_003"):
        ctx.run.put("inventory_loaded", InventoryItem(item_id=iid, section_ids=(note11,), source_id="synergy_s1a_20240813",
                                                     heading_path=("Note 11",), kind="debt_or_financing_agreement", signal=0.9))
    _, fid = _accepted_settlement_finding(ctx)
    call(T.account_for_items, ctx, {"items": [{"item_id": "inv_001", "disposition": "covered_by_findings", "finding_ids": [fid]}]})
    out = call(T.account_for_items, ctx, {"items": [
        {"item_id": "inv_002", "disposition": "duplicate_of", "duplicate_of": "inv_003"},  # not covered
        {"item_id": "inv_003", "disposition": "duplicate_of", "duplicate_of": "inv_001"}]})  # Jev: adds an item
    reasons = [r["reason"] for r in out["rejected"]]
    assert out["accounted"] == [] and "already covered" in reasons[0] and "duplicate check" in reasons[1]
    out = call(T.account_for_items, ctx, {"items": [
        {"item_id": "inv_002", "disposition": "not_decision_relevant", "reason": "An old matter with no bearing on the draw."}]})
    assert "decision-relevance check failed" in out["rejected"][0]["reason"]
