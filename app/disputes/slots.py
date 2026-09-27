"""Which accepted findings supply each record item the forecast questions name (spec §3.4-3.5).

The design gives this matching to the agent, which attaches its findings to the record items as it groups a dispute.
For a run recorded before the agent was given the record items, Jev answers it: one passage and one record item per
request (the accepted finding with its passage), with the decision the item serves, a yes/no reading. The result is written beside the run as
`slots.json`: question node -> record item -> finding ids.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

QUESTION = "record_item_supplied"
CONCURRENCY = 8


def record_items(model: dict) -> dict[str, dict]:
    """question node -> its actor, decision and record items, for every node the forecasts can ask."""
    return {n: {"actor": s["actor"], "decision": s["decision"], "items": s["record_items"]}
            for t in model["templates"].values() for n, s in t.get("nodes", {}).items() if not s.get("stress_only")}


async def match(findings: dict, hydrate: Callable, nodes: dict[str, dict], jev) -> dict[str, dict[str, list[str]]]:
    """question node -> record item -> the finding ids whose passage supplies it."""
    sem = asyncio.Semaphore(CONCURRENCY)
    passages = {fid: {"finding": f.proposition, **hydrate(f)} for fid, f in findings.items()}

    async def one(node: str, fid: str, item: str) -> tuple[str, str, str, bool]:
        spec = nodes[node]
        async with sem:
            _, [o] = await jev.judge(
                profile="evidence_routing", question_ids=[QUESTION], subject_ids=(fid,),
                state={"decision": f"{spec['actor']}: {spec['decision']}", "record_item": item,
                       "passage": passages[fid]},
                criteria={QUESTION: {"true": f"The passage supplies {item}.",
                                     "false": f"The passage does not supply {item}."}})
        return node, fid, item, bool(o.answer)

    results = await asyncio.gather(*(one(n, fid, item) for n, spec in nodes.items() for item in spec["items"]
                                     for fid in sorted(findings)))
    out: dict[str, dict[str, list[str]]] = {n: {item: [] for item in spec["items"]} for n, spec in nodes.items()}
    for node, fid, item, yes in results:
        if yes:
            out[node][item].append(fid)
    return out


def load(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None
