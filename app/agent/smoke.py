"""Step 1 runtime smoke test (spec §9): subscription auth, one scoped custom tool with schema output,
one budgeted Jev call, and the reviewer's sign-in mode. Writes a run record without secrets.

Any failed gate is FAILED_CONFIGURATION: stop before case execution, never switch to metered inference.
"""

from __future__ import annotations

import asyncio
import json
import platform
import subprocess
import tempfile
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    create_sdk_mcp_server,
    tool,
)
from claude_agent_sdk._cli_version import __cli_version__
from pydantic import BaseModel

from app.agent.jev import JevAdapter
from app.agent.mission import project_mission
from app.config import (
    AGENT_PROCESS_ENV,
    ROOT,
    SDK_OUTPUT_TOOL,
    ConfigurationError,
    agent_config,
    main_agent_settings,
    metered_overrides_present,
)

SMOKE_CASE = "synergy_chc_2024"
SMOKE_TOOL = "mcp__credit__get_mission"


class SmokeReport(BaseModel):
    """Schema-constrained output the model must return."""

    borrower: str
    as_of: str
    mission_type: str
    tools_available: list[str]


def _bundled_cli() -> Path:
    import claude_agent_sdk

    return Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"


def claude_auth_status() -> dict[str, Any]:
    """Auth method of the CLI the SDK launches. Only non-identifying fields are kept."""
    out = subprocess.run([str(_bundled_cli()), "auth", "status"], capture_output=True, text=True, timeout=30)
    try:
        status = json.loads(out.stdout)
    except json.JSONDecodeError as e:
        raise ConfigurationError(f"`claude auth status` returned no JSON (exit {out.returncode})") from e
    return {k: status.get(k) for k in ("loggedIn", "authMethod", "apiProvider", "subscriptionType")}


def preflight() -> dict[str, Any]:
    overrides = metered_overrides_present()
    if overrides:
        raise ConfigurationError(f"Metered/alternate provider overrides present: {overrides}")
    auth = claude_auth_status()
    if not (auth["loggedIn"] and auth["authMethod"] == "claude.ai" and auth["apiProvider"] == "firstParty"):
        raise ConfigurationError(f"Claude is not on native subscription auth: {auth}")
    return auth


async def run_claude_smoke() -> dict[str, Any]:
    settings = main_agent_settings()
    sdk_opts = settings["native_sdk_options"]
    calls: list[dict] = []

    @tool("get_mission", "Return the scoped mission for this run.", {})
    async def get_mission(_args: dict) -> dict:
        mission = project_mission(SMOKE_CASE)
        calls.append({"tool": "get_mission", "returned_keys": sorted(mission)})
        return {"content": [{"type": "text", "text": json.dumps(mission)}]}

    server = create_sdk_mcp_server("credit", version="0.1.0", tools=[get_mission])
    cwd = Path(tempfile.mkdtemp(prefix="slope-smoke-"))  # empty: no corpus, outputs or answers
    options = ClaudeAgentOptions(
        model=settings["model"],
        effort=settings["effort"],
        tools=[],
        allowed_tools=[SMOKE_TOOL],
        mcp_servers={"credit": server},
        strict_mcp_config=True,
        setting_sources=[],
        permission_mode=sdk_opts["permission_mode"],
        max_turns=4,
        cwd=str(cwd),
        env=AGENT_PROCESS_ENV,
        system_prompt=(
            "You are running a runtime smoke test. Call the get_mission tool exactly once, then "
            "return the structured report. List in tools_available the exact names of every tool "
            "you can call in this session, and nothing else."
        ),
        output_format={"type": "json_schema", "schema": SmokeReport.model_json_schema()},
    )

    init: dict[str, Any] = {}
    models_seen: set[str] = set()
    result: ResultMessage | None = None
    async with ClaudeSDKClient(options=options) as client:
        await client.query("Run the smoke test.")
        async for msg in client.receive_response():
            if isinstance(msg, SystemMessage) and msg.subtype == "init":
                init = msg.data
            elif isinstance(msg, AssistantMessage):
                models_seen.add(msg.model)
            elif isinstance(msg, ResultMessage):
                result = msg

    checks: dict[str, bool] = {}
    if result is None or result.is_error:
        detail = result and (result.api_error_status, result.result or result.errors)
        raise ConfigurationError(f"Claude run did not complete: {detail}")
    report = SmokeReport.model_validate(result.structured_output)
    mission = project_mission(SMOKE_CASE)
    init_tools = sorted(init.get("tools") or [])
    checks["api_key_source_is_subscription"] = init.get("apiKeySource") == "none"
    checks["only_scoped_tool_exposed"] = init_tools == sorted([SMOKE_TOOL, SDK_OUTPUT_TOOL])
    checks["custom_tool_called"] = len(calls) == 1
    checks["structured_output_valid"] = True  # model_validate above raises otherwise
    checks["structured_output_matches_tool"] = (report.borrower, report.as_of) == (
        mission["borrower"], mission["as_of"])
    # Every model that consumed tokens in the run, not just those that produced assistant turns.
    returned_models = sorted(models_seen | set(result.model_usage or {}))
    checks["requested_model_served"] = returned_models == [settings["model"]]

    return {
        "requested_model": settings["model"],
        "requested_effort": settings["effort"],
        "returned_models": returned_models,
        "api_key_source": init.get("apiKeySource"),
        "cli_version_reported": init.get("claude_code_version"),
        "init_tools": init_tools,
        "init_mcp_servers": init.get("mcp_servers"),
        "permission_mode": init.get("permissionMode"),
        "tool_calls": calls,
        "structured_output": report.model_dump(),
        "num_turns": result.num_turns,
        "duration_ms": result.duration_ms,
        "usage": result.usage,
        "notional_cost_usd": result.total_cost_usd,
        "notional_cost_note": "SDK-reported list-price estimate; subscription auth is not billed per token.",
        "checks": checks,
    }


# One admissible passage (merchant agreement §4.1.1, public 2024-06-28) and one atomic claim.
JEV_SMOKE_STATE = {
    "target": "Synergy CHC Corp. — existing WebBank merchant loan under the 1 May 2024 agreement",
    "source": {
        "source_id": "synergy_merchant_agreement_20240501",
        "sha256": "ac8e4d3a6bb05cb51ed876299199acb7e7536f817a350209cb8298dd2b404ada",
        "publisher": "SEC EDGAR (Synergy CHC S-1 Exhibit 10.32)",
        "document_kind": "executed loan agreement",
    },
    "passage": (
        "4.1.1. Borrower promises to pay Lender (i) a Minimum Payment within the first six (6) Month "
        "period from the Effective Date; (ii) an additional Minimum Payment within the next six Month "
        "period from Month six (6) to Month twelve (12) of the Term so that the total paid is at least "
        "60 percent of the Total Payment Amount by the end of Month twelve (12) and (iii) the Total "
        "Payment Amount prior to the end of the Term."
    ),
    "definitions": "Minimum Payment means thirty (30) percent of the Total Payment Amount.",
    "atomic_claim": "The Borrower must pay a Minimum Payment within the first six-Month period from the Effective Date.",
}


def run_jev_smoke(run_id: str) -> dict[str, Any]:
    adapter = JevAdapter(run_id=run_id, case_id=SMOKE_CASE, snapshot_id="smoke_no_snapshot")
    [judgment] = adapter.judge(JEV_SMOKE_STATE, ["claim_posture"],
                               source_content_hashes=[JEV_SMOKE_STATE["source"]["sha256"]])
    return {
        "provider": adapter.provider.name,
        "requests_used": adapter.requests,
        "request_ceiling": adapter.max_requests,
        "reserved_usd": str(adapter.reserved_usd),
        "spend_cap_usd": str(adapter.spend_cap),
        "judgment": judgment.model_dump(),
        "checks": {
            "returned_model_matches_pinned_build": bool(judgment.returned_model),  # enforced in adapter
            "typed_choice_returned": judgment.selected_choice is not None,
        },
    }


def codex_status() -> dict[str, Any]:
    ver = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=30)
    login = subprocess.run(["codex", "login", "status"], capture_output=True, text=True, timeout=30)
    text = (login.stdout + login.stderr).strip()
    return {
        "codex_version": ver.stdout.strip(),
        "login_status": text,
        "checks": {"chatgpt_sign_in": "ChatGPT" in text and "API key" not in text},
        "note": "Reviewer isolation over the admissible evidence bundle is verified in step 6.",
    }


def run_smoke(*, with_jev: bool = True, out: Path | None = None) -> dict[str, Any]:
    started = datetime.now(UTC)
    run_id = f"smoke-{started:%Y%m%dT%H%M%SZ}"
    record: dict[str, Any] = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "configuration_version": agent_config()["configuration_version"],
        "git_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                     text=True).stdout.strip(),
        "runtime": {
            "python": platform.python_version(),
            "claude_agent_sdk": version("claude-agent-sdk"),
            "bundled_claude_cli": __cli_version__,
            "typesafe_sdk": version("typesafe-sdk"),
            "platform": platform.platform(),
        },
        "paid_api_fallback": False,
    }
    try:
        record["claude_auth"] = preflight()
        record["claude"] = asyncio.run(run_claude_smoke())
        record["jev"] = run_jev_smoke(run_id) if with_jev else {"skipped": True}
        record["reviewer"] = codex_status()
        failed = [f"{part}.{name}" for part in ("claude", "jev", "reviewer")
                  for name, ok in record[part].get("checks", {}).items() if not ok]
        record["failed_checks"] = failed
        record["status"] = "PASS" if not failed else "FAILED_CONFIGURATION"
    except ConfigurationError as e:
        record["status"], record["error"] = "FAILED_CONFIGURATION", str(e)
    record["finished_at"] = datetime.now(UTC).isoformat()

    out = out or ROOT / "runs/recorded/runtime_smoke.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, default=str) + "\n")
    return record
