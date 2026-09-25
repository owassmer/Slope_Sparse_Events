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


class FakeJev:
    """First criterion wins with 0.8 unless `flat` names a question to answer ambiguously."""

    def __init__(self, adapter, flat=()):
        self.adapter, self.flat = adapter, set(flat)

    async def system_one(self, *, state, questions, model, response_model):
        self.adapter.physical_attempts += 1
        answers = {}
        for qid, q in questions.items():
            if q.type == "noul":
                answers[qid] = {"type": "noul", "noul": 0.9}
            else:
                keys = list(q.criteria)
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

    def make(arm="agent_plus_jev", flat=()):
        run = RunStore(f"run-{arm}-{len(flat)}", root=tmp_path, meta={"arm": arm})
        sem = None
        if arm == "agent_plus_jev":
            a = JevAdapter(run_id=run.run_id, new_id=run.new_id)
            a.client = FakeJev(a, flat)
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
    item = next(r["item_id"] for r in res["results"] if r["kind"] == "section" and "#s02" in r["item_id"])
    quote = "The Company is required to make future payments as follows:"
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
                                       "cash_direction": "outflow", "baseline_treatment": "normalization_only"})
    assert bad["status"] == "rejected"
    eff = call(T.propose_effect, ctx, {"finding_ids": [prop["finding_id"]], "mechanism": "settlement_payment_timing",
                                       "target": "supplier settlement loan", "cash_direction": "outflow",
                                       "baseline_treatment": "already_in_baseline_reclassify_timing",
                                       "parameters": [{"name": "remaining_current_year_bucket_cents", "description": "2024 bucket at June 30",
                                                       "status": "known", "value_cents": 200_000_000, "finding_ids": [prop["finding_id"]]},
                                                      {"name": "paid_since_june_30", "description": "payments since June 30", "status": "unknown"}],
                                       "double_count_guard": "Existing liability; scheduled once", "model_consequence": "Dated outflows"})
    assert eff["status"] == "validated" and eff["baseline_overlap_warning"]
    unknown_share = call(T.run_sensitivity, ctx, {"effect_ids": [eff["effect_id"]]})
    assert all(r["required_net_cash_cents"] is None for r in unknown_share["scenarios"])  # unknown stays unknown
    s = call(T.run_sensitivity, ctx, {"effect_ids": [eff["effect_id"]], "unavailable_opening_cash_cents": 0})
    by = {r["share_of_bucket_paid_since_measurement"]: r["required_net_cash_cents"] for r in s["scenarios"]}
    assert by == {"0": 20_000_000, "0.5": 0, "1": 0}  # $2.0m bucket + $0.2m reserve - $2.0m reported cash


def test_agent_only_arm_has_no_screen_or_judge(make_ctx):
    ctx = make_ctx(arm="agent_only")
    dep = call(T.record_dependency, ctx, {"question": "q", "target": "Synergy CHC Corp. — settlement loan", "affects": "a"})["dependency_id"]
    res = call(T.search_evidence, ctx, {"dependency_id": dep, "query": "settlement"})
    assert res["results"] and not any("screen" in r for r in res["results"])
    with pytest.raises(T.ToolError):
        call(T.judge, ctx, {"profile": "claim_interpretation"})
