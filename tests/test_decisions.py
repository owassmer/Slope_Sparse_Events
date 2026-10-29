"""The Decisions API transport (app/agent/decisions.py) and the adapter's use of it (app/agent/jev.py): the judge's
typed questions become Decisions questions with the same options and levels, its answers come back in the shape the
adapter reads, a refusal is an unknown (never a probability of zero), the answering model is the one the provider
names, a request is priced by what each of its tokens costs, and an attempt that may have been billed without a usage
is counted against the cap."""
import asyncio
from decimal import Decimal

import pytest
from typesafe_sdk import Choice, Noul, Score

from app.agent import decisions
from app.agent.decisions import _answer, _input, _question, returned_model
from app.agent.jev import MAX_RETRIES, JevAdapter
from app.config import judgment_provider
from app.disputes.forecast import answer_distribution
from app.domain.investigation import SemanticObservation


def test_questions_keep_their_options_and_levels():
    c = _question("q", Choice(instructions="Which?", criteria={"pay": "Pays.", "file": "Files."}).model_dump(mode="json"))
    assert c["choices"] == [{"value": "pay", "description": "Pays."}, {"value": "file", "description": "Files."}]
    p = _question("n", Noul(instructions="Does it?", criteria={"true": "It does.", "false": "It does not."})
                  .model_dump(mode="json"))
    assert p["type"] == "predicate" and "True: It does." in p["instructions"] and "False: It does not." in p["instructions"]
    s = _question("s", Score(instructions="How far?", criteria=["low", "middle", "high"]).model_dump(mode="json"))
    assert s["levels"] == [{"label": "low"}, {"label": "middle"}, {"label": "high"}]


def test_answers_in_the_adapters_shape():
    assert _answer({"type": "choice", "choice": "pay", "confidence": 0.4,
                    "probabilities": [{"value": "pay", "probability": 0.7}, {"value": "file", "probability": 0.3}]}) \
        == {"choice": "pay", "confidence": 0.4, "probabilities": {"pay": 0.7, "file": 0.3}}
    assert _answer({"type": "predicate", "probability": 0.07}) == {"noul": 0.07}
    # a score's levels by their index in the rubric, as the interpreter reads them (interpret.py)
    assert _answer({"type": "score", "score": 0.8, "confidence": 0.5,
                    "probabilities": [{"value": 0, "label": "low", "probability": 0.2},
                                      {"value": 1, "label": "high", "probability": 0.8}]})["probabilities"] \
        == {"0": 0.2, "1": 0.8}


def test_a_refusal_is_unknown_not_zero():
    ans = _answer({"type": "refusal", "name": "q"})
    o = SemanticObservation(observation_id="o", call_id="c", profile="event_forecast", question_id="q",
                            question_version="1", primitive="noul", answer=None, noul_value=ans.get("noul"))
    with pytest.raises(RuntimeError, match="no probability"):
        answer_distribution("k", ("yes", "no"), o)


def test_the_answering_model_is_the_one_the_provider_names():
    assert returned_model({"model": "gpt-6-luna"}, "gpt-6-luna") == "gpt-6-luna"
    assert returned_model({"model": "gpt-6-luna-2026-10-01"}, "gpt-6-luna") == "gpt-6-luna-2026-10-01"
    for data in ({}, {"model": None}, {"model": "not-gpt-6-luna-other"}, {"model": "gpt-6-lunar"}):
        with pytest.raises(decisions.DecisionsError):
            returned_model(data, "gpt-6-luna")


def test_the_shared_evidence_comes_first_and_alone_is_cached():
    state = {"question": {"text": "Q?"}, "case": {"company": "C"}, "historical_evidence": ["e"], "situation": {"x": 1}}
    [msg] = _input(state)
    first, second = msg["content"]
    assert first["prompt_cache_breakpoint"] == {"mode": "explicit"} and '"historical_evidence"' in first["text"]
    assert '"Q?"' not in first["text"] and second["data"] == {"question": {"text": "Q?"}, "situation": {"x": 1}}


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("SLOPE_JUDGMENT_PROVIDER", "decisions")
    monkeypatch.setenv("OPENAI_DECISIONS_API_KEY", "sk-test")
    return JevAdapter(run_id="t", provider=judgment_provider(), use_cache=False, spend_cap_usd="1.00")


def test_a_request_is_priced_by_what_each_token_costs(adapter):
    # the first real call: 16,037 input tokens, 15,739 of them written to the 30-minute cache
    usage = {"input_tokens": 16037, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 15739,
                                                            "cache_write_tokens_details": {"ttl_30m_tokens": 15739}}}
    assert adapter.cost(usage) == (298 * Decimal("0.10") + 15739 * Decimal("0.125")) / 10**6
    usage = {"input_tokens": 10000, "input_tokens_details": {"cached_tokens": 9000, "cache_write_tokens": 0}}
    assert adapter.cost(usage) == (1000 * Decimal("0.10") + 9000 * Decimal("0.01")) / 10**6


def test_attempts_that_may_have_been_billed_count_against_the_cap(adapter, monkeypatch):
    from app.agent.jev import _ATTEMPTS

    class Failing:
        async def system_one(self, **k):
            _ATTEMPTS.get()[0] += 1 + MAX_RETRIES  # every attempt went out and timed out
            raise decisions.DecisionsError("timeout")

    adapter.client = Failing()
    with pytest.raises(decisions.DecisionsError):
        asyncio.run(adapter.judge(profile="event_forecast", question_ids=["forecast_claim_settlement_offer"],
                                  state={"case": {}, "question": {"text": "Q?"}}))
    assert adapter.spent_usd > 0 and adapter.inflight_usd == 0


def test_concurrent_requests_count_only_their_own_attempts(adapter):
    """A request that succeeds first time is charged its usage alone, however many others are in flight."""
    from app.agent.jev import _ATTEMPTS

    class Quick:
        async def system_one(self, **k):
            _ATTEMPTS.get()[0] += 1
            await asyncio.sleep(0.01)
            return k["response_model"](model="gpt-6-luna", usage={"input_tokens": 100}, answers={})

    adapter.client = Quick()

    async def many():
        await asyncio.gather(*(adapter.judge(profile="event_forecast", question_ids=["forecast_claim_settlement_offer"],
                                             state={"case": {"n": i}, "question": {"text": "Q?"}}) for i in range(8)))

    asyncio.run(many())
    assert adapter.spent_usd == 8 * 100 * Decimal("0.10") / 10**6
