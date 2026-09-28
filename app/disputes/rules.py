"""The host-owned dispute model contract and its cited rule parameters.

Windows, the supersedeas multiple, the collateral share, the settlement range and post-judgment interest are read from
`contracts/dispute_model.json`; the analysis (app/analysis/events.py) applies them. Jev never sets any of them.
"""

from __future__ import annotations

import json

from app.config import CASES_DIR, CONTRACTS

MODEL_PATH = CONTRACTS / "dispute_model.json"


def load_model(snapshot_id: str | None = None) -> dict:
    """The contract; with a snapshot id, its parameters overlaid by the case's scenario parameters
    (cases/<snapshot>/scenario.json), where the case has any. The case's labels ride along under `case_labels`."""
    m = json.loads(MODEL_PATH.read_text())
    path = CASES_DIR / snapshot_id / "scenario.json" if snapshot_id else None
    if path is not None and path.exists():
        scen = json.loads(path.read_text())
        for k, v in scen.get("parameters", {}).items():
            m["parameters"][k] = {**m["parameters"].get(k, {}), **v}
        m["case_labels"] = scen.get("labels", {})
        if "verdict_form" in scen:  # the money-bearing questions of the case's verdict form (template verdict_form)
            m["case_verdict_form"] = scen["verdict_form"]
    return m


def compatible(model: dict, version: str) -> bool:
    """Whether disputes interpreted under `version` build under this contract (additive versions)."""
    return version == model["model_version"] or version in model.get("compatible_versions", [])


def param(model: dict, key: str) -> int:
    return model["rules"][key]["value"]


def param_range(model: dict, key: str) -> tuple[int, int]:
    r = model["rules"][key]
    return r["lower"], r["upper"]
