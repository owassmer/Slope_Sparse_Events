"""Investigation runner: one Claude Agent SDK session bound to one run, snapshot and evaluation arm.

The agent sees only the scoped MCP tools (tools.py) and a generic role prompt built from agent_config.
Nothing answer-bearing enters the prompt: no documents list in order, no expected findings, no Jev
question sequence. Execution states follow agent_config: CANDIDATE_READY when the packet is submitted and
no budget ran out; INCOMPLETE_REVIEW on budget, deadline or service failure (never a borrower decline);
FAILED_CONFIGURATION on auth or setup problems. The locked run is copied to runs/recorded/<run_id>/.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    ToolUseBlock,
)
from pydantic import BaseModel

from app.agent.jev import JevAdapter
from app.agent.jev_profiles import Semantics
from app.agent.run_store import RunStore
from app.agent.smoke import preflight
from app.agent.tools import RunContext, build_server
from app.config import (
    AGENT_PROCESS_ENV,
    CASES_DIR,
    RECORDED,
    ConfigurationError,
    agent_config,
    allowed_tools,
    main_agent_settings,
    question_registry,
)
from app.evidence.store import EvidenceStore

WORKING_METHOD = """Working method
- Start with get_mission, read_baseline_profile and read_loan_terms.
- For each material unknown that could change the financing choice, record_dependency with a precise target: the entity plus
  the specific obligation, counterparty, asset or activity. Prefer unknowns about dated cash obligations, cash access and receipts.
- search_evidence for that dependency, read_evidence for the passages you need (including their surrounding context), and cite
  only text you have read. Quote exactly.
{jev_method}- propose_finding for one atomic proposition at a time, then resolve_finding. Record how each linked observation was used,
  and give a reason whenever you override one.
- propose_effect for each supported economic mechanism, stating its baseline treatment, parameters (unknown stays unknown) and the
  plain-language model consequence. Existing liabilities are scheduled once, never added again.
- Account for every disclosed legal matter, settlement and settlement-related accounting item you encounter. When one has no
  future cash effect (for example it is reported paid, or it is an accounting entry rather than cash), record that as a cited
  finding and an effect with mechanism resolved_obligation or noncash_normalization and cash_direction none, so the reviewer can
  see why it does not change cash.
- If you connect a name, label or table row to an obligation by matching amounts or dates, mark that finding is_inference and
  describe the link as an inference in your conclusion.
- run_sensitivity on validated settlement effects to see which unknown changes the cash requirement.
- request_missing_fact for each pivotal fact the evidence cannot supply, then submit_packet.
- Use only the evidence returned by the tools; do not rely on remembered facts about this company or later events.
- Work efficiently: about {turn_budget} turns in total."""

JEV_METHOD = """- Search results carry a semantic screen label; all results are shown and you decide what to read.
- When the posture, status or entity of a statement matters, call judge (claim_interpretation) with one claim, a precise target
  and the verbatim anchor_quote, and link the observations to the finding. When two statements may conflict, use
  statement_relation. An ambiguous or unsettled judgment means: read more context or narrow the target, not accept.
"""


class InvestigationOutcome(BaseModel):
    conclusion: str
    pivotal_unknowns: list[str]
    supported_effect_ids: list[str]


def system_prompt(arm: str, max_turns: int) -> str:
    cfg = agent_config()
    role = (cfg["comparison_mode_overrides"]["agent_only"]["role_instruction"] if arm == "agent_only"
            else cfg["role_instruction"])
    invariants = "\n".join(f"- {i}" for i in cfg["invariants"])
    method = WORKING_METHOD.format(jev_method=JEV_METHOD if arm == "agent_plus_jev" else "", turn_budget=max_turns - 5)
    return f"{role}\n\nInvariants\n{invariants}\n\n{method}"


async def _session(ctx: RunContext, options: ClaudeAgentOptions, stats: dict[str, Any]) -> ResultMessage | None:
    result = None
    async with ClaudeSDKClient(options=options) as client:
        await client.query("Begin the investigation for this review.")
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                stats["models"].add(msg.model)
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        name = block.name.removeprefix("mcp__credit__")
                        stats["tool_calls"][name] = stats["tool_calls"].get(name, 0) + 1
            elif isinstance(msg, ResultMessage):
                result = msg
    return result


def investigate(snapshot_id: str = "synergy_20240813", arm: str = "agent_plus_jev", *,
                max_turns: int | None = None, wall_clock_s: int | None = None) -> dict[str, Any]:
    cfg, settings = agent_config(), main_agent_settings()
    max_turns = max_turns or cfg["budgets"]["main_agent_max_turns"]
    wall_clock_s = wall_clock_s or cfg["budgets"]["main_investigation_wall_clock_seconds"]
    started = datetime.now(UTC)
    record: dict[str, Any] = {"snapshot_id": snapshot_id, "arm": arm, "started_at": started.isoformat(),
                              "configuration_version": cfg["configuration_version"],
                              "registry_version": question_registry()["registry_version"],
                              "requested_model": settings["model"], "effort": settings["effort"]}
    try:
        record["claude_auth"] = preflight()
        inputs = json.loads((CASES_DIR / snapshot_id / "run_inputs.json").read_text())
    except ConfigurationError as e:
        return {**record, "status": "FAILED_CONFIGURATION", "error": str(e)}

    run_id = f"{snapshot_id}-{arm}-{started:%Y%m%dT%H%M%SZ}"
    evidence = EvidenceStore(snapshot_id)
    run = RunStore(run_id, meta={"arm": arm, "case_id": inputs["case_id"], "snapshot_id": snapshot_id,
                                 "evidence_manifest_hash": evidence.snapshot_info()["evidence_manifest_hash"],
                                 "configuration_version": cfg["configuration_version"],
                                 "registry_version": record["registry_version"], "model": settings["model"]})
    jev = JevAdapter(run_id=run_id, new_id=run.new_id) if arm == "agent_plus_jev" else None
    ctx = RunContext(run=run, evidence=evidence, inputs=inputs, arm=arm,
                     semantics=Semantics(run, evidence, jev) if jev else None)
    allowed = allowed_tools(arm)
    options = ClaudeAgentOptions(
        model=settings["model"], effort=settings["effort"], tools=[], allowed_tools=allowed,
        mcp_servers={"credit": build_server(ctx, allowed)}, strict_mcp_config=True, setting_sources=[],
        permission_mode=settings["native_sdk_options"]["permission_mode"], max_turns=max_turns,
        cwd=tempfile.mkdtemp(prefix="slope-run-"), env=AGENT_PROCESS_ENV, system_prompt=system_prompt(arm, max_turns),
        output_format={"type": "json_schema", "schema": InvestigationOutcome.model_json_schema()})

    stats: dict[str, Any] = {"models": set(), "tool_calls": {}}
    result, failure = None, None
    try:
        result = asyncio.run(asyncio.wait_for(_session(ctx, options, stats), timeout=wall_clock_s))
    except TimeoutError:
        failure = f"wall clock budget of {wall_clock_s}s reached"
    except Exception as e:  # service failure: incomplete review, never a decline
        failure = f"{type(e).__name__}: {e}"[:500]

    if result is not None and result.is_error:
        failure = failure or f"agent session ended with an error: {result.subtype} {result.result or ''}"[:500]
    if result is not None and result.subtype == "error_max_turns":
        failure = failure or f"turn budget of {max_turns} reached"
    if not ctx.submitted:
        run.lock({"summary": None, "incomplete_reasons": ctx.incomplete_reasons + [failure or "packet not submitted"]})
    status = "CANDIDATE_READY" if ctx.submitted and not ctx.incomplete_reasons and not failure else "INCOMPLETE_REVIEW"

    graph = run.export()
    record.update({
        "run_id": run_id, "status": status, "failure": failure, "incomplete_reasons": ctx.incomplete_reasons,
        "finished_at": datetime.now(UTC).isoformat(),
        "returned_models": sorted(stats["models"] | set((result.model_usage or {}) if result else {})),
        "tool_calls": stats["tool_calls"],
        "claude": None if result is None else {"num_turns": result.num_turns, "duration_ms": result.duration_ms,
                                                "usage": result.usage, "notional_cost_usd": result.total_cost_usd,
                                                "structured_output": result.structured_output},
        "jev": jev.usage_summary() if jev else None,
        "graph_counts": {k: len(v) for k, v in graph.items() if isinstance(v, list)},
        "chain_head": run.head,
    })
    (run.dir / "run.json").write_text(json.dumps(record, indent=2, default=str) + "\n")
    dest = RECORDED / run_id
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(run.dir, dest)
    record["recorded_at"] = str(Path("runs/recorded") / run_id)
    return record
