"""Which accepted findings supply each record item the forecast questions name (spec §3.4-3.5).

The design gives this matching to the agent: it reads each template's record items (template_items), attaches the
accepted findings that supply each, or records that the record has nothing (RecordItemSlot); `from_agent` builds the
slots from those records. For a run recorded before the agent was given the record items, Jev answers it: one passage and one record item per
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


def _v(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in version.split("."))


def record_items(model: dict, version: str | None = None) -> dict[str, dict]:
    """question node -> its actor, decision and record items, for every node the forecasts can ask; with `version`
    (the disputes' interpretation version), only the nodes that version has (a node's `since`)."""
    return {n: {"actor": s["actor"], "decision": s["decision"], "items": s["record_items"]}
            for t in model["templates"].values() for n, s in t.get("nodes", {}).items() if not s.get("stress_only")
            and (version is None or _v(s.get("since", "0.0.0")) <= _v(version))}


CHAIN_TEMPLATES = ("indenture_convertible", "bankruptcy_effects")  # the instrument and filing templates a chain reaches


def template_nodes(model: dict, template: str) -> list[str]:
    """The question nodes a dispute template's chain asks: the contract's `asks` for the template (design §6.1); for a
    4.0.0 template without one, its own nodes and those of the instrument and filing templates, as 4.0.0 has them."""
    t = model["templates"][template]
    if "asks" in t:
        return list(t["asks"])
    spec = {n: s for tt in model["templates"].values() for n, s in tt.get("nodes", {}).items()}
    names = list(t.get("nodes", {})) + [n for tn in CHAIN_TEMPLATES for n in model["templates"].get(tn, {}).get("nodes", {})]
    return [n for n in names if not spec[n].get("stress_only") and "since" not in spec[n]]


def template_items(model: dict, template: str) -> dict[str, list[str]]:
    """Record item -> the decisions ('actor: decision') whose questions name it, for the template's chain: the slots
    the agent fills (spec §3.5). The text is the dispute model's, and no more."""
    spec = {n: s for tt in model["templates"].values() for n, s in tt.get("nodes", {}).items()}
    out: dict[str, list[str]] = {}
    for n in template_nodes(model, template):
        for item in spec[n]["record_items"]:
            d = f"{spec[n]['actor']}: {spec[n]['decision']}"
            if d not in out.setdefault(item, []):
                out[item].append(d)
    return out


def from_agent(recorded: list, nodes: dict[str, dict], accepted: set[str]) -> dict[str, dict[str, list[str]]]:
    """question node -> record item -> the accepted findings the agent attached to the item (its RecordItemSlot
    records); an item it recorded as not in the record, or never attached, has none."""
    by_item: dict[str, list[str]] = {}
    for slot in recorded:
        if slot.status == "filled":
            fids = by_item.setdefault(slot.item, [])
            fids += [f for f in slot.finding_ids if f in accepted and f not in fids]
    return {n: {item: list(by_item.get(item, [])) for item in spec["items"]} for n, spec in nodes.items()}


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
