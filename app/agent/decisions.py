"""OpenAI's Decisions API (`POST /v1/decisions`, model gpt-6-luna) with the TypeSafe client's `system_one` call shape.

The judge's request is the same as Clef's: a state (JSON) and typed questions, each answered with a probability per
option. Here the state is the input every question reads and each question is a `choice` (a probability per option),
`predicate` (the probability its condition holds) or `score` (a probability per ordered level). The answer is returned
in the System One shape the Jev adapter reads (`answers` by question name: `choice` and `probabilities` by option,
`noul`, or `score` with `probabilities` by level index), so its caching, budget and observation code is unchanged. A
refusal is an answer with no probabilities: an unknown, never a zero (`forecast.answer_distribution` raises on it).

Prompt caching. The state's case-level parts (`SHARED`: the case, the standard, the record items and the evidence)
are the same for every question about the same decision, and are most of its tokens; the question and its situation
are its own. The shared parts go first, as one text part marked as the only cache boundary, and the question's own
parts after it: a later question about the same decision reads the evidence from the cache (at a tenth of the input
price) instead of sending it again, and nothing past the evidence is written to the cache (a write costs more than
the input it holds). The judge reads the same keys and values either way.

One question per request. The API takes up to 400 questions about one shared input, but a batch can share only the
evidence: each question's own situation would have to go in its instructions. Asked so, real questions were answered
differently (13 of the 14 May questions, 29 October: a bid-compliance question 0.52 alone, 0.13 with its situation in
its instructions; a batch of one answered exactly as a batch of six, so the questions did not read one another: the
placement of the facts moved the judgment). The judge is asked as the question standard is built, the facts as the
state and the question as the question, so each question is its own request; the evidence it shares with the other
questions about its decision is read from the cache.
"""
from __future__ import annotations

import asyncio
import json
import random
import re

import httpx2

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
# the state's parts that are the same for every question about one decision (state14.build, Forecaster.built)
SHARED = ("case", "standard", "record_items", "historical_evidence", "party_assertions", "court_findings", "evidence")


class DecisionsError(RuntimeError):
    """The Decisions API refused or failed the request after its retries."""


def _question(name: str, q: dict) -> dict:
    """A System One question (`Choice`/`Noul`/`Score`.model_dump) as a Decisions question."""
    kind, instructions, criteria = q["type"], q["instructions"], q.get("criteria")
    if kind == "choice":  # criteria {value: meaning}
        return {"type": "choice", "name": name, "instructions": instructions,
                "choices": [{"value": value, "description": text} for value, text in criteria.items()]}
    if kind == "noul":  # criteria {'true': ..., 'false': ...}: what each truth value means
        meanings = "\n".join(f"{value.capitalize()}: {text}" for value, text in (criteria or {}).items())
        return {"type": "predicate", "name": name,
                "instructions": f"{instructions}\n\n{meanings}" if meanings else instructions}
    if kind == "score":  # criteria: the ordered levels, lowest first (a level's text is its label)
        return {"type": "score", "name": name, "instructions": instructions,
                "levels": [{"label": level if isinstance(level, str) else json.dumps(level)} for level in criteria]}
    raise DecisionsError(f"No Decisions question type for {kind!r}")


def _answer(a: dict) -> dict:
    """A Decisions answer in the System One shape (a score's levels by their index, as the rubric orders them)."""
    kind = a.get("type")
    if kind == "choice":
        return {"choice": a.get("choice"), "confidence": a.get("confidence"),
                "probabilities": {str(p["value"]): p["probability"] for p in a.get("probabilities") or ()}}
    if kind == "predicate":
        return {"noul": a.get("probability")}
    if kind == "score":
        return {"score": a.get("score"), "confidence": a.get("confidence"),
                "probabilities": {str(p["value"]): p["probability"] for p in a.get("probabilities") or ()}}
    return {"refusal": True}


def _input(state: dict) -> list[dict]:
    """The state as the request's input: its shared parts first, as one text part closing the cache boundary, then
    its own parts. A state without shared parts is one JSON part, with no cache boundary."""
    shared, own = split_state(state)
    if not shared:
        return [{"role": "user", "content": [{"type": "input_json", "data": own}]}]
    content = [{"type": "input_text", "text": json.dumps(shared, ensure_ascii=False, default=str),
                "prompt_cache_breakpoint": {"mode": "explicit"}}]
    if own:
        content.append({"type": "input_json", "data": own})
    return [{"role": "user", "content": content}]


def returned_model(data: dict, model: str) -> str:
    """The model the provider says answered: the requested one or a dated snapshot of it (`<model>-YYYY-MM-DD`), never
    assumed."""
    got = data.get("model")
    if not isinstance(got, str) or not (got == model or re.fullmatch(re.escape(model) + r"-\d{4}-\d{2}-\d{2}", got)):
        raise DecisionsError(f"Decisions answered as {got!r}, not {model!r}")
    return got


def split_state(state: dict) -> tuple[dict, dict]:
    """A state's shared parts and its own."""
    shared = {k: state[k] for k in SHARED if k in state}
    return shared, {k: v for k, v in state.items() if k not in shared}


class DecisionsClient:
    def __init__(self, *, url: str, token: str, max_retries: int, http_client: httpx2.AsyncClient) -> None:
        self.url, self.max_retries, self.http = url, max_retries, http_client
        self.headers = {"Authorization": f"Bearer {token}"}

    async def system_one(self, *, state, questions, model, response_model):
        body = {"model": model, "input": _input(state), "prompt_cache_options": {"mode": "explicit"},
                "questions": [_question(name, q.model_dump(mode="json")) for name, q in questions.items()]}
        return await self._post(body, model, response_model)

    async def _post(self, body, model, response_model):
        for attempt in range(self.max_retries + 1):
            try:
                r = await self.http.post(self.url, json=body, headers=self.headers)
            except (httpx2.TimeoutException, httpx2.TransportError) as error:
                if attempt == self.max_retries:
                    raise DecisionsError(f"Decisions transport failure: {error!r}") from error
            else:
                if r.status_code < 400:
                    data = r.json()
                    return response_model(model=returned_model(data, model), id=data.get("id"),
                                          usage=data.get("usage"),
                                          answers={a.get("name"): _answer(a) for a in data.get("answers") or ()})
                exhausted = r.status_code == 429 and "credit_balance_exhausted" in r.text
                if exhausted or r.status_code not in RETRY_STATUS or attempt == self.max_retries:
                    raise DecisionsError(f"Decisions HTTP {r.status_code}: {r.text[:500]}")
            await asyncio.sleep(min(30.0, 2 ** attempt + random.random()))
        raise DecisionsError("unreachable")
