"""The bank view's question on the page: a row asked on bank data alone, with its drill-down and a slider that
reweights the bank view only, the way the research rows reweight the research view."""

import numpy as np
import pytest


@pytest.fixture(scope="module")
def page():
    from akoustis_fixture import REVIEW, SETUP, SNAP, basis, judgment

    from app.analysis.core import Analysis, EventModel
    from app.analysis.page import CLASSES, class_matrix, page_payload
    from app.disputes.forecast import Forecaster, Judgment, neutral_map

    feed, b = basis()
    d = judgment(stage="judgment_entered", motions=(), components=(), financing=(),
                 amount=judgment().amount.model_copy(update={"value": 200_000_000}))
    fc = Forecaster([d], {}, borrower="Akoustis Technologies, Inc.", review=REVIEW, horizon=SETUP.horizon,
                    hydrate=lambda f: {}, setup=SETUP, basis=b)
    per, bank_paths = fc.all_paths(), fc.bank_paths()

    def judged(nodes, p):
        return {n.key: Judgment(key=n.key, instance_id=n.instance_id, node=n.node, question_id=n.question_id,
                                event=n.event, assumptions=n.assumptions, window=n.window,
                                distribution=dict(zip(n.branches, [p] + [(1 - p) / (len(n.branches) - 1)]
                                                      * (len(n.branches) - 1), strict=True))) for n in nodes.values()}
    js, bank_js = judged(fc.nodes, 0.5), judged(fc.bank_nodes, 0.4)
    m = EventModel({x.instance_id: x for x in fc.disputes}, js, per, fc.ordered(), neutral=neutral_map(js),
                   bank_paths=bank_paths, bank_judgments=bank_js)
    a = Analysis(feed, SETUP, m)
    p = page_payload(a, m, fc, borrower="Akoustis Technologies, Inc.", snapshot_id=SNAP, neutral=False)
    state = {"payload": p, "r": a.r, "bank_r": a.bank_r, "model": m, "months": a.months,
             "class_of_path": class_matrix(p["paths"]["class"], len(CLASSES))}
    return a, m, state


def _bank_probs(p, dist):
    """page.js bankProbs: each bank path's probability from its edges over the rows."""
    out = []
    for e in p["bank"]["edges"]:
        x = 1.0
        for j in range(0, len(e), 2):
            x *= dist[e[j]][e[j + 1]]
        out.append(x)
    return np.array(out)


def test_the_bank_question_is_a_row_asked_on_bank_data_alone_with_its_drill_down(page):
    _, m, state = page
    p = state["payload"]
    rows = [n for n in p["nodes"] if n.get("view") == "bank"]
    assert {n["key"] for n in rows} == set(m.bank_judgments) and rows
    for n in rows:
        assert n["decider"] == "Bank data" and n["sub"] == "Asked on bank data alone"
        tags = {s["tag"] for s in n["detail"]["steps"]}
        assert {"Law", "Calculation", "Jev"} <= tags and "Record" not in tags  # no research facts
        assert n["detail"]["answer"]["distribution"] == m.bank_judgments[n["key"]].distribution
        assert "dispute" not in " ".join(s["text"] for s in n["detail"]["steps"])
    assert all(e[j] >= len(p["nodes"]) - len(rows) for e in p["bank"]["edges"] for j in range(0, len(e), 2))


def test_the_bank_slider_changes_only_the_bank_view(page):
    from app.analysis.page import decode_probs, reweight

    a, m, state = page
    p = state["payload"]
    i = next(k for k, n in enumerate(p["nodes"]) if n.get("view") == "bank")
    node = p["nodes"][i]
    dist = [n["jev"] for n in p["nodes"]]
    moved = [1.0] + [0.0] * (len(node["branches"]) - 1)
    enc = {"keys": [n["key"] for n in p["nodes"] if n.get("view") != "bank"], "composites": p["composites"],
           "paths": p["paths"]["edges"]}
    research = {n["key"]: n["jev"] for n in p["nodes"] if n.get("view") != "bank"}
    override = {node["key"]: dict(zip(node["branches"], moved, strict=True))}
    # the research view's path probabilities and tiles do not move
    assert np.allclose(decode_probs(enc, research), m.probs(override), atol=1e-12)
    assert np.allclose(m.probs(override), m.probs(), atol=1e-15)
    # the bank view's do, exactly as the analysis computes them
    before, after = _bank_probs(p, dist), _bank_probs(p, dist[:i] + [moved] + dist[i + 1:])
    assert np.allclose(before, m.bank_probs(), atol=1e-12) and np.allclose(after, m.bank_probs(override), atol=1e-12)
    ps = p["bank"]["path_scalars"]
    tile = lambda probs: float(np.dot(probs, ps["petition_p"]))  # noqa: E731
    assert tile(before) == pytest.approx(p["bank"]["scalars"]["petition_p"], abs=1e-12)
    assert tile(after) == pytest.approx(a.bank_r.metrics(m.bank_probs(override))["petition_p"], abs=1e-4)
    assert tile(after) != pytest.approx(tile(before))
    # the server's reweight: the research series unchanged, the bank series reweighted
    base, out = reweight(state, {}), reweight(state, {node["key"]: moved})
    assert out["daily"] == base["daily"] and out["monthly"] == base["monthly"]
    assert out["bank"]["daily"]["petition_cum_p"] != base["bank"]["daily"]["petition_cum_p"]
    assert out["bank"]["daily"]["petition_cum_p"] == a.bank_r.daily(m.bank_probs(override),
                                                                    collected_q=False)["petition_cum_p"]
