"""The analysis page (default for a run) and the investigation record (secondary). Local only.

GET /runs/{id} shows the analysis when the run has one: evidence -> Jev judgment -> financial mechanism -> financial
impact, with controls. GET/POST /runs/{id}/analysis load and recalculate it (no agent or Jev call). The investigation
record stays available at /runs/{id}/investigation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import RECORDED
from app.web import viewmodel

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
app = FastAPI(title="Slope Sparse Events")
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


def runs_root() -> Path:
    return Path(os.environ.get("SLOPE_RUNS_ROOT", str(RECORDED)))


def _run_dir(run_id: str) -> Path:
    d = runs_root() / run_id
    if "/" in run_id or ".." in run_id or not d.is_dir():
        raise HTTPException(404, "No recorded run with that ID")
    return d


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {"runs": viewmodel.list_runs(runs_root())})


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_page(request: Request, run_id: str) -> HTMLResponse:
    d = _run_dir(run_id)
    if (d / "analysis.json").exists():
        data = json.loads((d / "analysis.json").read_text())
        data.pop("model", None)
        return templates.TemplateResponse(request, "analysis.html", {"run_id": run_id, "data": data,
                                                                      "has_record": (d / "events.jsonl").exists()})
    return investigation_page(request, run_id)


@app.get("/runs/{run_id}/investigation", response_class=HTMLResponse)
def investigation_page(request: Request, run_id: str) -> HTMLResponse:
    d = _run_dir(run_id)
    if not (d / "events.jsonl").exists():
        raise HTTPException(404, "No investigation record for that run")
    try:
        view = viewmodel.build(run_id, runs_root())
    except ValueError as e:  # broken hash chain or altered locked log
        raise HTTPException(409, f"Run failed verification: {e}") from e
    return templates.TemplateResponse(request, "run.html", {"v": view})


@app.get("/runs/{run_id}/analysis")
def get_analysis(run_id: str) -> JSONResponse:
    path = _run_dir(run_id) / "analysis.json"
    if not path.exists():
        raise HTTPException(404, "This run has no analysis; run `slope analyze --run <id>`")
    data = json.loads(path.read_text())
    data.pop("model", None)
    return JSONResponse(data)


EMPTY_BODY = Body(default_factory=dict)


@app.post("/runs/{run_id}/analysis")
def post_analysis(run_id: str, body: dict = EMPTY_BODY) -> JSONResponse:
    from app.analysis.build import recompute

    path = _run_dir(run_id) / "analysis.json"
    if not path.exists():
        raise HTTPException(404, "This run has no analysis")
    try:
        out = recompute(path, body.get("controls") or {}, body.get("overrides") or {})
    except (ValueError, TypeError, KeyError) as e:
        raise HTTPException(422, f"Invalid controls: {e}") from e
    return JSONResponse(out)


# --- the Akoustis development page (no recorded run yet: the pre-D record with neutral judgments) ---------------

_DEV = None


def _dev():
    global _DEV
    from app.analysis.page import DevPage

    if _DEV is None:
        _DEV = DevPage()
    if _DEV.state is None and _DEV.load() is None:
        raise HTTPException(404, "No development page yet: run `slope viewer --dev`")
    return _DEV


@app.get("/dev/akoustis", response_class=HTMLResponse)
def dev_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "page.html", {"data": _dev().state["payload"], "has_record": False,
                                                             "api": "/dev/akoustis"})


@app.get("/dev/akoustis/payload")
def dev_payload() -> JSONResponse:
    return JSONResponse(_dev().state["payload"])


@app.post("/dev/akoustis/reweight")
def dev_reweight(body: dict = EMPTY_BODY) -> JSONResponse:
    from app.analysis.page import reweight

    classes = body.get("classes")
    try:
        out = reweight(_dev().state, body.get("overrides") or {}, [int(c) for c in classes] if classes else None)
    except (ValueError, TypeError, KeyError) as e:
        raise HTTPException(422, f"Invalid probabilities: {e}") from e
    return JSONResponse(out)


@app.post("/dev/akoustis/settings")
def dev_settings(body: dict = EMPTY_BODY) -> JSONResponse:
    page = _dev()
    started = page.start(body.get("settings") or {})
    return JSONResponse({"started": started, "progress": page.progress})


@app.get("/dev/akoustis/progress")
def dev_progress() -> JSONResponse:
    return JSONResponse(_dev().progress)
