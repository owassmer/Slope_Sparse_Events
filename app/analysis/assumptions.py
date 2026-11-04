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
from datetime import timedelta
from pathlib import Path

import numpy as np

from app.config import CASES_DIR, VAR

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


def load_state(run_id: str, vid: str) -> dict:
    with gzip.open(out_dir(run_id) / f"{vid}.page_state.pkl.gz", "rb") as fh:
        return pickle.load(fh)


PAGE_KEYS = ("id", "label", "basis", "central", "metrics", "daily", "monthly", "ordinary", "paths", "questions", "jev")

