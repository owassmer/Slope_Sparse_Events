"""Jev adapter: one physical request -> one JevCallRecord plus one SemanticObservation per question.

Jev answers narrow semantic questions built from the versioned registry (Choice or Noul). Its
probabilities and confidence describe its own judgment; they are never event probabilities, weights or
dollar adjustments. The host, not the agent, builds the state and chooses the questions (see
jev_profiles.py).

Budget: the per-run ceiling counts physical HTTP attempts, including SDK retries, observed by a
request hook on the client. Before dispatch a request needs headroom for its worst case (1 + max_retries
attempts, and a conservative input-cost estimate); afterwards only what it actually used is kept, and
spend is reconciled against the provider-reported cost when present. Cache: identical requests (model,
built question text and criteria, state) are answered from var/jev_cache, marked as cache hits and keep
their original timestamp.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx2
from pydantic import BaseModel, ConfigDict
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, RetryPolicy

from app.config import (
    VAR,
    ConfigurationError,
    JevProvider,
    agent_config,
    jev_credential,
    jev_provider,
    question_registry,
)
from app.domain.investigation import JevCallRecord, SemanticObservation

EXPECTED_BUILD_MARKER = "jev-1.13"
MAX_RETRIES = 2
NOUL_THRESHOLD = 0.5  # code-owned; also used for screen routing (checked against evals/)
CACHE_DIR = VAR / "jev_cache"


class JevBudgetExceeded(RuntimeError):
    """Attempt ceiling or spend cap reached: the investigation becomes INCOMPLETE_REVIEW, never a decline."""


class _Raw(BaseModel):
    """Permissive response model so provider-added fields (e.g. OpenRouter cost) are preserved."""

    model_config = ConfigDict(extra="allow")


def canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def registry_question(question_id: str) -> dict:
    for q in question_registry()["questions"]:
        if q["id"] == question_id:
            return q
    raise KeyError(f"Unknown Jev question {question_id!r}")


def build_question(entry: dict) -> Choice | Noul:
    """Registry mapping: global rules prefixed to the question's instructions; primitive from the entry."""
    rules = "\n".join(f"- {r}" for r in question_registry()["global_rules"])
    instructions = f"Global rules:\n{rules}\n\nQuestion:\n{entry['prompt']['instructions']}"
    if entry["primitive"] == "noul":
        return Noul(instructions=instructions, criteria=entry["prompt"]["criteria"])
    return Choice(instructions=instructions, criteria=entry["prompt"]["criteria"])


class JevAdapter:
    def __init__(self, *, run_id: str, provider: JevProvider | None = None,
                 new_id: Callable[[str], str] | None = None, use_cache: bool = True,
                 max_attempts: int | None = None, spend_cap_usd: str | None = None) -> None:
        cfg = agent_config()
        self.provider = provider or jev_provider()
        self.physical_attempts = 0

        async def count_attempt(_request) -> None:
            self.physical_attempts += 1

        self.client = AsyncTypeSafeClient(
            api_key=jev_credential(self.provider), base_url=self.provider.base_url,
            retry=RetryPolicy(max_retries=MAX_RETRIES),
            http_client=httpx2.AsyncClient(timeout=30.0, event_hooks={"request": [count_attempt]}))
        self.run_id = run_id
        self.new_id = new_id or (lambda prefix: f"{prefix}_{uuid.uuid4().hex[:10]}")
        self.use_cache = use_cache
        self.max_attempts = max_attempts or cfg["budgets"]["jev_max_physical_attempts_including_retries"]
        self.spend_cap = Decimal(spend_cap_usd or cfg["runtime"]["jev"]["spend_cap_usd_per_run"])
        self.price_per_token = Decimal(cfg["runtime"]["jev"]["provider_price_usd_per_million_input_tokens_at_design"]) / 10**6
        self.inflight_attempts = 0
        self.inflight_usd = Decimal(0)
        self.spent_usd = Decimal(0)
        self.requests = 0
        self.cache_hits = 0

    def _reserve(self, payload_chars: int) -> tuple[int, Decimal]:
        """Worst-case headroom check before dispatch; released after the request completes."""
        attempts = 1 + MAX_RETRIES
        estimate = Decimal(payload_chars // 3 + 1) * self.price_per_token * attempts  # ~3 chars/token, conservative
        if self.physical_attempts + self.inflight_attempts + attempts > self.max_attempts:
            raise JevBudgetExceeded(f"Jev attempt ceiling reached ({self.max_attempts} physical attempts)")
        if self.spent_usd + self.inflight_usd + estimate > self.spend_cap:
            raise JevBudgetExceeded(f"Jev spend cap ${self.spend_cap} would be exceeded")
        self.inflight_attempts += attempts
        self.inflight_usd += estimate
        return attempts, estimate

    async def judge(self, *, profile: str, question_ids: list[str], state: dict, subject_ids: tuple[str, ...] = (),
                    source_content_hashes: tuple[str, ...] = (),
                    criteria: dict[str, dict[str, str]] | None = None) -> tuple[JevCallRecord, list[SemanticObservation]]:
        """Ask independent questions about one state in a single request. `criteria` supplies host-built answer
        options for questions whose registry entry says so (e.g. the branches of a dispute decision node)."""
        entries = {qid: registry_question(qid) for qid in question_ids}
        for qid, crit in (criteria or {}).items():
            if not entries[qid]["prompt"].get("criteria_from_host"):
                raise ConfigurationError(f"{qid} does not take host-built criteria")
            entries[qid] = {**entries[qid], "prompt": {**entries[qid]["prompt"], "criteria": crit}}
        registry_version = question_registry()["registry_version"]
        questions = {qid: build_question(e) for qid, e in entries.items()}
        # Key on the exact built question text and criteria, so an edit without a version bump cannot hit.
        cache_key = canonical_sha256({"model": self.provider.model, "state": state,
                                      "questions": {q: v.model_dump(mode="json") for q, v in questions.items()}})
        cache_file = CACHE_DIR / f"{cache_key}.json"
        attempts_used, cache_hit = 0, False
        if self.use_cache and cache_file.exists():
            cached = json.loads(cache_file.read_text())
            raw, now = cached["raw"], cached["created_at"]
            cache_hit = True
            self.cache_hits += 1
        else:
            reserved, estimate = self._reserve(
                len(json.dumps(state)) + sum(len(q.model_dump_json()) for q in questions.values()))
            before = self.physical_attempts
            try:
                self.requests += 1
                raw = (await self.client.system_one(state=state, questions=questions, model=self.provider.model,
                                                    response_model=_Raw)).model_dump(mode="json")
            finally:
                self.inflight_attempts -= reserved
                self.inflight_usd -= estimate
                attempts_used = self.physical_attempts - before  # approximate under concurrency; totals are exact
            cost = (raw.get("usage") or {}).get("cost")
            self.spent_usd += Decimal(str(cost)) if cost is not None else estimate
            now = datetime.now(UTC).isoformat()
            returned = raw.get("model")
            if not returned or EXPECTED_BUILD_MARKER not in str(returned):
                raise ConfigurationError(f"Unexpected Jev model {returned!r}; pinned {self.provider.pinned_build}")
            if self.use_cache:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps({"raw": raw, "created_at": now}))

        call = JevCallRecord(
            call_id=self.new_id("jev"), run_id=self.run_id, profile=profile, subject_ids=subject_ids,
            question_ids=tuple(question_ids), registry_version=registry_version, requested_model=self.provider.model,
            returned_model=raw.get("model"), state_sha256=canonical_sha256(state),
            source_content_hashes=source_content_hashes, attempts_used=attempts_used, usage=raw.get("usage"),
            raw_response=raw, created_at=now, cache_hit=cache_hit)
        answers = raw.get("answers") or {}
        observations = []
        for qid in question_ids:
            ans, entry = answers.get(qid) or {}, entries[qid]
            if entry["primitive"] == "noul":
                value = ans.get("noul")
                observations.append(SemanticObservation(
                    observation_id=self.new_id("obs"), call_id=call.call_id, profile=profile, question_id=qid,
                    question_version=entry["version"], primitive="noul",
                    answer=None if value is None else value >= NOUL_THRESHOLD, noul_value=value, subject_ids=subject_ids))
            else:
                observations.append(SemanticObservation(
                    observation_id=self.new_id("obs"), call_id=call.call_id, profile=profile, question_id=qid,
                    question_version=entry["version"], primitive="choice", answer=ans.get("choice"),
                    probabilities=ans.get("probabilities"), confidence=ans.get("confidence"), subject_ids=subject_ids))
        return call, observations

    def usage_summary(self) -> dict[str, Any]:
        return {"provider": self.provider.name, "requests": self.requests, "cache_hits": self.cache_hits,
                "physical_attempts": self.physical_attempts, "attempt_ceiling": self.max_attempts,
                "spent_usd": str(self.spent_usd), "spend_cap_usd": str(self.spend_cap)}
