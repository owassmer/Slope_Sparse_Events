"""The read-only viewer renders only verified recorded runs."""

import json
import shutil

import pytest
from fastapi.testclient import TestClient

from app.config import RECORDED
from app.web import viewmodel
from app.web.app import app

RUNS = sorted(p.parent.name for p in RECORDED.glob("*/run.json"))
WITH_EFFECTS = [r for r in RUNS if json.loads((RECORDED / r / "run.json").read_text()).get("graph_counts", {}).get("effects")]


@pytest.mark.skipif(not WITH_EFFECTS, reason="no recorded run with effects")
def test_recorded_run_renders_with_its_causal_chain():
    c = TestClient(app)
    assert c.get("/").status_code == 200
    assert all(c.get(f"/runs/{r}").status_code == 200 for r in RUNS)  # every recorded run renders (analysis or record)
    assert all(c.get(f"/runs/{r}/investigation").status_code == 200 for r in RUNS)  # every record verifies
    page = c.get(f"/runs/{WITH_EFFECTS[-1]}/investigation")
    assert page.status_code == 200
    for text in ("event chain verified", "Model consequence", "Agent finding", "Source"):
        assert text in page.text
    assert c.get("/runs/..%2Fetc").status_code == 404 and c.get("/runs/nope").status_code == 404


@pytest.mark.skipif(not RUNS, reason="no recorded runs")
def test_tampered_run_fails_verification(tmp_path):
    shutil.copytree(RECORDED / RUNS[-1], tmp_path / RUNS[-1])
    events = tmp_path / RUNS[-1] / "events.jsonl"
    lines = events.read_text().splitlines()
    i = next(n for n, line in enumerate(lines) if '"question":"' in line)
    lines[i] = lines[i].replace('"question":"', '"question":"(edited) ', 1)
    events.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError):
        viewmodel.build(RUNS[-1], root=tmp_path)


# The analysis page ------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def small_page():
    """The page payload on the $2M small judgment (tests/test_analysis.py SMALL), neutral judgments."""
    from akoustis_fixture import REVIEW, SETUP, SNAP, basis, judgment

    from app.analysis.core import Analysis, EventModel, stress
    from app.analysis.page import CLASSES, class_matrix, page_payload
    from app.disputes.forecast import Forecaster, Judgment, neutral_map
    from app.finance.bank import load_feed

    feed, b = basis()
    d = judgment(stage="judgment_entered", motions=(), components=(), financing=(),
                 amount=judgment().amount.model_copy(update={"value": 200_000_000}))
    fc = Forecaster([d], {}, borrower="Akoustis Technologies, Inc.", review=REVIEW, horizon=SETUP.horizon,
                    hydrate=lambda f: {}, setup=SETUP, basis=b)
    per = fc.all_paths()
    js = {n.key: Judgment(key=n.key, instance_id=n.instance_id, node=n.node, question_id=n.question_id,
                          event=n.event, assumptions=n.assumptions, window=n.window,
                          distribution={x: 1 / len(n.branches) for x in n.branches}) for n in fc.nodes.values()}
    m = EventModel({x.instance_id: x for x in fc.disputes}, js, per, fc.ordered(), neutral=neutral_map(js))
    a = Analysis(feed, SETUP, m)
    rows = [{"index": r["index"], **r} for r in stress(load_feed(SNAP), SETUP, m)]
    p = page_payload(a, m, fc, borrower="Akoustis Technologies, Inc.", snapshot_id=SNAP, neutral=True, stress_rows=rows)
    state = {"payload": p, "r": a.r, "bank_r": a.bank_r, "model": m, "months": a.months,
             "class_of_path": class_matrix(p["paths"]["class"], len(CLASSES))}
    return a, m, state


def test_the_page_renders_from_its_payload_and_reweights(small_page, monkeypatch):
    from app.analysis.page import DevPage
    from app.web import app as web

    _, _, state = small_page
    page = DevPage()
    page.state = state
    monkeypatch.setattr(web, "_DEV", page)
    c = TestClient(app)
    r = c.get("/dev/akoustis")
    assert r.status_code == 200 and "page-data" in r.text and "Bank data only" in r.text
    assert state["payload"]["meta"]["judgments"] == "neutral" and "No Jev answers yet" in r.text
    node = state["payload"]["nodes"][0]
    out = c.post("/dev/akoustis/reweight", json={"overrides": {node["key"]: [0.9] + [0.1 / (len(node["branches"]) - 1)]
                                                               * (len(node["branches"]) - 1)}}).json()
    assert len(out["daily"]["outstanding_mean"]) == len(state["payload"]["dates"]) and out["monthly"]


def test_the_browser_reweight_arithmetic_matches_the_analysis_for_one_override(small_page):
    """What page.js computes from the payload (path probabilities from the encoded edges, the slider's
    renormalisation, tile expectations from per-path means) equals the analysis under the same override."""
    import numpy as np

    from app.analysis.page import decode_probs, reweight, with_branch

    a, m, state = small_page
    p = state["payload"]
    enc = {"keys": [n["key"] for n in p["nodes"]], "composites": p["composites"], "paths": p["paths"]["edges"]}
    i = max(range(len(p["nodes"])), key=lambda k: len(p["nodes"][k]["branches"]))  # a choice node if there is one
    node = p["nodes"][i]
    moved = with_branch(node["jev"], len(node["branches"]) - 1, 0.7)
    assert sum(moved) == pytest.approx(1.0) and moved[-1] == 0.7
    if len(moved) > 2:  # the other branches keep their proportions
        assert moved[0] / moved[1] == pytest.approx(node["jev"][0] / node["jev"][1])
    override = {node["key"]: dict(zip(node["branches"], moved, strict=True))}
    dist = {n["key"]: (moved if n["key"] == node["key"] else n["jev"]) for n in p["nodes"]}
    probs = decode_probs(enc, dist)
    assert np.allclose(probs, m.probs(override), atol=1e-12)
    ref = a.expected(m.probs(override))
    for k in ("collected", "unrecovered", "petition_p", "stayed", "preference", "peak_outstanding"):
        assert float(np.dot(probs, p["paths"]["scalars"][k])) == pytest.approx(ref[k], rel=1e-6, abs=1.0), k
    out = reweight(state, {node["key"]: moved})
    assert out["daily"]["outstanding_mean"] == a.r.daily(m.probs(override))["outstanding_mean"]


def test_filed_shares_sum_to_the_bankruptcy_probability(small_page):
    """Outcomes are classed by draw: the Filed shares on the bar equal the Bankruptcy probability tile, and every
    path's shares sum to one."""
    import numpy as np

    from app.analysis.page import CLASSES

    a, m, state = small_page
    p, cm = state["payload"], state["class_of_path"]
    assert np.allclose(cm.sum(axis=1), 1.0, atol=1e-4)
    filed = [i for i, (c, _) in enumerate(CLASSES) if c.startswith("filed")]
    for probs in (m.probs(), np.full(len(m.combos), 1 / len(m.combos))):
        tile = float(np.dot(probs, p["paths"]["scalars"]["petition_p"]))
        assert float(probs @ cm[:, filed].sum(axis=1)) == pytest.approx(tile, abs=1e-12)
        assert tile == pytest.approx(a.expected(probs)["petition_p"], abs=1e-4)


def test_the_collections_table_reconciles(small_page):
    """Cumulatively, due - collected = past due + frozen (installments already due); frozen (due) + frozen (not yet
    due) on the last row = the Frozen tile; past due + both frozen columns = the Unrecovered tile (to a few cents)."""
    import numpy as np

    a, m, state = small_page
    p = state["payload"]
    for view in ("event", "bank"):
        rows = p[view]["monthly"]
        due = coll = 0
        for r in rows:
            due, coll = due + r["due"], coll + r["collected"]
            assert abs((due - coll) - (r["past_due"] + r["frozen_due"])) <= 2, (view, r["month"])
        if view == "event":
            probs = m.probs()
            frozen, unrec = (float(np.dot(probs, a.r.means[k])) for k in ("stayed", "unrecovered"))
        else:
            frozen, unrec = (p["bank"]["scalars"][k] for k in ("stayed", "unrecovered"))
        last = rows[-1]
        assert abs(last["frozen_due"] + last["frozen_not_due"] - frozen) <= 2, view
        assert abs(last["past_due"] + last["frozen_due"] + last["frozen_not_due"] - unrec) <= 2, view
