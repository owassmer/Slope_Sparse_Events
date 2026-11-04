"""A case as the compiled walk reads it: a recorded investigation run's context (its inputs, the live disputes with the
instruments their terms reach, the dispute model with the case's parameters, the bank feed, the record-item slots),
the operating draws' basis, and the question builder (`Forecaster`) over the case's draws.

    context(run_id, variant)       -> the run's context, setup and sensitivities (cached)
    forecaster(run_id, variant)    -> (fc, d): the question builder on every draw, and its pending dispute
    on_draws(draws, ...)           -> (fc, d) on the chosen operating draws (indices among them), each keeping its own
                                      identity, so its flows, lags and verdict day are the same as in the full run
    chain(fc, d)                   -> the case's state at the review date (`Chain` after `instrument_cash`), which
                                      `replay.constants` and `replay.state` read

A variant is a declared economic assumption (app/analysis/assumptions.py `declared`); 'central' is the run's own.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from functools import cache
from pathlib import Path

import numpy as np

LEAD_RUN = "akoustis_20240514-agent_plus_jev-20260929T052558Z"
RECORDED = Path("runs/recorded")


def basis_for(feed, setup):
    """The operating draws' cash, need and legal spend: the same simulation the line engine runs."""
    from app.analysis import operating
    from app.analysis.engine import prepare
    from app.analysis.events import Basis

    ops = operating.simulate_for(feed, setup)
    line = prepare(setup, ops)
    # the borrower's cash on the review date includes what an existing line's history moved (Setup.exposure)
    return Basis.of(ops, line.need, feed.available_cents + setup.exposure.cash_cents, line=line)


def _load_run(run_id: str, root: Path):
    from app.agent.run_store import RunStore
    from app.evidence.store import EvidenceStore

    store = RunStore(run_id, root=root)
    meta = store.events[0].payload
    inputs = meta["run_inputs"]
    evidence = EvidenceStore(meta["snapshot_id"])
    review = date.fromisoformat(str(evidence.snapshot_info()["cutoff"])[:10])
    return store, meta, inputs, evidence, review


def record_item_slots(run_id: str, root: Path, findings: dict, hydrate, refresh: bool,
                      version: str | None = None, recorded: list | None = None) -> dict:
    """Which accepted findings supply each record item (app/disputes/slots.py), kept beside the run in slots.json.
    The agent's own records (RecordItemSlot) decide it where the run has them (spec §3.5); a run recorded before the
    agent was given the record items is read once by Jev, on its own adapter and budget."""
    from app.agent.jev import JevAdapter
    from app.disputes.rules import load_model
    from app.disputes.slots import from_agent, load, match, record_items

    path = root / run_id / "slots.json"
    nodes = record_items(load_model(), version)  # the nodes the disputes' interpretation version has
    if recorded:
        out = from_agent(recorded, nodes, set(findings))
        path.write_text(json.dumps(out, indent=1) + "\n")
        return out
    kept = load(path)
    if kept is not None and {n: list(v) for n, v in kept.items()} == {n: s["items"] for n, s in nodes.items()} \
            and not refresh:
        return kept
    jev = JevAdapter(run_id=f"{run_id}-slots", use_cache=not refresh, max_attempts=3000)
    out = asyncio.run(match(findings, hydrate, nodes, jev))
    path.write_text(json.dumps(out, indent=1) + "\n")
    return out


def run_context(run_id: str, root: Path = RECORDED, refresh: bool = False) -> dict:
    """What the walk of a recorded run reads before it asks the judge: the run's inputs, the live disputes with the
    instruments their terms reach, the dispute model with the case's parameters, the feed and the record-item slots."""
    from app.analysis.events import coupon_terms
    from app.analysis.setup import setup_from_inputs
    from app.disputes.hydrate import evidence_state
    from app.disputes.rules import compatible, load_model
    from app.finance.bank import load_feed

    store, meta, inputs, evidence, review = _load_run(run_id, root)
    setup = setup_from_inputs(inputs, review)
    findings = {k: f for k, f in store.graph["findings"].items() if f.status == "accepted"}
    live = [d for d in store.graph["disputes"].values() if d.status != "superseded"]
    m = load_model(meta["snapshot_id"])  # the contract, with the case's scenario parameters where it has any
    current = m["model_version"]
    stale = sorted({d.model_version for d in live if not compatible(m, d.model_version)})
    if stale and not refresh:
        raise RuntimeError(f"{run_id}: disputes were interpreted under dispute model {', '.join(stale)}, not the current "
                           f"{current}; re-interpret the run, or pass refresh to build anyway.")
    sources = {s["source_id"]: (s["title"], s["available_at"][:10]) for s in evidence.list_sources()}
    instruments = [f for f in store.graph.get("financing", {}).values() if f.status != "superseded"]
    instruments = [coupon_terms(f, [s.quote for fid in f.finding_ids if fid in findings for s in findings[fid].spans],
                                review, setup.horizon) for f in instruments]  # the coupon: arithmetic on its quote
    live = [d.model_copy(update={"financing": tuple(f for f in instruments if d.instance_id in f.dispute_ids)})
            for d in live]  # the instruments each judgment's terms reach (dispute model 4.0.0)
    hydrate = lambda f: evidence_state(evidence, f, [], sources)["passage"]  # noqa: E731
    version = max((d.model_version for d in live), key=lambda v: tuple(map(int, v.split("."))), default=current)
    slots = record_item_slots(run_id, root, findings, hydrate, refresh, version,
                              list(store.graph.get("record_items", {}).values()))
    return {"meta": meta, "inputs": inputs, "review": review, "setup": setup,
            "borrower": inputs["baseline_profile"]["borrower"], "findings": findings, "live": live, "m": m,
            "feed": load_feed(meta["snapshot_id"]), "hydrate": hydrate, "slots": slots}


@cache
def context(run_id: str = LEAD_RUN, variant: str = "central"):
    """The run's context, and the setup and chain sensitivities of the variant ('central': the run's own)."""
    from app.analysis.assumptions import declared
    from app.analysis.setup import setup_from_inputs

    ctx = run_context(run_id)
    if variant == "central":
        return ctx, ctx["setup"], None
    v = {x["id"]: x for x in declared(ctx["meta"]["snapshot_id"], ctx["inputs"], ctx["m"])}[variant]
    return ctx, setup_from_inputs(ctx["inputs"], ctx["review"], v["scenario"]), (v["sens"] or None)


def _no_judge(*args, **kwargs):
    raise RuntimeError("the walk reads no judgment: questions are answered from the judge's recorded answers")


def forecaster(run_id: str = LEAD_RUN, variant: str = "central"):
    """A fresh question builder over every draw (its caches are its own), and the case's pending dispute. The
    findings' passages are hydrated for the questions' records."""
    from app.disputes.forecast import Forecaster

    ctx, setup, sens = context(run_id, variant)
    fc = Forecaster(ctx["live"], ctx["findings"], borrower=ctx["borrower"], review=ctx["review"],
                    horizon=setup.horizon, hydrate=ctx["hydrate"], setup=setup,
                    basis=basis_for(ctx["feed"], setup), slots=ctx["slots"], model=ctx["m"], sens=sens)
    d = next(d for d, _ in fc.ordered() if d.stage == "liability_pending" and d.borrower_role == "debtor")
    return fc, d


def on_draws(draws, run_id: str = LEAD_RUN, variant: str = "central"):
    """The question builder on the chosen operating draws (indices among them), each keeping its own identity."""
    fc, d = forecaster(run_id, variant)
    keep = np.zeros(fc.draws.n, dtype=bool)
    keep[list(draws)] = True
    fc.draws = fc.draws.sub(keep)
    return fc, d


def chain(fc, d):
    """The case's state at the review date on fc's draws, as the compiled walk starts from it."""
    from app.analysis.events import Chain

    ch = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
    ch.instrument_cash()
    return ch
