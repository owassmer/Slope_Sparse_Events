"""Jev adapter: one physical request -> one JevCallRecord plus one SemanticObservation per question.

Jev answers narrow semantic questions built from the versioned registry (Choice, Noul or Score). Its
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
import os
import uuid
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx2
from pydantic import BaseModel, ConfigDict
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, RetryPolicy, Score

from app.config import (
    JUDGMENT_PROVIDERS,
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
# While a caller holds a list here, every exchange is appended to it: the request as the SDK receives it (model,
# built questions, state) and Jev's raw response. `slope analyze` writes the run's exchanges beside the run.
EXCHANGE_LOG: list[dict] | None = None
# per request: its physical attempts (set by the request, counted by the HTTP hook in the same task)
_ATTEMPTS: ContextVar[list[int] | None] = ContextVar("jev_attempts", default=None)


def cache_only() -> bool:
    """SLOPE_JEV_CACHE_ONLY=1: answer only from saved responses; a request not already answered is refused."""
    return os.environ.get("SLOPE_JEV_CACHE_ONLY") == "1"


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


def build_question(entry: dict, fill: Callable[[str], str] | None = None) -> Choice | Noul | Score:
    """Registry mapping: the profile's rules (the global rules unless the profile states its own) prefixed to the
    question's instructions; primitive from the entry. `fill`: applied to the instructions and every criterion (the
    parties' placeholders filled with their roles: `roles.fill`)."""
    reg = question_registry()
    own = reg["profiles"].get(entry.get("profile", ""), {}).get("rules")
    rules = "\n".join(f"- {r}" for r in (own if own is not None else reg["global_rules"]))
    instruction = entry['prompt']['instructions']
    if entry.get('profile') == 'event_forecast' and entry['prompt'].get('criteria_from_host'):
        instruction = ('Answer the question in state.question.text. Its state.question.answers gives the '
                       'available actions and their dated meanings; use only the answer options in the criteria.')
    instructions = f"{'Rules' if own is not None else 'Global rules'}:\n{rules}\n\nQuestion:\n{instruction}"
    criteria = entry["prompt"]["criteria"]
    if fill is not None:
        instructions = fill(instructions)
        criteria = ({k: fill(v) for k, v in criteria.items()} if isinstance(criteria, dict)
                    else [fill(c) if isinstance(c, str) else c for c in criteria])
    if entry["primitive"] == "noul":
        return Noul(instructions=instructions, criteria=criteria)
    if entry["primitive"] == "score":
        return Score(instructions=instructions, criteria=criteria)
    return Choice(instructions=instructions, criteria=criteria)


class JevAdapter:
    def __init__(self, *, run_id: str, provider: JevProvider | None = None,
                 new_id: Callable[[str], str] | None = None, use_cache: bool = True,
                 max_attempts: int | None = None, spend_cap_usd: str | None = None) -> None:
        cfg = agent_config()
        self.provider = provider or jev_provider()
        self.physical_attempts = 0

        async def count_attempt(_request) -> None:
            self.physical_attempts += 1
            own = _ATTEMPTS.get()
            if own is not None:  # the request's own count (requests run concurrently: the total is everyone's)
                own[0] += 1

        http = httpx2.AsyncClient(timeout=60.0, event_hooks={"request": [count_attempt]})
        if self.provider.name == "clef":
            from app.agent.clef import ClefClient
            self.client = ClefClient(url=self.provider.base_url, token=jev_credential(self.provider),
                                     max_retries=MAX_RETRIES, http_client=http)
        elif self.provider.name == "decisions":
            from app.agent.decisions import DecisionsClient
            self.client = DecisionsClient(url=self.provider.base_url, token=jev_credential(self.provider),
                                          max_retries=MAX_RETRIES, http_client=http,
                                          tokens_per_minute=JUDGMENT_PROVIDERS["decisions"].get("tokens_per_minute"))
        else:
            self.client = AsyncTypeSafeClient(
                api_key=jev_credential(self.provider), base_url=self.provider.base_url,
                retry=RetryPolicy(max_retries=MAX_RETRIES), http_client=http)
        self.run_id = run_id
        self.new_id = new_id or (lambda prefix: f"{prefix}_{uuid.uuid4().hex[:10]}")
        self.use_cache = use_cache
        own = JUDGMENT_PROVIDERS.get(self.provider.name, {})
        self.max_attempts = max_attempts or int(os.environ.get(
            "SLOPE_JEV_MAX_ATTEMPTS", own.get("max_physical_attempts",
                                              cfg["budgets"]["jev_max_physical_attempts_including_retries"])))
        self.spend_cap = Decimal(spend_cap_usd or own.get("spend_cap_usd_per_run")
                                 or cfg["runtime"]["jev"]["spend_cap_usd_per_run"])
        self.price_per_token = Decimal(self.provider.price_usd_per_million or cfg["runtime"]["jev"][
            "provider_price_usd_per_million_input_tokens_at_design"]) / 10**6
        # where the provider prices cached reads and cache writes apart (Decisions), each at its own rate; a request
        # that may have been billed without a usage returned is reserved at the dearest rate a token can cost
        self.rates = {k: Decimal(own[f"price_usd_per_million_{k}_tokens"]) / 10**6
                      for k in ("cached_input", "cache_write_30m", "cache_write_12h")
                      if f"price_usd_per_million_{k}_tokens" in own}
        self.worst_per_token = max((self.price_per_token, *self.rates.values()))
        self.inflight_attempts = 0
        self.settled_attempts = 0  # the physical attempts of the requests finished
        self.inflight_usd = Decimal(0)
        self.spent_usd = Decimal(0)
        self.requests = 0
        self.cache_hits = 0

    def _check_model(self, raw: dict) -> None:
        """The answer is the pinned model's: for Decisions the requested model or its dated snapshot; else a build
        whose identifier carries the pinned marker."""
        returned = raw.get("model")
        if self.provider.name == "decisions":
            from app.agent.decisions import DecisionsError, returned_model

            try:
                returned_model(raw, self.provider.model)
            except DecisionsError as error:
                raise ConfigurationError(str(error)) from error
        elif not returned or self.provider.marker not in str(returned):
            raise ConfigurationError(f"Unexpected model {returned!r}; pinned {self.provider.pinned_build}")

    def cost(self, usage: dict) -> Decimal | None:
        """A request's cost from its usage: the provider's own figure where it gives one, else its input tokens at
        their rates (cached reads and cache writes apart where the provider prices them apart); None without usage."""
        if usage.get("cost") is not None:
            return Decimal(str(usage["cost"]))
        tokens = next((usage[k] for k in ("input_tokens", "prompt_tokens", "total_tokens") if k in usage), None)
        if tokens is None:
            return None
        details = usage.get("input_tokens_details") or {}
        if not self.rates or not details:
            return Decimal(int(tokens)) * self.price_per_token
        cached = int(details.get("cached_tokens") or 0)
        ttl = details.get("cache_write_tokens_details") or {}
        w30, w12 = int(ttl.get("ttl_30m_tokens") or 0), int(ttl.get("ttl_12h_tokens") or 0)
        w12 += max(int(details.get("cache_write_tokens") or 0) - w30 - w12, 0)  # a write without its period: dearer
        plain = int(tokens) - cached - w30 - w12  # cached reads and writes are counted within the input tokens
        if plain < 0:
            raise ConfigurationError(f"usage counts more cached and written tokens than input tokens: {usage}")
        return (plain * self.price_per_token + cached * self.rates["cached_input"]
                + w30 * self.rates["cache_write_30m"] + w12 * self.rates["cache_write_12h"])

    def _reserve(self, payload_bytes: int) -> tuple[int, Decimal]:
        """Headroom check before dispatch; released after the request completes. An attempt is reserved, and charged
        where it returns no usage, at twice its payload's bytes at the dearest rate a token can cost: a token covers at
        least a byte of text in the tokenizers these models use, and the doubling stands for the provider's framing,
        which it does not publish. It is a margin, not a bound; the provider's own credit balance is the hard stop. The
        attempts are those of the requests finished (`settled_attempts`) and every request in flight at its worst."""
        attempts = 1 + MAX_RETRIES
        estimate = 2 * Decimal(payload_bytes) * self.worst_per_token * attempts
        if self.settled_attempts + self.inflight_attempts + attempts > self.max_attempts:
            raise JevBudgetExceeded(f"Jev attempt ceiling reached ({self.max_attempts} physical attempts)")
        if self.spent_usd + self.inflight_usd + estimate > self.spend_cap:
            raise JevBudgetExceeded(f"Jev spend cap ${self.spend_cap} would be exceeded")
        self.inflight_attempts += attempts
        self.inflight_usd += estimate
        return attempts, estimate

    async def judge(self, *, profile: str, question_ids: list[str], state: dict, subject_ids: tuple[str, ...] = (),
                    source_content_hashes: tuple[str, ...] = (),
                    criteria: dict[str, dict[str, str] | list[str]] | None = None,
                    fill: Callable[[str], str] | None = None) -> tuple[JevCallRecord, list[SemanticObservation]]:
        """Ask independent questions about one state in a single request. `criteria` supplies host-built answer
        options for questions whose registry entry says so (e.g. a dispute model stage's transitions, or a factor's
        rubric)."""
        entries = {qid: registry_question(qid) for qid in question_ids}
        for qid, crit in (criteria or {}).items():
            if not entries[qid]["prompt"].get("criteria_from_host"):
                raise ConfigurationError(f"{qid} does not take host-built criteria")
            entries[qid] = {**entries[qid], "prompt": {**entries[qid]["prompt"], "criteria": crit}}
        registry_version = question_registry()["registry_version"]
        questions = {qid: build_question(e, fill) for qid, e in entries.items()}
        # Key on the exact built question text and criteria, so an edit without a version bump cannot hit.
        cache_key = canonical_sha256({"model": self.provider.model, "state": state,
                                      "questions": {q: v.model_dump(mode="json") for q, v in questions.items()}})
        cache_file = CACHE_DIR / f"{cache_key}.json"
        attempts_used, cache_hit = 0, False
        if self.use_cache and cache_file.exists():
            cached = json.loads(cache_file.read_text())
            raw, now = cached["raw"], cached["created_at"]
            self._check_model(raw)  # a saved answer is the pinned model's too, or it is not used
            cache_hit = True
            self.cache_hits += 1
        else:
            if cache_only():
                raise ConfigurationError(f"No saved Jev response for {profile} {question_ids} (SLOPE_JEV_CACHE_ONLY)")
            reserved, estimate = self._reserve(len(json.dumps(state, default=str).encode()) + sum(
                len(q.model_dump_json().encode()) for q in questions.values()))
            mine = [0]
            token = _ATTEMPTS.set(mine)
            per_attempt = estimate / (1 + MAX_RETRIES)
            done = False
            try:
                self.requests += 1
                raw = (await self.client.system_one(state=state, questions=questions, model=self.provider.model,
                                                    response_model=_Raw)).model_dump(mode="json")
                done = True
            finally:
                self.inflight_attempts -= reserved
                self.inflight_usd -= estimate
                _ATTEMPTS.reset(token)
                attempts_used = mine[0]
                self.settled_attempts += attempts_used
                # an attempt that returned no usage (a timeout, an error after the work) may still have been billed:
                # it is counted at its worst case, so the spend cap covers every request that may have been charged
                self.spent_usd += per_attempt * max(attempts_used - (1 if done else 0), 0)
            cost = self.cost(raw.get("usage") or {})
            self.spent_usd += cost if cost is not None else per_attempt
            now = datetime.now(UTC).isoformat()
            self._check_model(raw)
            if self.use_cache:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps({"raw": raw, "created_at": now}))

        call = JevCallRecord(
            call_id=self.new_id("jev"), run_id=self.run_id, profile=profile, subject_ids=subject_ids,
            question_ids=tuple(question_ids), registry_version=registry_version, requested_model=self.provider.model,
            returned_model=raw.get("model"), state_sha256=canonical_sha256(state),
            source_content_hashes=source_content_hashes, attempts_used=attempts_used, usage=raw.get("usage"),
            raw_response=raw, created_at=now, cache_hit=cache_hit)
        if EXCHANGE_LOG is not None:
            EXCHANGE_LOG.append({"adapter": self.run_id, "call_id": call.call_id, "profile": profile,
                                 "question_ids": list(question_ids), "created_at": now, "cache_hit": cache_hit,
                                 "request": {"model": self.provider.model,
                                             "questions": {q: v.model_dump(mode="json") for q, v in questions.items()},
                                             "state": state},
                                 "response": raw})
        return call, self._observations(call.call_id, profile, question_ids, entries, raw.get("answers") or {},
                                        subject_ids)

    def _observations(self, call_id: str, profile: str, question_ids, entries: dict, answers: dict,
                      subject_ids: tuple[str, ...]) -> list[SemanticObservation]:
        observations = []
        for qid in question_ids:
            ans, entry = answers.get(qid) or {}, entries[qid]
            if entry["primitive"] == "noul":
                value = ans.get("noul")
                observations.append(SemanticObservation(
                    observation_id=self.new_id("obs"), call_id=call_id, profile=profile, question_id=qid,
                    question_version=entry["version"], primitive="noul",
                    answer=None if value is None else value >= NOUL_THRESHOLD, noul_value=value, subject_ids=subject_ids))
            elif entry["primitive"] == "score":
                probs = ans.get("probabilities")
                observations.append(SemanticObservation(
                    observation_id=self.new_id("obs"), call_id=call_id, profile=profile, question_id=qid,
                    question_version=entry["version"], primitive="score",
                    answer=None if not probs else str(max(probs, key=lambda k: probs[k])), score_value=ans.get("score"),
                    probabilities={str(k): v for k, v in probs.items()} if probs else None,
                    confidence=ans.get("confidence"), subject_ids=subject_ids))
            else:
                observations.append(SemanticObservation(
                    observation_id=self.new_id("obs"), call_id=call_id, profile=profile, question_id=qid,
                    question_version=entry["version"], primitive="choice", answer=ans.get("choice"),
                    probabilities=ans.get("probabilities"), confidence=ans.get("confidence"), subject_ids=subject_ids))
        return observations

    def usage_summary(self) -> dict[str, Any]:
        return {"provider": self.provider.name, "requests": self.requests, "cache_hits": self.cache_hits,
                "physical_attempts": self.physical_attempts, "attempt_ceiling": self.max_attempts,
                "spent_usd": str(self.spent_usd), "spend_cap_usd": str(self.spend_cap)}
