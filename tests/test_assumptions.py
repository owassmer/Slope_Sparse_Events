"""Economic-assumption variants (spec §16.6): the central entry is the run's own page, and each variant changes one
declared setting from central and nothing else."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date

import numpy as np
import pytest

from app.analysis.assumptions import declared
from app.analysis.setup import setup_from_inputs
from app.config import CASES_DIR, ROOT
from app.disputes.rules import load_model

SNAP = "akoustis_20240514"
RUN = ROOT / "runs" / "recorded" / "akoustis_20240514-agent_plus_jev-20260928T052641Z"
REVIEW = date(2024, 5, 14)


def _inputs() -> dict:
    return json.loads((CASES_DIR / SNAP / "run_inputs.json").read_text())


def _page_expect(page: dict) -> dict:
    """page.js pathProbs -> expect on page.json alone, rounded as the variants' metrics are."""
    nodes = [np.asarray(n["jev"], dtype=np.float64) for n in page["nodes"]]
    comp = []
    for conj in page["composites"]:
        y = min(max(sum(np.prod([nodes[n][b] for n, b in c]) for c in conj), 0.0), 1.0)
        comp.append((y, 1 - y))
    probs = np.array([np.prod([nodes[e[j]][e[j + 1]] if e[j] >= 0 else comp[-e[j] - 1][e[j + 1]]
                               for j in range(0, len(e), 2)]) for e in page["paths"]["edges"]])
    return {k: (round(float(probs @ np.asarray(v)), 6) if k == "petition_p" else round(float(probs @ np.asarray(v))))
            for k, v in page["paths"]["scalars"].items()}


def test_each_variant_differs_from_central_only_through_its_knob() -> None:
    inputs, m = _inputs(), load_model(SNAP)
    spec = json.loads((CASES_DIR / SNAP / "scenario.json").read_text())["assumption_variants"]["variants"]
    vs = declared(SNAP, inputs, m)
    assert [v["id"] for v in vs] == ["central"] + [v["id"] for v in spec] and vs[0]["central"]
    central = asdict(setup_from_inputs(inputs, REVIEW))
    for v, d in zip(vs[1:], spec, strict=True):
        s = asdict(setup_from_inputs(inputs, REVIEW, v["scenario"]))
        changed = {k for k in s if s[k] != central[k]}
        if "common_model" in d:  # one Setup setting, the scenario's own; no chain parameter
            knob = set(inputs["common_model"]["scenarios"][d["common_model"]]) - {"basis"}
            assert changed == knob and len(knob) == 1 and v["sens"] == {}, v["id"]
        else:  # the declared parameters at their sensitivities; the Setup is central's
            assert changed == set() and set(v["sens"]) == set(d["parameters"]), v["id"]
            for k, x in v["sens"].items():
                sens = m["parameters"][k]["sensitivity"]
                assert x == (sens if isinstance(sens, str) else True) and sens != m["parameters"][k]["value"]
        assert v["label"] and v["basis"]


@pytest.mark.skipif(not (RUN / "page.json").exists(), reason="the lead run's page")
def test_the_central_entry_equals_the_runs_page() -> None:
    page = json.loads((RUN / "page.json").read_text())
    entries = page["assumptions"]
    c = entries[0]
    assert c["id"] == "central" and c["central"] and not any(e["central"] for e in entries[1:])
    assert c["metrics"] == _page_expect(page)
    assert c["daily"] == page["event"]["daily"] and c["monthly"] == page["event"]["monthly"]
    assert c["ordinary"]["daily"] == page["bank"]["daily"] and c["ordinary"]["monthly"] == page["bank"]["monthly"]
    assert c["jev"]["re_asked"] == 0  # the central questions are the run's own, from the cache
    for e in entries:
        assert set(e["metrics"]) == set(c["metrics"]) and set(e["daily"]) == set(c["daily"])
        assert [r["month"] for r in e["monthly"]] == [r["month"] for r in c["monthly"]]


@pytest.mark.skipif(not (RUN / "assumptions.json").exists(), reason="the lead run's assumptions")
def test_each_saved_variant_records_only_its_knob() -> None:
    saved = {v["id"]: v for v in json.loads((RUN / "assumptions.json").read_text())["variants"]}
    for v in declared(SNAP, _inputs(), load_model(SNAP)):
        assert saved[v["id"]]["change"] == {"scenario": v["scenario"], "sens": v["sens"]}


@pytest.mark.skipif(not (RUN / "page.json").exists(), reason="the lead run's page")
def test_a_variants_saved_state_reweights_to_its_saved_series() -> None:
    from app.analysis.assumptions import load_state, out_dir
    from app.analysis.page import reweight

    page = json.loads((RUN / "page.json").read_text())
    for e in page["assumptions"][1:]:
        if not (out_dir(RUN.name) / f"{e['id']}.page_state.pkl.gz").exists():
            pytest.skip("the variants' reweight states are built by `python -m app.analysis.assumptions`")
        state = load_state(RUN.name, e["id"])
        out = reweight(state, {})
        assert out["daily"] == e["daily"] and out["monthly"] == e["monthly"], e["id"]
        assert {k: round(v, 6) if k == "petition_p" else round(v) for k, v in out["metrics"].items()} == e["metrics"]
        key = next(iter(state["model"].judgments))  # a slider moves the variant's own paths
        n = len(state["model"].judgments[key].distribution)
        assert reweight(state, {key: [1.0] + [0.0] * (n - 1)})["daily"] is not None
