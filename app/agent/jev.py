"""Jev adapter: registry question -> typesafe_sdk.Choice -> preserved, versioned judgment record.

Jev answers one atomic semantic question. Its confidence describes its own judgment and is
never a probability of repayment or a dollar adjustment.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict
from typesafe_sdk import Choice, TypeSafeClient

from app.config import (
    ConfigurationError,
    JevProvider,
    agent_config,
    jev_credential,
    jev_provider,
    question_registry,
)

EXPECTED_BUILD_MARKER = "jev-1.13"


class _RawSystemOne(BaseModel):
    """Permissive response model so provider-added fields (e.g. OpenRouter cost) are preserved."""

    model_config = ConfigDict(extra="allow")


def canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_choice(question_id: str) -> tuple[dict, Choice]:
    """Registry mapping: copy entry.question and prefix global_rules to its instructions."""
    registry = question_registry()
    entry = next((q for q in registry["questions"] if q["id"] == question_id), None)
    if entry is None:
        raise KeyError(f"Unknown Jev question {question_id!r}")
    rules = "\n".join(f"- {r}" for r in registry["global_rules"])
    instructions = f"Global rules:\n{rules}\n\nQuestion:\n{entry['question']['instructions']}"
    return entry, Choice(instructions=instructions, criteria=entry["question"]["criteria"])


class JevJudgment(BaseModel):
    """One response record (registry response_record fields plus provider and usage)."""

    run_id: str
    case_id: str
    snapshot_id: str
    question_id: str
    question_version: str
    registry_version: str
    provider: str
    requested_model: str
    returned_model: str | None
    pinned_build: str
    state_sha256: str
    source_content_hashes: list[str]
    selected_choice: str | None
    probabilities: dict[str, float] | None
    confidence: float | None
    usage: dict[str, Any] | None
    raw_response: dict[str, Any]
    agent_disposition: str | None = None  # set later by the agent: accepted/needs_reconciliation/rejected
    created_at: str


class JevAdapter:
    """Host-side Jev client with request ceiling and a conservative input-cost reservation."""

    def __init__(self, *, run_id: str, case_id: str, snapshot_id: str,
                 provider: JevProvider | None = None) -> None:
        self.provider = provider or jev_provider()
        self.client = TypeSafeClient(api_key=jev_credential(self.provider), base_url=self.provider.base_url)
        cfg = agent_config()
        self.run_id, self.case_id, self.snapshot_id = run_id, case_id, snapshot_id
        self.max_requests = cfg["budgets"]["jev_max_requests_including_retries"]
        self.spend_cap = Decimal(cfg["runtime"]["jev"]["spend_cap_usd_per_run"])
        self.price_per_token = Decimal(cfg["runtime"]["jev"]["provider_price_usd_per_million_input_tokens_at_design"]) / 10**6
        self.requests = 0
        self.reserved_usd = Decimal(0)

    def judge(self, state: dict, question_ids: list[str],
              source_content_hashes: list[str] | None = None) -> list[JevJudgment]:
        """Ask independent questions against one state in a single request."""
        entries, questions = {}, {}
        for qid in question_ids:
            entries[qid], questions[qid] = build_choice(qid)

        payload_chars = len(json.dumps(state)) + sum(len(q.model_dump_json()) for q in questions.values())
        estimate = Decimal(payload_chars // 3 + 1) * self.price_per_token  # ~3 chars/token, conservative
        if self.requests >= self.max_requests:
            raise ConfigurationError(f"Jev request ceiling reached ({self.max_requests})")
        if self.reserved_usd + estimate > self.spend_cap:
            raise ConfigurationError(f"Jev spend cap ${self.spend_cap} would be exceeded")
        self.requests += 1
        self.reserved_usd += estimate

        raw = self.client.system_one(state=state, questions=questions, model=self.provider.model,
                                     response_model=_RawSystemOne).model_dump(mode="json")
        returned_model = raw.get("model")
        if not returned_model or EXPECTED_BUILD_MARKER not in str(returned_model):
            raise ConfigurationError(
                f"Unexpected Jev model {returned_model!r}; pinned {self.provider.pinned_build}")

        now = datetime.now(UTC).isoformat()
        state_hash = canonical_sha256(state)
        answers = raw.get("answers") or {}
        registry_version = question_registry()["registry_version"]
        out = []
        for qid in question_ids:
            ans = answers.get(qid) or {}
            out.append(JevJudgment(
                run_id=self.run_id, case_id=self.case_id, snapshot_id=self.snapshot_id,
                question_id=qid, question_version=entries[qid]["version"], registry_version=registry_version,
                provider=self.provider.name, requested_model=self.provider.model,
                returned_model=returned_model, pinned_build=self.provider.pinned_build,
                state_sha256=state_hash, source_content_hashes=source_content_hashes or [],
                selected_choice=ans.get("choice"), probabilities=ans.get("probabilities"),
                confidence=ans.get("confidence"), usage=raw.get("usage"),
                raw_response=raw, created_at=now,
            ))
        return out
