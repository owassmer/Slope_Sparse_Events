"""Economic-assumption variants of a recorded run (spec §16.6), precomputed for the page.

The case declares them (scenario.json `assumption_variants`): each changes one setting from the central case, either
a common-model scenario (run_inputs.json `common_model.scenarios`, a Setup change) or a dispute-model parameter's
declared sensitivity (the chains' `sens`). A variant rebuilds the tree, asks Jev every question whose facts the change
moved (unchanged questions come from Jev's cache), and runs the analysis. Each variant runs in its own process
(`python -m app.analysis.assumptions RUN VARIANT`), one at a time; `assemble` then writes the page's `assumptions` list
and the compact `assumptions.json` beside the run.
"""

from __future__ import annotations

import gzip
import json
import pickle
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np

from app.config import CASES_DIR, ROOT, VAR

HEADLINE = ("due_horizon_cents", "collected_cents", "collection_rate", "past_due_horizon_cents", "frozen_due_cents",
            "contractual_cents", "uncollected_horizon_cents", "stayed_claim_cents",
            "not_yet_due_cents", "petition_p", "preference_exposed_cents", "drawn_cents")


def declared(snapshot_id: str, inputs: dict, model: dict) -> list[dict]:
    """The central setting first, then the case's declared variants, each as the one change it makes: a common-model
    scenario name, or {parameter: its sensitivity} for the chains (a named sensitivity stays a string)."""
    spec = json.loads((CASES_DIR / snapshot_id / "scenario.json").read_text()).get("assumption_variants") or {}
    common = inputs.get("common_model") or {}
    out = [{"id": "central", "label": spec.get("central_label", "Central setting"), "basis": common.get("note", ""),
            "central": True, "scenario": "central", "sens": {}}]
    for v in spec.get("variants", []):
        if "common_model" in v:
            s = common["scenarios"][v["common_model"]]
            out.append({"id": v["id"], "label": v["label"], "basis": s["basis"], "central": False,
                        "scenario": v["common_model"], "sens": {}})
            continue
        params = {k: model["parameters"][k] for k in v["parameters"]}
        missing = [k for k, p in params.items() if "sensitivity" not in p]
        if missing:
            raise ValueError(f"{v['id']}: parameters {missing} declare no sensitivity")
        out.append({"id": v["id"], "label": v["label"], "basis": " ".join(p["basis"] for p in params.values()),
                    "central": False, "scenario": "central",
                    "sens": {k: named(v, k, p) for k, p in params.items()}})
    return out


def named(v: dict, k: str, p: dict):
    """A variant's setting of parameter k: its declared sensitivity (a list of parameters), or the one it names (a
    {parameter: value} map), which must be the declared sensitivity or one of them. A string stays named; a figure
    of a list is named by its value; a single non-string sensitivity is True (the chains' `sens`)."""
    sens = p["sensitivity"]
    if isinstance(v["parameters"], dict):
        x = v["parameters"][k]
        if x != sens and not (isinstance(sens, list) and x in sens):
            raise ValueError(f"{v['id']}: {k} = {x!r} is not a declared sensitivity ({sens!r})")
        return x if isinstance(x, str) or isinstance(sens, list) else True
    if isinstance(sens, list):
        raise ValueError(f"{v['id']}: {k} declares several sensitivities; name one ({{parameter: value}})")
    return sens if isinstance(sens, str) else True


def out_dir(run_id: str) -> Path:
    return VAR / "analysis" / run_id / "assumptions"


def tiles(r, probs: np.ndarray) -> dict[str, float]:
    """The page's tile figures (page.path_scalars, as page.js `expect` reads them) under `probs`: whole cents, and the
    filing chance to six places."""
    from app.analysis.page import path_scalars

    return {k: (round(float(probs @ v), 6) if k == "petition_p" else round(float(probs @ v)))
            for k, v in path_scalars(r).items()}


def raise_share(model) -> float:
    """The probability mass of paths on which the company raises equity at its cash floor."""
    return float(sum(p for c, p in zip(model.combos, model.probs(), strict=True) for path in c
                     if any(s[0] == "cash_floor" and s[2] == "raise_equity" for s in path.steps)))


def headline(a, model, probs: np.ndarray) -> dict:
    m = a.r.metrics(probs)
    ff = a.r.first_floor(probs)
    day = ff["median_day"]
    return {**{k: m[k] for k in HEADLINE}, "raise_share": raise_share(model), "survival_share": 1 - m["petition_p"],
            "floor_share": ff["share"],
            "first_floor_median": None if day is None else (a.setup.review + timedelta(days=day + 1)).isoformat()}


def run_variant(run_id: str, vid: str, root: Path | None = None) -> dict:
    """One variant: the tree, Jev's answers (re-asked where the facts changed), the analysis, and what the page needs.
    Writes <var>/analysis/<run>/assumptions/<id>.json and, for a non-central variant, its reweight state."""
    from app.agent import jev as jev_module
    from app.agent.jev import JevAdapter
    from app.analysis.build import judged_model, run_context, write_exchanges
    from app.analysis.core import Analysis
    from app.analysis.page import chart_view
    from app.analysis.setup import setup_from_inputs
    from app.config import judgment_provider

    root = root or ROOT / "runs" / "recorded"
    ctx = run_context(run_id, root)
    v = {x["id"]: x for x in declared(ctx["meta"]["snapshot_id"], ctx["inputs"], ctx["m"])}[vid]
    setup = setup_from_inputs(ctx["inputs"], ctx["review"], v["scenario"])
    jev_module.EXCHANGE_LOG = exchanges = []
    try:  # caps are settings: sized to re-ask every question of the tree
        jev = JevAdapter(provider=judgment_provider(), run_id=f"{run_id}-assumption-{vid}", use_cache=True)
        fc, model = judged_model(ctx, setup, jev, [], v["sens"])
    finally:
        jev_module.EXCHANGE_LOG = None
    a = Analysis(ctx["feed"], setup, model, sens=v["sens"], dispute_model=ctx["m"])
    probs, bprobs = model.probs(), model.bank_probs()
    event, ordinary = chart_view(a.r, probs, a.months), chart_view(a.bank_r, bprobs, a.months)
    usage = jev.usage_summary()
    entry = {"id": vid, "label": v["label"], "basis": v["basis"], "central": v["central"],
             "metrics": tiles(a.r, probs), "daily": event["daily"], "monthly": event["monthly"],
             "ordinary": {"metrics": tiles(a.bank_r, bprobs), "daily": ordinary["daily"],
                          "monthly": ordinary["monthly"]},
             "change": {"scenario": v["scenario"], "sens": v["sens"]},
             "paths": len(model.combos), "questions": len(model.judgments) + len(model.bank_judgments),
             "jev": {"questions": usage["requests"] + usage["cache_hits"], "cached": usage["cache_hits"],
                     "re_asked": usage["requests"], "spent_usd": usage["spent_usd"]},  # requests: new calls only
             "headline": headline(a, model, probs),
             "ordinary_headline": {k: x for k, x in a.bank_r.metrics(bprobs).items() if k in HEADLINE}}
    out = out_dir(run_id)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{vid}.json").write_text(json.dumps(entry, default=str) + "\n")
    write_exchanges(out / f"{vid}.jev_log.jsonl.gz", exchanges)
    if not v["central"]:  # the central setting's state is the run's own page_state
        with gzip.open(out / f"{vid}.page_state.pkl.gz", "wb", compresslevel=6) as fh:  # sparse: ~160 MB -> ~6 MB
            pickle.dump({"r": a.r.for_reweight(), "bank_r": a.bank_r, "model": model, "months": a.months}, fh,
                        protocol=pickle.HIGHEST_PROTOCOL)
    return entry


def load_state(run_id: str, vid: str) -> dict:
    with gzip.open(out_dir(run_id) / f"{vid}.page_state.pkl.gz", "rb") as fh:
        return pickle.load(fh)


PAGE_KEYS = ("id", "label", "basis", "central", "metrics", "daily", "monthly", "ordinary", "paths", "questions", "jev")


def assemble(run_id: str, root: Path | None = None) -> dict:
    """page.json `assumptions` (central first) and the compact assumptions.json beside the run, from the variants'
    saved results."""
    root = root or ROOT / "runs" / "recorded"
    run_dir = root / run_id
    page = json.loads((run_dir / "page.json").read_text())
    snap = page["meta"]["snapshot_id"]
    from app.analysis.build import _load_run
    from app.disputes.rules import load_model

    _, meta, inputs, _, _ = _load_run(run_id, root)
    order = [v["id"] for v in declared(snap, inputs, load_model(snap))]
    entries = [json.loads((out_dir(run_id) / f"{vid}.json").read_text()) for vid in order]
    page["assumptions"] = [{k: e[k] for k in PAGE_KEYS} for e in entries]
    (run_dir / "page.json").write_text(json.dumps(page, default=str) + "\n")
    c = entries[0]["headline"]
    diff = lambda h: {k: (h[k] - c[k] if isinstance(h[k], (int, float)) and isinstance(c[k], (int, float))  # noqa: E731
                          else None) for k in h if k != "first_floor_median"}
    compact = {"run_id": run_id, "note": "Each economic assumption changes one declared setting from the central "
               "case (spec §16.6). Amounts in cents; shares 0-1. due_horizon is every installment falling due by the "
               "horizon (the page's Due tile) = collected + past due + frozen due (due and unpaid at a filing); "
               "collection_rate = collected / due_horizon. contractual adds installments due after the horizon; the "
               "uncollected part of it is split into past due, stayed at a filing and not yet due.",
               "variants": [{"id": e["id"], "label": e["label"], "central": e["central"], "change": e["change"],
                             "paths": e["paths"], "jev": e["jev"], "headline": e["headline"],
                             "vs_central": None if e["central"] else diff(e["headline"])} for e in entries]}
    (run_dir / "assumptions.json").write_text(json.dumps(compact, indent=1, default=str) + "\n")
    return compact


if __name__ == "__main__":
    if sys.argv[2] == "assemble":
        assemble(sys.argv[1])
    else:
        e = run_variant(sys.argv[1], sys.argv[2])
        print(json.dumps({k: e[k] for k in ("id", "paths", "questions", "jev", "headline")}, default=str))
