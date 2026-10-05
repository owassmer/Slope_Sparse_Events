"""Cloudflare Clef through the Workers AI REST API, with the TypeSafe client's `system_one` call shape.

Clef follows the System One request (model, state, typed questions) and answers with a probability per option.
Workers AI wraps the answer in {"result": ..., "success": ...}; this client returns the inner result so the Jev
adapter's caching, budget and observation code is unchanged. Usage carries tokens, not cost: the adapter prices it.
"""
from __future__ import annotations

import asyncio
import random

import httpx2

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class ClefError(RuntimeError):
    """Workers AI refused or failed the request after its retries."""


class ClefClient:
    def __init__(self, *, url: str, token: str, max_retries: int, http_client: httpx2.AsyncClient) -> None:
        self.url, self.max_retries, self.http = url, max_retries, http_client
        self.headers = {"Authorization": f"Bearer {token}"}

    async def system_one(self, *, state, questions, model, response_model):
        body = {"model": model, "state": state,
                "questions": {q: v.model_dump(mode="json") for q, v in questions.items()}}
        for attempt in range(self.max_retries + 1):
            try:
                r = await self.http.post(self.url, json=body, headers=self.headers)
            except (httpx2.TimeoutException, httpx2.TransportError) as error:
                if attempt == self.max_retries:
                    raise ClefError(f"Clef transport failure: {error!r}") from error
            else:
                if r.status_code < 400:
                    data = r.json()
                    if not data.get("success", True) or "result" not in data:
                        raise ClefError(f"Clef error: {data.get('errors')}")
                    result = data["result"]
                    result.setdefault("model", model)
                    return response_model(**result)
                if r.status_code not in RETRY_STATUS or attempt == self.max_retries:
                    raise ClefError(f"Clef HTTP {r.status_code}: {r.text[:500]}")
            await asyncio.sleep(min(30.0, 2 ** attempt + random.random()))
        raise ClefError("unreachable")
