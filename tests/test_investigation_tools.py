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
                   "coverage_supported": "covered"}


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


def test_reconciliations_gate_submission(make_ctx):
    ctx = make_ctx(answers={"statement_relation": "conflict"}, name="gates")
    dep, fid = _accepted_settlement_finding(ctx)
    hvl = next(h for h in ctx.evidence.search("December 28, 2023 settlement 802,445") if h["kind"] == "section")
    second = call(T.propose_finding, ctx, {"dependency_id": dep, "proposition": "The settlement loan balance was $4,802,445.",
                                           "target": "Synergy CHC Corp. — the December 28, 2023 supplier settlement loan",
                                           "citations": [{"item_id": hvl["id"], "quote": "The outstanding loan balance at both June 30, 2024 and December 31, 2023 was $4,802,445"}]})
    disp = [{"observation_id": o["observation_id"], "disposition": "used_in_finding"} for o in second["observations"]]
    res = call(T.resolve_finding, ctx, {"finding_id": second["finding_id"], "decision": "accept", "dispositions": disp})
    task = res["relation_checks"][0]["reconciliation_task"]
    submit = {"summary": "s", "conclusion": "The settlement loan requires future payments."}
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


def test_sweep_units_are_atomic():
    from app.agent.sweep import chunks, unit_of, units
    parts = chunks("a" * 9000 + "\n\n" + "b" * 10)
    assert all(len(x) <= 8000 for x in parts) and "".join(parts).replace("\n", "").count("a") == 9000
    text = ("The notes payable are as follows, at the dates shown below:\n\n[t1]\n| | 2024 | 2023 |\n| Knight | 12 | 13 |\n"
            "| Sanders | 9 | 10 |\n\nShort\n\nThe settlement resulted in a gain reflected as a reduction of cost of sales.")
    us = units(text)
    assert [u["kind"] for u in us] == ["paragraph", "table_row", "table_row", "paragraph"]  # "Short" is not a unit
    assert us[1]["text"] == "| | 2024 | 2023 |\n| Knight | 12 | 13 |"  # each row carries its header
    assert text[us[2]["start"]:us[2]["end"]] == "| Sanders | 9 | 10 |"
    assert unit_of(text, text.index("gain"))["kind"] == "paragraph"


def test_cited_units_gate_submission(make_ctx):
    from app.agent.sweep import unit_of
    from app.domain.investigation import InventoryItem
    ctx = make_ctx(answers={"coverage_supported": "partly_covered"}, name="cited")
    _, fid = _accepted_settlement_finding(ctx)
    span = ctx.run.get("findings", fid).spans[0]
    text = ctx.evidence.read_section(span.section_id)["text"]
    u = unit_of(text, span.start)
    other = next(x for x in __import__("app.agent.sweep", fromlist=["units"]).units(text) if x["start"] != u["start"])
    for iid, unit in (("inv_001", u), ("inv_002", other)):  # the cited unit and another flagged unit nobody cites
        ctx.run.put("inventory_loaded", InventoryItem(item_id=iid, section_ids=(span.section_id,), source_id=span.source_id,
                                                     heading_path=("Note 11",), kind="debt_or_financing_agreement", signal=0.9,
                                                     unit_kind=unit["kind"], unit_start=unit["start"], unit_end=unit["end"]))
    submit = {"summary": "s", "conclusion": "The settlement loan requires future payments."}
    with pytest.raises(T.ToolError, match="cited paragraphs or rows") as err:
        call(T.submit_packet, ctx, submit)
    failed = next(o.observation_id for o in ctx.run.graph["observations"].values() if o.question_id == "coverage_supported")
    assert failed in str(err.value)
    with pytest.raises(T.ToolError, match="say what"):
        call(T.submit_packet, ctx, {**submit, "coverage_escalations": [{"observation_id": failed, "missing": "no"}]})
    missing = "The paragraph also reports the accounting gain, which no finding states."
    call(T.submit_packet, ctx, {**submit, "coverage_escalations": [{"observation_id": failed, "missing": missing}]})
    assert not ctx.incomplete_reasons  # an escalation goes to the reviewer's checklist, not straight to INCOMPLETE
    packet = next(e.payload for e in ctx.run.events if e.kind == "packet_submitted")
    checklist = packet["reviewer_checklist"]
    assert [e["kind"] for e in checklist["escalations"]] == ["cited_unit_coverage"]
    assert [x["item_id"] for x in checklist["uncited_flagged_units"]] == ["inv_002"]
    assert ctx.run.get("inventory", "inv_001").status == "cited" and ctx.run.get("inventory", "inv_002").status == "uncited"


def test_gap_share_and_short_quotes_and_amounts():
    from app.agent.sweep import units_touching
    from app.domain.investigation import SemanticObservation

    def cov(p):
        return SemanticObservation(observation_id="obs_x", call_id="c", profile="inventory_coverage",
                                   question_id="coverage_supported", question_version="3.8.0", primitive="choice",
                                   answer=max(p, key=p.get), probabilities=p)
    assert T._coverage_gap(cov({"partly_covered": 0.55, "not_covered": 0.45, "covered": 0.0}))  # two gap answers: a gap
    assert not T._coverage_gap(cov({"partly_covered": 0.52, "covered": 0.48}))  # near-even with covered: not blocking
    text = "Daily Payment Percentage means 25%\n\nThe Minimum Payment is thirty percent of the Total Payment Amount."
    assert [u["text"] for u in units_touching(text, 0, 10)] == ["Daily Payment Percentage means 25%"]  # short, still checked
    assert len(units_touching(text, 5, text.index("Minimum") + 3)) == 2  # a quote across paragraphs checks both
    assert T._cents_in_text(223_598_600, "a gain to us of $2,235,986, and is reflected")  # comma after the amount
    assert not T._cents_in_text(60_000_000, "$2,600,000")


def test_a_dispute_is_modelled_once(make_ctx):
    from app.domain.investigation import DisputeInstance
    from app.domain.values import Basis, EvidenceValue, Provenance, Status, Unit

    ctx = make_ctx(name="dispute_once")
    dep, fid = _accepted_settlement_finding(ctx)
    amount = EvidenceValue(status=Status.EXACT, unit=Unit.CENTS, value=200_000_000,
                           provenance=Provenance(basis=Basis.DOCUMENTED))
    ctx.run.put("dispute_instantiated", DisputeInstance(
        instance_id="dispute_001", dependency_id=dep, model_id="m", model_version="3", title="t",
        order_reference="D. Del. 1:21-cv-01417", nature="fee_and_cost_award", counterparty="c", finding_ids=(fid,),
        amount=amount))
    args = {"dependency_id": dep, "title": "t", "order_reference": "D. Del. 1:21-cv-01417", "nature": "fee_and_cost_award",
            "finding_ids": [fid], "counterparty": "Qorvo, Inc.",
            "amount": {"value_cents": 200_000_000}}
    with pytest.raises(T.ToolError, match="already instantiated"):
        call(T.instantiate_dispute, ctx, args)  # the same findings again would count the dispute twice



NOTES_QUOTE = ("The Notes bear interest at 6.0% payable semi-annually on June 15 and December 15, the next payment of "
               "$1,320,000 due December 15, 2024. Aggregate principal outstanding: $44.0 million. An Event of Default "
               "includes final judgments aggregating in excess of $10.0 million that remain unpaid or unstayed for 60 "
               "days after notice. The Company shall give notice within 20 Business Days and repurchase between 20 and "
               "35 Business Days after the notice. Compliance deadline: October 21, 2024.")
JUDGMENT_QUOTE = ("D.I. 602: judgment of $31,315,215 in unjust enrichment, $7,000,000 exemplary and $279,808 patent "
                  "damages, entered May 20, 2024. D.I. 613 asks for a new trial or remittitur to $23,100,000. D.I. 605: "
                  "briefing closes August 8, 2024. Complaint filed October 4, 2021.")


def _accepted_with_quote(ctx, text: str, fid: str) -> tuple[str, str]:
    """An accepted finding whose single span quotes `text` (only the quotes and the status are read by the guards)."""
    dep, base = _accepted_settlement_finding(ctx)
    f = ctx.run.get("findings", base)
    span = f.spans[0].model_copy(update={"quote": text})
    ctx.run.put("finding_resolved", f.model_copy(update={"finding_id": fid, "spans": (span,)}))
    return dep, fid


def test_financing_terms_are_checked_against_the_quotes(make_ctx):
    ctx = make_ctx(name="financing")
    dep, fid = _accepted_with_quote(ctx, NOTES_QUOTE, "finding_notes")
    good = {"dependency_id": dep, "title": "6.0% convertible notes", "issuer": "Borrower", "kind": "convertible_notes",
            "finding_ids": [fid], "principal_cents": 4_400_000_000, "coupon_cents": 132_000_000,
            "interest_dates": ["2024-12-15"], "judgment_default_threshold_cents": 1_000_000_000,
            "judgment_default_days": 60, "listing_deadline": "2024-10-21", "repurchase_notice_business_days": 20,
            "repurchase_business_days": [20, 35]}
    for bad, match in (({"principal_cents": 4_500_000_000}, "not in the cited quotes"),
                       ({"judgment_default_days": 90}, "not in the cited quotes"),
                       ({"listing_deadline": "2024-10-22"}, "not in the cited quotes"),
                       ({"repurchase_business_days": [35, 20]}, r"\[earliest, latest\]"),
                       ({"kind": "credit_agreement"}, "kind is one of"),
                       ({"dispute_ids": ["dispute_999"]}, "not live dispute"),
                       ({"insured_cents": 5_000_000}, "insured_cents")):
        with pytest.raises(T.ToolError, match=match):
            call(T.instantiate_financing, ctx, {**good, **bad})
    out = call(T.instantiate_financing, ctx, good)
    inst = ctx.run.get("financing", out["instrument_id"])
    assert inst.principal_cents == 4_400_000_000 and inst.repurchase_business_days == (20, 35)
    with pytest.raises(T.ToolError, match="already instantiated"):
        call(T.instantiate_financing, ctx, good)  # an instrument is modelled once


def test_dispute_components_and_motions_are_checked(make_ctx):
    ctx = make_ctx(name="components")
    dep, fid = _accepted_with_quote(ctx, JUDGMENT_QUOTE, "finding_judgment")
    base = {"dependency_id": dep, "title": "t", "order_reference": "D. Del. 1:21-cv-01417", "nature": "money_judgment",
            "finding_ids": [fid], "counterparty": "Creditor Inc.", "amount": {"value_cents": 3_131_521_500},
            "judgment_date": "2024-05-20"}
    ue = {"component_id": "ue", "kind": "compensatory", "status": "awarded", "amount_cents": 3_131_521_500,
          "remittitur_cents": 2_310_000_000, "motion": "D.I. 613"}
    motion = {"motion_id": "D.I. 613", "kind": "rule_59a", "briefing_close": "2024-08-08", "decides": ["ue"]}
    for extra, match in (({"components": [{**ue, "amount_cents": 3_200_000_000}]}, "not in the cited quotes"),
                         ({"components": [{**ue, "statutory": "nc_24_5_b"}]}, "exactly one of"),
                         ({"components": [{"component_id": "pji", "kind": "prejudgment_interest", "status": "requested",
                                           "statutory": "no_such_rule"}]}, "names a rule"),
                         ({"components": [ue]}, "not listed in motions"),
                         ({"components": [ue], "motions": [{**motion, "briefing_close": "2024-08-09"}]},
                          "not in the cited quotes"),
                         ({"components": [ue], "motions": [{**motion, "motion_id": "D.I. 999"}]}, "docket reference"),
                         ({"commenced": "2021-10-05"}, "not in the cited quotes"),
                         ({"forum": "arbitration"}, "arbitration template")):
        with pytest.raises(T.ToolError, match=match):
            call(T.instantiate_dispute, ctx, {**base, **extra})
    comps = T._components([ue, {"component_id": "pji", "kind": "prejudgment_interest", "status": "requested",
                                "statutory": "nc_24_5_b"}], JUDGMENT_QUOTE, __import__("app.disputes.rules",
                          fromlist=["load_model"]).load_model())
    motions = T._motions([motion], JUDGMENT_QUOTE, None, comps)
    assert comps[0].remittitur_cents == 2_310_000_000 and comps[1].statutory == "nc_24_5_b"
    assert motions[0].briefing_close.isoformat() == "2024-08-08"  # a scheduled date may follow the review date
