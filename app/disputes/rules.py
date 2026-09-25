"""The host-owned dispute model contract and its cited rule parameters.

Windows, the supersedeas multiple, the collateral share, the settlement range and post-judgment interest are read from
`contracts/dispute_model.json`; the analysis (app/analysis/events.py) applies them. Jev never sets any of them.
"""

from __future__ import annotations

import json

from app.config import CONTRACTS

MODEL_PATH = CONTRACTS / "dispute_model.json"


def load_model() -> dict:
    return json.loads(MODEL_PATH.read_text())


def param(model: dict, key: str) -> int:
    return model["rules"][key]["value"]


def param_range(model: dict, key: str) -> tuple[int, int]:
    r = model["rules"][key]
    return r["lower"], r["upper"]
