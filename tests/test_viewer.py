"""The read-only viewer renders only verified recorded runs."""

import shutil

import pytest
from fastapi.testclient import TestClient

from app.config import RECORDED
from app.web import viewmodel
from app.web.app import app

RUNS = sorted(p.parent.name for p in RECORDED.glob("*/run.json"))


@pytest.mark.skipif(not RUNS, reason="no recorded runs")
def test_recorded_run_renders_with_its_causal_chain():
    c = TestClient(app)
    assert c.get("/").status_code == 200
    page = c.get(f"/runs/{RUNS[-1]}")
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
