"""The Clef client sends the System One body and returns Workers AI's inner result; the adapter prices tokens."""
import asyncio
import json

import httpx2
from typesafe_sdk import Choice, Noul

from app.agent.clef import ClefClient, ClefError
from app.agent.jev import _Raw


def _client(handler, retries=2):
    return ClefClient(url="https://x/ai/run/@cf/cloudflare/clef", token="t", max_retries=retries,
                      http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))


def test_body_and_result():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content), auth=request.headers["Authorization"])
        return httpx2.Response(200, json={"success": True, "errors": [], "result": {
            "model": "clef", "answers": {"q": {"choice": "a", "probabilities": {"a": 0.7, "b": 0.3}}},
            "usage": {"input_tokens": 100}}})
    out = asyncio.run(_client(handler).system_one(
        state={"s": 1}, questions={"q": Choice(instructions="x", criteria={"a": "A", "b": "B"}),
                                   "n": Noul(instructions="y")}, model="clef", response_model=_Raw))
    assert seen["model"] == "clef" and seen["state"] == {"s": 1} and seen["auth"] == "Bearer t"
    assert seen["questions"]["q"] == {"type": "choice", "instructions": "x", "criteria": {"a": "A", "b": "B"}}
    assert out.model_dump()["answers"]["q"]["probabilities"] == {"a": 0.7, "b": 0.3}


def test_retries_then_fails():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx2.Response(429, text="slow down")
    try:
        asyncio.run(_client(handler, retries=1).system_one(state="s", questions={}, model="clef",
                                                           response_model=_Raw))
    except ClefError:
        pass
    else:
        raise AssertionError("expected ClefError")
    assert len(calls) == 2
