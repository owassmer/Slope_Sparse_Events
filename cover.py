"""What the compiled pipeline and the viewer execute in Python (run under `coverage run --source=app`): pass 1 on two
draws, a reduce and the requests as sent over every question kind found, pass 2 on one draw, the review-date
constants and state under every declared scenario, and the viewer serving the recorded lead run with one reweight."""
import asyncio
import io
import json
import os
import sys
from pathlib import Path

import numpy as np

os.environ.setdefault("SLOPE_JUDGMENT_PROVIDER", "decisions")
os.environ.setdefault("OPENAI_DECISIONS_API_KEY", "not-sent")
os.environ.pop("SLOPE_JEV_CACHE_ONLY", None)  # nothing is sent: the request body is captured before the post
OUT = Path("out")
OUT.mkdir(exist_ok=True)
log = lambda *a: print(*a, flush=True)  # noqa: E731

from app.analysis.events import Chain  # noqa: E402
from app.exact import oracle, pass1, pass2, pool, question_keys, replay  # noqa: E402

forced = set(json.load(open("forced.json")))
DRAWS = (145, 300)
fc_all, d = oracle._forecaster()
fc, _ = oracle.context(DRAWS)
ch0 = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
ch0.instrument_cash()
tk = pass1.case_tables(fc_all, d, ch0)
qt = question_keys.case_tables(fc_all, d, ch0, tk)
log("tables")

# pass 1 and the members
parts = []
for r, draw in enumerate(DRAWS):
    mem, *_ = pass1.explore(ch0, r, tk, forced=forced)
    _, first = np.unique(mem.ids[:, 1], return_index=True)
    names = {int(i): pool.question_id(pool.render(mem.rows[j], mem.cls[j], qt)) for i, j in
             zip(mem.ids[first, 1], first, strict=True)}
    qn = np.array([names[int(i)] for i in mem.ids[:, 1]], dtype=np.int64)
    parts.append(dict(draw=np.full(mem.count, draw, np.int16), rows=mem.rows[:mem.count], cls=mem.cls[:mem.count],
                      ids=mem.ids[:mem.count], ints=mem.ints[:mem.count], floats=mem.floats[:mem.count], question=qn))
    log("pass1", draw, mem.count)
members = pool._ordered({k: np.concatenate([p[k] for p in parts]) for k in parts[0]})

# a reduce over a few questions of every kind
fc2, d2, qt2, tk2 = pool.context()
q = members["question"]
bounds = np.flatnonzero(q[1:] != q[:-1]) + 1
keep, seen = [], {}
for sel in np.split(np.arange(len(q)), bounds):
    kind = int(members["rows"][sel[0]][0])
    if seen.get(kind, 0) < 6:
        seen[kind] = seen.get(kind, 0) + 1
        keep.append(sel)
sub = {k: v[np.concatenate(keep)] for k, v in members.items()}
buf, shared = io.StringIO(), {}
pool.reduce(fc2, d2, qt2, sub, buf, log=lambda s: None, tk=tk2, shared=shared)
states = OUT / "states"
states.mkdir(exist_ok=True)
(states / "states-0.jsonl").write_text(buf.getvalue())
(states / "states-0.shared.json").write_text(json.dumps(shared))
log("reduce", len(buf.getvalue().splitlines()))

# the requests as sent
from app.agent import decisions  # noqa: E402
from app.exact import judge  # noqa: E402

sent = []


class _Stop(Exception):
    pass


async def _capture(self, body, model, response_model):
    sent.append(body)
    raise _Stop


decisions.DecisionsClient._post = _capture


async def render():
    from app.agent.jev import JevAdapter
    from app.agent.jev_profiles import DisputeProfile
    from app.config import judgment_provider
    from app.disputes.rules import load_model

    prof = DisputeProfile(JevAdapter(provider=judgment_provider(), run_id="cover", use_cache=False),
                          lambda kind, obj: None, parties=load_model("akoustis_20240514").get("case_parties"))
    for line, st in judge.questions(states):
        try:
            await prof.forecast(line["question_id"], st, (line["instance_id"], *line["findings"]),
                                tuple(line["branches"]))
        except _Stop:
            pass


asyncio.run(render())
log("rendered", len(sent))

# pass 2 on one draw, even odds
pass2.standin(states, OUT / "answers.jsonl")
np.savez(OUT / "range.npz", lo=np.zeros(ch0.N, np.int64), hi=np.zeros(ch0.N, np.int64))
try:
    pass2.line(145, 145, OUT / "answers.jsonl", sorted(forced), OUT / "p2", [OUT / "range.npz"], forced,
               heavy_dir=OUT / "heavy", log=log)
except (ValueError, LookupError) as e:  # a sample of questions: pass 2 stops at the first it was not given
    log("pass2 stopped:", str(e)[:200])

# the review-date constants and state under every declared scenario
from app.analysis.assumptions import declared  # noqa: E402
from app.analysis.setup import setup_from_inputs  # noqa: E402

ctx, setup, _ = oracle._run_context()
for v in declared(ctx["meta"]["snapshot_id"], ctx["inputs"], ctx["m"]):
    s2 = setup_from_inputs(ctx["inputs"], ctx["review"], v["scenario"])
    try:
        ch = Chain(d, s2, fc.m, fc.draws, v["sens"] or None)
        ch.instrument_cash()
        k, ln = replay.constants(ch, 0)
        replay.state(ch, 0, ln)
        log("scenario", v["id"], "ok")
    except Exception as e:  # noqa: BLE001 - recorded: what a scenario needs that the compiled walk lacks
        log("scenario", v["id"], "FAILED", type(e).__name__, str(e)[:200])

# the viewer: the recorded lead run and a reweight
from fastapi.testclient import TestClient  # noqa: E402

from app.web.app import app  # noqa: E402

c = TestClient(app)
run = "akoustis_20240514-agent_plus_jev-20260928T052641Z"
r = c.get(f"/runs/{run}")
log("page", r.status_code)
page = c.get(f"/runs/{run}/payload").json()
node = next(n for n in page["nodes"] if len(n.get("branches") or n.get("distribution") or []) >= 2)
k = len(node.get("branches") or node.get("distribution"))
r = c.post(f"/runs/{run}/reweight", json={"overrides": {node["key"]: [1.0 / k] * k}})
log("reweight", r.status_code, str(r.json())[:120])
r = c.post(f"/runs/{run}/reweight", json={"assumption": page["assumptions"][1]["id"], "overrides": {}})
log("reweight variant", r.status_code)
log("outcome", c.get(f"/runs/{run}/outcome").status_code,
    "investigation", c.get(f"/runs/{run}/investigation").status_code)
for route in [x.path for x in app.routes]:
    log("route", route)
sys.exit(0)
