"""Runtime configuration: paths, kit contracts, provider selection and auth preflight.

Credentials are read from the process environment or the repo's `.env` file into a
host-side dict. They are never exported into `os.environ`, so the Claude subprocess
never sees the Jev key, and they are never logged.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "Slope_Credit_Scenario_Research_and_Design_Kit"
CONTRACTS = KIT / "research/revision_v2/contracts"
VAR = ROOT / "var"
RECORDED = ROOT / "runs/recorded"


class ConfigurationError(RuntimeError):
    """Maps to execution state FAILED_CONFIGURATION: stop before any case execution."""


@cache
def agent_config() -> dict:
    return json.loads((CONTRACTS / "agent_config.json").read_text())


@cache
def question_registry() -> dict:
    return json.loads((CONTRACTS / "question_registry.json").read_text())


@cache
def _dotenv() -> dict[str, str]:
    # A worktree under .claude/worktrees/ has no .env of its own; use the main checkout's (.claude/worktrees/<name> sits three levels below it).
    path = next((d / ".env" for d in (ROOT, *ROOT.parents[:3]) if (d / ".env").is_file()), None)
    if path is None:
        return {}
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip().removeprefix("export ").strip()] = value.strip().strip("'\"")
    return values


def secret(name: str) -> str | None:
    return os.environ.get(name) or _dotenv().get(name)


# --- Jev provider selection -------------------------------------------------------------

@dataclass(frozen=True)
class JevProvider:
    name: str
    credential_env: str
    base_url: str | None  # None = SDK default (direct TypeSafe)
    model: str  # provider-specific identifier for the same pinned Jev build
    pinned_build: str  # canonical build from agent_config (jev-1.13.0)


def jev_provider() -> JevProvider:
    """Select the Jev provider. OpenRouter is the default while TypeSafe sign-up is restricted.

    Both routes serve the same Jev build; only the transport and model identifier differ.
    """
    pinned = agent_config()["runtime"]["jev"]["model_pin"]  # "jev-1.13.0"
    name = (os.environ.get("JEV_PROVIDER") or _dotenv().get("JEV_PROVIDER") or "openrouter").lower()
    if name == "openrouter":
        return JevProvider("openrouter", "OPENROUTER_API_KEY", "https://openrouter.ai/api",
                           "typesafe/jev-1.13", pinned)
    if name == "typesafe":
        return JevProvider("typesafe", "TYPESAFE_API_KEY", None, pinned, pinned)
    raise ConfigurationError(f"Unknown JEV_PROVIDER {name!r}; expected 'openrouter' or 'typesafe'")


def jev_credential(provider: JevProvider) -> str:
    key = secret(provider.credential_env)
    if not key:
        raise ConfigurationError(
            f"Jev provider {provider.name!r} requires {provider.credential_env} (env or .env)")
    return key


# --- Claude subscription preflight --------------------------------------------------------

# Any of these would route Claude or the reviewer to metered/alternate inference.
METERED_OVERRIDES = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
)


def metered_overrides_present() -> list[str]:
    """Names (never values) of provider overrides set in env or .env."""
    return [n for n in METERED_OVERRIDES if os.environ.get(n) or _dotenv().get(n)]


# Env for the agent's CLI subprocess. Without this the CLI makes background calls (e.g. session
# titles) on a small model, so the run would not be served exclusively by the pinned model.
AGENT_PROCESS_ENV = {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}

# The SDK implements json_schema output as this tool; it is exposed alongside the scoped MCP tools.
SDK_OUTPUT_TOOL = "StructuredOutput"


def main_agent_settings() -> dict:
    return agent_config()["runtime"]["main_agent"]
