"""Semantic layer (step 4a): isolation of Jev state, verbatim provenance, authority boundaries, the
event log, cache and budget accounting, and the agent-only arm. Jev itself is stubbed; no network."""

import asyncio
import json
from pathlib import Path

import pytest

import app.agent.jev as jev_module
from app.agent.jev import JevAdapter, JevBudgetExceeded
from app.agent.jev_profiles import Semantics, is_ambiguous, route
from app.agent.run_store import LockedRunError, RunStore
from app.config import CONTRACTS, allowed_tools, question_registry
from app.domain.investigation import (
    AtomicFinding,
    DecisionDependency,
    EconomicEffectProposal,
    EvidenceCandidate,
    SemanticObservation,
    validate_effect,
)
from app.evidence import snapshot
from app.evidence.spans import SpanError, locate, verify
from app.evidence.store import EvidenceStore

SNAP = "synergy_20240813"


class FakeJevClient:
    """Answers every question with a fixed, valid response and records each state it received.

    Counts one physical attempt per request, as the real client's HTTP request hook does."""

    def __init__(self, adapter=None, fail_on: str | None = None) -> None:
        self.states: list[dict] = []
        self.adapter, self.fail_on = adapter, fail_on

    async def system_one(self, *, state, questions, model, response_model):
        if self.adapter is not None:
            self.adapter.physical_attempts += 1
        if self.fail_on == "first" and len(self.states) == 0 and not getattr(self, "failed", False):
            self.failed = True
            raise RuntimeError("provider unavailable")
        self.states.append(state)
        answers = {}
        for qid, q in questions.items():
            if q.type == "noul":
                answers[qid] = {"type": "noul", "noul": 0.9}
            else:
                first = next(iter(q.criteria))
                answers[qid] = {"type": "choice", "choice": first, "confidence": 0.8,
                                "probabilities": {k: (0.8 if k == first else 0.2 / (len(q.criteria) - 1)) for k in q.criteria}}
        return response_model.model_validate({"model": "typesafe/jev-1.13-test", "answers": answers,
                                              "usage": {"input_tokens": 100, "output_tokens": 5}})


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    monkeypatch.setattr(jev_module, "jev_credential", lambda provider: "test-key")
    monkeypatch.setattr(jev_module, "CACHE_DIR", tmp_path / "cache")

    def make(run: RunStore | None = None, **kw) -> JevAdapter:
        fail_on = kw.pop("fail_on", None)
        a = JevAdapter(run_id=run.run_id if run else "t", new_id=run.new_id if run else None, **kw)
        a.client = FakeJevClient(a, fail_on)
        return a
    return make


@pytest.fixture(scope="module")
def evidence(tmp_path_factory):
    out = tmp_path_factory.mktemp("evidence")
    snapshot.build_snapshot(SNAP, out_dir=out)
    return EvidenceStore(SNAP, out / f"{SNAP}.sqlite")


def forbidden_strings() -> list[str]:
    private = json.loads((CONTRACTS / "case_eval_private.json").read_text())
    return ["Vitabest Nutrition", "Delayed Draw", "expected_findings", "case_eval_private",
            *private["cases"]["synergy_chc_2024"]["outcome_source_ids"]]


def test_jev_state_is_built_only_from_admissible_run_objects(adapter, evidence, tmp_path):
    run = RunStore("r1", root=tmp_path, meta={"arm": "agent_plus_jev"})
    jev = adapter(run)
    sem = Semantics(run, evidence, jev)
    dep = run.put("dependency_recorded", DecisionDependency(
        dependency_id=run.new_id("dep"), question="What settlement payments remain in 2024?", affects="settlement outflows"))
    hits = evidence.search("required to make future payments settlement", limit=4)
    cands = [EvidenceCandidate(candidate_id=run.new_id("cand"), dependency_id=dep.dependency_id, search_id="s1", query="q",
                               rank=i, item_id=h["id"], kind=h["kind"], source_id=h["source_id"],
                               heading_path=tuple(h["heading_path"]), snippet=h["snippet"]) for i, h in enumerate(hits)]
    screened = asyncio.run(sem.screen(dep, cands))
    assert {c.candidate_id for c in screened} == {c.candidate_id for c in cands}  # screening never hides a candidate
    table = next(c for c in cands if c.kind == "table")
    asyncio.run(sem.interpret(item_id=table.item_id, claim="The Company must make future settlement payments.",
                              target="Synergy CHC Corp.", subject_kind="obligation", subject="a settlement loan"))
    span = locate(evidence, table.item_id, "The Company is required to make future payments as follows:")
    finding = AtomicFinding(finding_id=run.new_id("fnd"), dependency_id=dep.dependency_id,
                            proposition="The Company reports a schedule of future settlement loan payments.",
                            target="Synergy CHC Corp.", spans=(span,))
    asyncio.run(sem.check_finding(finding))
    asyncio.run(sem.relate(finding, finding, "The Company reports future settlement loan payments."))

    corpus = " ".join(evidence.read_section(s["section_id"])["text"]
                      for s in [evidence.read(c.item_id) for c in cands] if "section_id" in s or "text" in s)
    for state in jev.client.states:
        text = json.dumps(state, ensure_ascii=False)
        assert not any(f in text for f in forbidden_strings())
        passage = state.get("passage")
        if isinstance(passage, dict):
            core = passage["text"].strip("… ").split("\n")[0][:80]
            assert core in corpus or core in json.dumps([evidence.read(c.item_id) for c in cands], ensure_ascii=False)
    kinds = [e.kind for e in run.events]
    assert kinds.count("jev_call") == len(jev.client.states)
    assert all(o.call_id in run.graph["jev_calls"] for o in run.graph["observations"].values())


def test_a_failed_screen_never_hides_candidates(adapter, evidence, tmp_path):
    run = RunStore("r3", root=tmp_path, meta={"arm": "agent_plus_jev"})
    hits = evidence.search("required to make future payments settlement", limit=4)
    sem = Semantics(run, evidence, adapter(run, fail_on="first"))
    dep = run.put("dependency_recorded", DecisionDependency(dependency_id=run.new_id("dep"), question="q", affects="a"))
    cands = [EvidenceCandidate(candidate_id=run.new_id("cand"), dependency_id=dep.dependency_id, search_id="search_001",
                               query="q", rank=i, item_id=h["id"], kind=h["kind"], source_id=h["source_id"],
                               heading_path=tuple(h["heading_path"]), snippet=h["snippet"]) for i, h in enumerate(hits)]
    out = asyncio.run(sem.screen(dep, cands))
    assert len(out) == len(cands)
    failed = [c for c in out if c.screen.route == "unscreened"]
    assert len(failed) == 1 and "provider unavailable" in failed[0].screen.error
    assert sem.last_screen_errors and len(run.graph["candidates"]) == len(cands)
    assert route({"gap_relevance": 0.9, "usable_evidence": 0.9}) == "unscreened"  # missing signal is not context-only
    assert route({"gap_relevance": 0.9, "usable_evidence": 0.9, "premise_conflict": 0.1,
                  "instruction_like_text": 0.9}) == "instruction_flagged"


def test_flat_judgments_are_flagged_ambiguous():
    flat = SemanticObservation(observation_id="o", call_id="c", profile="claim_interpretation", question_id="entity_scope",
                               question_version="3.0.1", primitive="choice", answer="target",
                               probabilities={"target": 0.53, "different": 0.38, "unknown": 0.09})
    clear = flat.model_copy(update={"probabilities": {"target": 0.9, "different": 0.07, "unknown": 0.03}})
    assert is_ambiguous(flat) and not is_ambiguous(clear)


def test_spans_must_be_verbatim(evidence):
    hit = next(h for h in evidence.search("paid in full the settlement L.O.D.C.") if h["kind"] == "section")
    span = locate(evidence, hit["id"], "the Company paid in full the settlement to L.O.D.C Group, Ltd.")
    verify(evidence, span)
    with pytest.raises(SpanError):
        locate(evidence, hit["id"], "the Company paid in full the settlement to HVL, LLC")
    with pytest.raises(SpanError):
        verify(evidence, span.model_copy(update={"start": span.start + 1}))


def test_jev_output_cannot_create_cash(evidence):
    with pytest.raises(ValueError):  # a finding must rest on at least one verbatim span
        AtomicFinding(finding_id="f0", dependency_id="d", proposition="p", target="t", spans=())
    hit = next(h for h in evidence.search("paid in full the settlement L.O.D.C.") if h["kind"] == "section")
    span = locate(evidence, hit["id"], "the Company paid in full the settlement to L.O.D.C Group, Ltd.")
    findings = {"f1": AtomicFinding(finding_id="f1", dependency_id="d", proposition="p", target="t", spans=(span,)),
                "f2": AtomicFinding(finding_id="f2", dependency_id="d", proposition="p", target="t", status="accepted",
                                    spans=(span.model_copy(update={"start": span.start + 3}),))}
    gain = EconomicEffectProposal(effect_id="e1", finding_ids=("f1", "f2"), mechanism="noncash_normalization",
                                  target="FY2023 cost of sales", cash_direction="outflow", baseline_treatment="normalization_only")
    problems = validate_effect(gain, findings, verify_span=lambda sp: verify(evidence, sp))
    assert any("not accepted" in p for p in problems) and any("cannot create a cash" in p for p in problems)
    assert any("does not match its quote" in p for p in problems)
    # The semantic modules have no path to the cash ledger.
    for mod in ("jev.py", "jev_profiles.py"):
        src = (Path(jev_module.__file__).parent / mod).read_text()
        assert "app.finance" not in src


def test_event_log_is_hash_chained_replayable_and_lockable(tmp_path):
    run = RunStore("r2", root=tmp_path, meta={"arm": "agent_plus_jev"})
    run.put("dependency_recorded", DecisionDependency(dependency_id=run.new_id("dep"), question="q", affects="a"))
    replay = RunStore("r2", root=tmp_path)
    assert replay.head == run.head and "dep_001" in replay.graph["dependencies"]
    run.append("search", object_ids=("search_007",), payload={"query": "q"})
    assert RunStore("r2", root=tmp_path).new_id("search") == "search_008"  # log-only IDs survive replay
    stale = RunStore("r2", root=tmp_path)
    run.lock({"summary": "test"})
    with pytest.raises(LockedRunError):
        run.put("dependency_recorded", DecisionDependency(dependency_id="dep_002", question="q", affects="a"))
    with pytest.raises(LockedRunError):  # another instance locked it
        stale.append("search", payload={})
    events = tmp_path / "r2" / "events.jsonl"
    full = events.read_text()
    events.write_text("\n".join(full.splitlines()[:-1]) + "\n")  # drop the final event of a locked run
    with pytest.raises(ValueError, match="does not match its packet"):
        RunStore("r2", root=tmp_path)
    events.write_text(full)
    lines = (tmp_path / "r2" / "events.jsonl").read_text().splitlines()
    lines[1] = lines[1].replace('"question":"q"', '"question":"tampered"')
    (tmp_path / "r2" / "events.jsonl").write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match="chain broken"):
        RunStore("r2", root=tmp_path)


def test_cache_and_attempt_accounting(adapter):
    a = adapter()
    state = {"target": "T", "source": {}, "passage": {"text": "x"}, "claim": "c"}
    call1, _ = asyncio.run(a.judge(profile="claim_interpretation", question_ids=["claim_posture"], state=state))
    call2, _ = asyncio.run(a.judge(profile="claim_interpretation", question_ids=["claim_posture"], state=state))
    assert (call1.cache_hit, call2.cache_hit) == (False, True) and a.requests == 1
    assert call1.attempts_used == 1 and call2.attempts_used == 0  # actual attempts, not the worst-case reservation
    b = adapter(use_cache=False)
    b.max_attempts = 4  # worst case 3 per request; unused reservations are released
    for _ in range(2):
        asyncio.run(b.judge(profile="claim_interpretation", question_ids=["claim_posture"], state=state))
    assert b.physical_attempts == 2 and b.inflight_attempts == 0
    with pytest.raises(JevBudgetExceeded):
        asyncio.run(b.judge(profile="claim_interpretation", question_ids=["claim_posture"], state=state))


def test_agent_only_arm_has_no_jev_surface():
    assert "mcp__credit__judge" in allowed_tools("agent_plus_jev")
    only = allowed_tools("agent_only")
    assert not any(n in t for t in only for n in ("judge", "inventory", "escalate_effect", "reconciliation"))
    assert len(only) == len(allowed_tools()) - 4


def test_registry_questions_are_generic_and_well_formed():
    reg = question_registry()
    text = json.dumps(reg)
    assert not any(f in text for f in ["Synergy", "Barfresh", "HVL", "Atrium", "L.O.D.C", "Vitabest", "Schreiber"])
    ids = {q["id"] for q in reg["questions"]}
    for profile in reg["profiles"].values():
        assert set(profile["questions"]) <= ids
    for q in reg["questions"]:
        assert q["primitive"] in ("noul", "choice")
        if q["primitive"] == "noul":
            assert set(q["prompt"]["criteria"]) == {"true", "false"}
