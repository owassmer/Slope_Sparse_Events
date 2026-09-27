"""Which accepted findings supply each record item the forecast questions name (spec §3.4-3.5).

The design gives this matching to the agent, which attaches its findings to the record items as it groups a dispute.
For a run recorded before the agent was given the record items, Jev answers it: one passage and one record item per
request, a yes/no reading of the passage. The result is written beside the run as `slots.json`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

QUESTION = "record_item_supplied"
CONCURRENCY = 8


def record_items(model: dict) -> list[str]:
    """Every record item a forecast question names, in the model's order."""
    return list(dict.fromkeys(x for t in model["templates"].values() for s in t.get("nodes", {}).values()
                              for x in s["record_items"]))


async def match(findings: dict, hydrate: Callable, items: list[str], jev) -> dict[str, list[str]]:
    """record item -> the finding ids whose passage supplies it."""
    sem = asyncio.Semaphore(CONCURRENCY)
    passages = {fid: hydrate(f) for fid, f in findings.items()}

    async def one(fid: str, item: str) -> tuple[str, str, bool]:
        async with sem:
            _, [o] = await jev.judge(
                profile="evidence_routing", question_ids=[QUESTION], subject_ids=(fid,),
                state={"record_item": item, "passage": passages[fid]},
                criteria={QUESTION: {"true": f"The passage supplies {item}.",
                                     "false": f"The passage does not supply {item}."}})
        return fid, item, bool(o.answer)

    results = await asyncio.gather(*(one(fid, item) for item in items for fid in sorted(findings)))
    out: dict[str, list[str]] = {item: [] for item in items}
    for fid, item, yes in results:
        if yes:
            out[item].append(fid)
    return out


def load(path: Path) -> dict[str, list[str]] | None:
    return json.loads(path.read_text()) if path.exists() else None
