"""Thin read-only viewer for recorded investigations (step 4b). Local only; no writes.

The polished credit workbench (decision, offers, curves) is step 6. This page makes the semantic
pipeline observable: what was asked, what the evidence says, how Jev interpreted it, what the agent
accepted, and what each accepted fact means for the cash model.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import RECORDED
from app.web import viewmodel

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app = FastAPI(title="Slope Sparse Events: investigation viewer")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {"runs": viewmodel.list_runs()})


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_page(request: Request, run_id: str) -> HTMLResponse:
    if "/" in run_id or ".." in run_id or not (RECORDED / run_id / "events.jsonl").exists():
        raise HTTPException(404, "No recorded run with that ID")
    try:
        view = viewmodel.build(run_id)
    except ValueError as e:  # broken hash chain or altered locked log
        raise HTTPException(409, f"Run failed verification: {e}") from e
    return templates.TemplateResponse(request, "run.html", {"v": view})
