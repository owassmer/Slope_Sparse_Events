"""OpenAI's Decisions API (`POST /v1/decisions`, model gpt-6-luna) with the TypeSafe client's `system_one` call shape.

The judge's request is the same as Clef's: a state (JSON) and typed questions, each answered with a probability per
option. Here the state is the shared input (an `input_json` part, the evidence every question reads) and each question
is a `choice` (a probability per option), `predicate` (the probability its condition holds) or `score` (a probability
per ordered level). The answer is returned in the System One shape the Jev adapter reads (`answers` by question name:
`choice` and `probabilities` by option, `noul`, or `score`), so its caching, budget and observation code is unchanged.
Usage carries tokens, not cost: the adapter prices them. A refusal is an answer with no probabilities (an unknown,
never a zero: `forecast.answer_distribution` raises on it).
"""
from __future__ import annotations

import asyncio
import random

import httpx2

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class DecisionsError(RuntimeError):
    """The Decisions API refused or failed the request after its retries."""


def _question(name: str, q: dict) -> dict:
    """A System One question (`Choice`/`Noul`/`Score`.model_dump) as a Decisions question."""
    kind, instructions, criteria = q["type"], q["instructions"], q.get("criteria") or {}
    if kind == "choice":
        return {"type": "choice", "name": name, "instructions": instructions,
                "choices": [{"value": value, "description": text} for value, text in criteria.items()]}
    if kind == "noul":  # criteria {'true': ..., 'false': ...}: what each truth value means
        meanings = "\n".join(f"{value.capitalize()}: {text}" for value, text in criteria.items())
        return {"type": "predicate", "name": name,
                "instructions": f"{instructions}\n\n{meanings}" if meanings else instructions}
    if kind == "score":
        return {"type": "score", "name": name, "instructions": instructions,
                "levels": [{"label": label, "description": text} for label, text in criteria.items()]}
    raise DecisionsError(f"No Decisions question type for {kind!r}")


def _answer(a: dict) -> dict:
    """A Decisions answer in the System One shape."""
    kind = a.get("type")
    if kind == "choice":
        return {"choice": a.get("choice"), "confidence": a.get("confidence"),
                "probabilities": {str(p["value"]): p["probability"] for p in a.get("probabilities") or ()}}
    if kind == "predicate":
        return {"noul": a.get("probability")}
    if kind == "score":
        return {"score": a.get("score"), "confidence": a.get("confidence"),
                "probabilities": {str(p.get("label", p.get("value"))): p["probability"]
                                  for p in a.get("probabilities") or ()}}
    return {"refusal": True}


class DecisionsClient:
    def __init__(self, *, url: str, token: str, max_retries: int, http_client: httpx2.AsyncClient) -> None:
        self.url, self.max_retries, self.http = url, max_retries, http_client
        self.headers = {"Authorization": f"Bearer {token}"}

    async def system_one(self, *, state, questions, model, response_model):
        body = {"model": model,
                "input": [{"role": "user", "content": [{"type": "input_json", "data": state}]}],
                "questions": [_question(name, q.model_dump(mode="json")) for name, q in questions.items()]}
        for attempt in range(self.max_retries + 1):
            try:
                r = await self.http.post(self.url, json=body, headers=self.headers)
            except (httpx2.TimeoutException, httpx2.TransportError) as error:
                if attempt == self.max_retries:
                    raise DecisionsError(f"Decisions transport failure: {error!r}") from error
            else:
                if r.status_code < 400:
                    data = r.json()
                    return response_model(model=data.get("model", model), id=data.get("id"),
                                          usage=data.get("usage"),
                                          answers={a.get("name"): _answer(a) for a in data.get("answers") or ()})
                exhausted = r.status_code == 429 and "credit_balance_exhausted" in r.text
                if exhausted or r.status_code not in RETRY_STATUS or attempt == self.max_retries:
                    raise DecisionsError(f"Decisions HTTP {r.status_code}: {r.text[:500]}")
            await asyncio.sleep(min(30.0, 2 ** attempt + random.random()))
        raise DecisionsError("unreachable")
