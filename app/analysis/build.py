"""Build the analysis of a recorded investigation, and recompute it for the page's controls.

`build` asks Jev for the conditional forecasts (cached; `refresh` re-asks), simulates Slope's reusable line, and
writes the sidecar artifacts beside the recorded run: analysis.json (views, lead-time series, the three-step
attribution, judgment sensitivity, stress), collections.csv and cashflows.csv. `recompute` reuses the stored
judgments: a probability override reweights the existing trajectories, a financial control (fee, line usage, limit
multiplier, variability, ...) re-simulates with the same seeds; neither calls the agent or Jev.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import re
import resource
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import date
from functools import lru_cache
from pathlib import Path

from app.analysis.core import Analysis, EventModel, dates, stress
from app.analysis.events import BANK, Basis, coupon_terms
from app.analysis.setup import (
    QUIET_DEFAULTS,
    Exposure,
    Setup,
    controls_from_json,
    controls_json,
    exposure_from_json,
    exposure_json,
    setup_from_inputs,
)
from app.config import VAR, question_registry
from app.disputes.forecast import DisputePath, Forecaster, Judgment, neutral_map
from app.disputes.hydrate import evidence_state
from app.disputes.rules import compatible, load_model
from app.domain.investigation import AtomicFinding, DisputeInstance
from app.domain.values import usd
from app.finance.bank import BankFeed, load_feed

MECHANISM = {
    "settle_before_ruling": "A settlement before the ruling: 60%-100% of the amount, paid inside the settlement window.",
    "amount_fixed": "The court fixing the amount starts the post-judgment clock: the appeal deadline, the stay, payment or enforcement.",
    "settle_after_judgment": "A settlement after judgment: 60%-100% of the amount, paid before the appeal deadline.",
    "appeal": "An appeal routes the obligation to a secured stay, or to payment and enforcement.",
    "secured_stay": "A secured stay encumbers cash or credit capacity from the stay until the appeal ends or settles.",
    "security_form": "A cash deposit locks the amount; a surety bond locks 50%-100% of a bond at 125% as collateral; a letter of credit commits 125% of credit capacity and locks no cash.",
    "settle_during_appeal": "A settlement during the appeal: 60%-100% of the amount, and the exact encumbrance released on that date.",
    "voluntary_payment": "Payment of the amount (plus post-judgment interest unless already included) inside the payment window.",
    "enforcement": "Collection of the amount by enforcement inside the enforcement window.",
}


LABEL = {"settle_before_ruling": "Settle before the ruling", "amount_fixed": "Court fixes the amount",
         "settle_after_judgment": "Settle after judgment", "appeal": "Appeal filed", "secured_stay": "Secured stay",
         "security_form": "Form of security", "settle_during_appeal": "Settle during the appeal",
         "voluntary_payment": "Pays voluntarily", "enforcement": "Collected by enforcement"}
CONDITION = {("settle_before_ruling", ""): "", ("amount_fixed", ""): "if no settlement first",
             ("settle_after_judgment", ""): "once the amount is fixed", ("appeal", ""): "once the amount is fixed",
             ("secured_stay", ""): "if it appeals", ("security_form", ""): "if it secures a stay",
             ("settle_during_appeal", ""): "during a secured appeal",
             ("voluntary_payment", "no_appeal"): "if it does not appeal",
             ("voluntary_payment", "appeal_unsecured"): "if it appeals without a secured stay",
             ("enforcement", "no_appeal"): "if still unpaid (no appeal)",
             ("enforcement", "appeal_unsecured"): "if still unpaid (appeal, no secured stay)"}
NATURE_SHORT = {"fee_and_cost_award": "fee award", "money_judgment": "judgment", "damages_award": "damages award",
                "settlement_payment": "settlement payment"}


def short_name(name: str) -> str:
    return re.split(r"[ ,]", name.strip())[0]


def dispute_short(d: DisputeInstance, borrower: str) -> str:
    court = re.split(r"\s\d", d.order_reference.strip())[0].strip(" ,")
    payer = borrower if d.borrower_role == "debtor" else d.counterparty
    return f"{court} {NATURE_SHORT.get(d.nature, d.nature)} · {short_name(payer)} pays"


def _dispute_meta(d: DisputeInstance, borrower: str, model: dict) -> dict:
    payer, payee = (borrower, d.counterparty) if d.borrower_role == "debtor" else (d.counterparty, borrower)
    amount = d.amount.value if d.amount.value is not None else d.amount.upper
    return {"id": d.instance_id, "title": d.title, "short": dispute_short(d, borrower), "reference": d.order_reference,
            "nature": model["natures"].get(d.nature, d.nature), "payer": payer, "payee": payee,
            "borrower_role": d.borrower_role, "amount_cents": amount,
            "amount_status": model["readings"]["amount_status_labels"].get(d.amount_status, d.amount_status),
            "stage": d.stage, "status": d.status,
            "established": [{"event": model["readings"]["events"][k], "finding": v.finding_id, "date": v.source_date,
                             "quote": v.quote} for k, v in d.established.items()],
            "factors": [{"label": f.label, "reading": f.level_label, "distribution": f.distribution,
                         "probability": f.probability, "conflict": f.conflict,
                         "finding": f.decisive.finding_id if f.decisive else None,
                         "quote": f.decisive.quote if f.decisive else None}
                        for f in d.factors if f.distribution or f.probability is not None]}


def _judgment_meta(j: Judgment, disputes: dict[str, DisputeInstance], order: dict[str, DisputeInstance | None],
                   borrower: str) -> dict:
    q = next(x for x in question_registry()["questions"] if x["id"] == j.question_id)
    context = j.key.split(":", 1)[1].split("|")
    ctx = next((c for c in context[1:] if not c.startswith("cls=")), "")
    cls = next((c[4:] for c in context[1:] if c.startswith("cls=")), "")
    cond = [CONDITION.get((j.node, ctx), "")]
    if disputes[j.instance_id].stage != "amount_pending" and cond[0] == "once the amount is fixed":
        cond = [""]  # the judgment is already entered
    if disputes[j.instance_id].stage == "appeal_filed" and cond[0] == "if it appeals":
        cond = [""]  # the appeal is already filed
    parent = order.get(j.instance_id)
    if cls and parent is not None:
        who = short_name(parent.counterparty)
        cond.append({"counterparty_receives": f"{who} is paid in the {dispute_short(parent, borrower).split(' ·')[0]}",
                     "counterparty_pays": f"{who} pays in the {dispute_short(parent, borrower).split(' ·')[0]}",
                     "no_cash": f"nothing paid in the {dispute_short(parent, borrower).split(' ·')[0]}"}[cls])
    return {"key": j.key, "dispute": j.instance_id, "dispute_title": disputes[j.instance_id].title,
            "dispute_short": dispute_short(disputes[j.instance_id], borrower), "node": j.node,
            "label": LABEL.get(j.node, j.event), "condition": "; ".join(c for c in cond if c),
            "question": q["question"], "event": j.event, "assumptions": list(j.assumptions), "window": j.window,
            "distribution": j.distribution, "confidence": j.confidence, "evidence": j.evidence,
            "readings": j.readings, "mechanism": MECHANISM.get(j.node, "")}


def _model_json(m: EventModel) -> dict:
    return {"disputes": {k: d.model_dump(mode="json") for k, d in m.disputes.items()},
            "judgments": {k: asdict(j) for k, j in m.judgments.items()},
            "paths": {i: {c: [{"steps": [list(s) for s in p.steps], "outcome": p.outcome,
                              "edges": [list(e) for e in p.edges], "cls": p.cls, **_mask_json(p)} for p in ps]
                          for c, ps in cl.items()} for i, cl in m.per.items()},
            "order": [[d.instance_id, parent.instance_id if parent else None] for d, parent in m.order],
            "neutral": m.neutral,
            "bank": {"judgments": {k: asdict(j) for k, j in m.bank_judgments.items()},
                     "paths": [{"steps": [list(s) for s in p.steps], "outcome": p.outcome,
                                "edges": [list(e) for e in p.edges], **_mask_json(p)} for p in m.bank_paths]}}


def _mask_json(p: DisputePath) -> dict:
    """A path's draws (DisputePath.mask, hex), only where it follows some of them (a grouped question's fork)."""
    return {} if p.mask is None else {"mask": p.mask.hex()}


def _judgments(data: dict) -> dict[str, Judgment]:
    return {k: Judgment(**{**v, "assumptions": tuple(v["assumptions"]), "finding_ids": tuple(v["finding_ids"])})
            for k, v in data.items()}


def model_from_json(data: dict) -> EventModel:
    disputes = {k: DisputeInstance.model_validate(v) for k, v in data["disputes"].items()}
    judgments = _judgments(data["judgments"])
    bank = data.get("bank") or {"judgments": {}, "paths": []}
    mask = lambda p: bytes.fromhex(p["mask"]) if p.get("mask") else None  # noqa: E731
    bank_paths = [DisputePath(instance_id=BANK, steps=tuple(tuple(s) for s in p["steps"]), outcome=p["outcome"],
                              edges=tuple(tuple(e) for e in p["edges"]), mask=mask(p)) for p in bank["paths"]]
    per = {i: {c: [DisputePath(instance_id=i, steps=tuple(tuple(s) for s in p["steps"]), outcome=p["outcome"],
                               edges=tuple(tuple(e) for e in p["edges"]), cls=p["cls"], mask=mask(p)) for p in ps]
               for c, ps in cl.items()} for i, cl in data["paths"].items()}
    order = [(disputes[i], disputes[p] if p else None) for i, p in data["order"]]
    return EventModel(disputes, judgments, per, order, neutral=data.get("neutral"), bank_paths=bank_paths,
                      bank_judgments=_judgments(bank["judgments"]))


def _sens_rows(a: Analysis, model: EventModel, overrides: dict | None) -> list[dict]:
    rows = []
    for r in a.judgment_sensitivity(overrides):
        j = model.judgments[r["key"]]
        rows.append({"key": r["key"], "dispute_title": model.disputes[j.instance_id].title, "event": j.event,
                     "window": j.window, "assumptions": list(j.assumptions),
                     "jev": (overrides or {}).get(r["key"], j.distribution), "jev_model": j.distribution,
                     "variants": r["variants"], "at_jev": r["jev"], "range": r["range"]})
    return rows


def payload(feed: BankFeed, setup: Setup, model: EventModel, meta: dict, overrides: dict | None = None,
            analysis: Analysis | None = None, stressed: list | None = None) -> dict:
    a = analysis or Analysis(feed, setup, model)
    views = a.views(overrides)
    b, e = views["bank_only"]["metrics"], views["event_adjusted"]["metrics"]
    return {
        "setup": {"review": setup.review.isoformat(), "horizon": setup.horizon.isoformat(),
                  "funding": setup.funding.isoformat(), "invoice_due": setup.invoice_due.isoformat(),
                  "invoice_cents": setup.invoice_cents, "amount_cents": setup.amount_cents, "fee_bps": setup.fee_bps,
                  "installments": setup.installments, "days": setup.days, "discount_rate_bps": setup.discount_rate_bps,
                  "exposure_scale": setup.exposure_scale, "collateral_share": setup.collateral_share,
                  "variability": setup.variability, "draws": a.ops.draws,
                  "line": {"limit_share_bps": setup.limit_share_bps, "limit_multiplier": setup.limit_multiplier,
                           "effective_share_bps": setup.share_bps, "line_usage": setup.line_usage,
                           "limit_at_review_cents": int(a.line.limit[0, 0]), "fee_bps": setup.fee_bps,
                           "installments": setup.installments, "facility_cents": setup.facility_cents,
                           **({"opening_exposure": exposure_json(setup.exposure)}
                              if setup.exposure != Exposure() else {})},
                  "schedule": [{"due": p.due.isoformat(), "amount_cents": p.amount_cents, "principal_cents": p.principal_cents,
                                "fee_cents": p.fee_cents} for p in setup.offer.schedule(setup.funding)] if setup.amount_cents else []},
        "dates": dates(setup), "views": views, "delta": {k: e[k] - b[k] for k in b if isinstance(b[k], float) and isinstance(e[k], float)},
        "scenarios": a.scenarios(overrides),
        "attribution": a.attribution(overrides),
        "sensitivity": {"judgments": _sens_rows(a, model, overrides)},
        "stress": stressed if stressed is not None else stress(feed, setup, model, overrides, dispute_model=a.m),
        "overrides": overrides or {}, **meta,
    }


def meta_for(model: EventModel, borrower: str, not_modelled: list[dict]) -> dict:
    m = load_model()
    return {"borrower": borrower, "probability_label": m["probability_label"],
            "disputes": [_dispute_meta(d, borrower, m) for d in model.disputes.values()],
            "not_modelled": not_modelled,
            "judgments": {k: _judgment_meta(j, model.disputes, {d.instance_id: p for d, p in model.order}, borrower)
                          for k, j in model.judgments.items()}}


# --- recorded runs ------------------------------------------------------------------------------------

def basis_for(feed: BankFeed, setup: Setup) -> Basis:
    """The operating draws' cash, need and legal spend: the same simulation the analysis runs."""
    from app.analysis import operating
    from app.analysis.engine import prepare

    ops = operating.simulate_for(feed, setup)
    line = prepare(setup, ops)
    # the borrower's cash on the review date includes what an existing line's history moved (Setup.exposure)
    return Basis.of(ops, line.need, feed.available_cents + setup.exposure.cash_cents, line=line)


def _load_run(run_id: str, root: Path):
    from app.agent.run_store import RunStore
    from app.evidence.store import EvidenceStore

    store = RunStore(run_id, root=root)
    meta = store.events[0].payload
    inputs = meta["run_inputs"]
    evidence = EvidenceStore(meta["snapshot_id"])
    review = date.fromisoformat(str(evidence.snapshot_info()["cutoff"])[:10])
    return store, meta, inputs, evidence, review


def record_item_slots(run_id: str, root: Path, findings: dict, hydrate, refresh: bool,
                      version: str | None = None, recorded: list | None = None) -> dict:
    """Which accepted findings supply each record item (app/disputes/slots.py), kept beside the run in slots.json.
    The agent's own records (RecordItemSlot) decide it where the run has them (spec §3.5); a run recorded before the
    agent was given the record items is read once by Jev, on its own adapter and budget."""
    from app.agent.jev import JevAdapter
    from app.disputes.rules import load_model
    from app.disputes.slots import from_agent, load, match, record_items

    path = root / run_id / "slots.json"
    nodes = record_items(load_model(), version)  # the nodes the disputes' interpretation version has
    if recorded:
        out = from_agent(recorded, nodes, set(findings))
        path.write_text(json.dumps(out, indent=1) + "\n")
        return out
    kept = load(path)
    if kept is not None and {n: list(v) for n, v in kept.items()} == {n: s["items"] for n, s in nodes.items()} \
            and not refresh:
        return kept
    jev = JevAdapter(run_id=f"{run_id}-slots", use_cache=not refresh, max_attempts=3000)
    out = asyncio.run(match(findings, hydrate, nodes, jev))
    path.write_text(json.dumps(out, indent=1) + "\n")
    pairs = [v for items in out.values() for v in items.values()]
    print("record items:", sum(1 for v in pairs if v), "of", len(pairs), "in the record; Jev", jev.usage_summary())
    return out


def build(run_id: str, root: Path, refresh: bool = False, *,
          progress: Callable[[str], None] | None = None) -> dict:
    """The run's analysis, page and Jev exchange log, written beside it."""
    from app.agent import jev as jev_module

    jev_module.EXCHANGE_LOG = exchanges = []  # the run's Jev requests and responses, written beside it
    try:
        return _build(run_id, root, refresh, exchanges, progress or (lambda stage: None))
    finally:
        jev_module.EXCHANGE_LOG = None


def run_context(run_id: str, root: Path, refresh: bool = False) -> dict:
    """What the analysis of a recorded run reads before it asks Jev: the run's inputs, the live disputes with the
    instruments their terms reach, the dispute model with the case's parameters, the feed and the record-item slots."""
    store, meta, inputs, evidence, review = _load_run(run_id, root)
    setup = setup_from_inputs(inputs, review)
    borrower = inputs["baseline_profile"]["borrower"]
    findings: dict[str, AtomicFinding] = {k: f for k, f in store.graph["findings"].items() if f.status == "accepted"}
    live = [d for d in store.graph["disputes"].values() if d.status != "superseded"]
    m = load_model(meta["snapshot_id"])  # the contract, with the case's scenario parameters where it has any
    current = m["model_version"]
    stale = sorted({d.model_version for d in live if not compatible(m, d.model_version)})
    if stale and not refresh:
        raise RuntimeError(f"{run_id}: disputes were interpreted under dispute model {', '.join(stale)}, not the current "
                           f"{current}; their readings do not fit the current tree. Re-interpret the run, or pass "
                           f"refresh to build anyway.")
    sources = {s["source_id"]: (s["title"], s["available_at"][:10]) for s in evidence.list_sources()}
    feed = load_feed(meta["snapshot_id"])
    instruments = [f for f in store.graph.get("financing", {}).values() if f.status != "superseded"]
    instruments = [coupon_terms(f, [s.quote for fid in f.finding_ids if fid in findings for s in findings[fid].spans],
                                review, setup.horizon) for f in instruments]  # the coupon: arithmetic on its quote
    live = [d.model_copy(update={"financing": tuple(f for f in instruments if d.instance_id in f.dispute_ids)})
            for d in live]  # the instruments each judgment's terms reach (dispute model 4.0.0)
    hydrate = lambda f: evidence_state(evidence, f, [], sources)["passage"]  # noqa: E731
    version = max((d.model_version for d in live), key=lambda v: tuple(map(int, v.split("."))), default=current)
    slots = record_item_slots(run_id, root, findings, hydrate, refresh, version,
                              list(store.graph.get("record_items", {}).values()))
    return {"meta": meta, "inputs": inputs, "review": review, "setup": setup, "borrower": borrower,
            "findings": findings, "live": live, "m": m, "feed": feed, "hydrate": hydrate, "slots": slots}


WALK_PROCESSES = {"akoustis_20240514": 4}  # snapshot -> processes walking its dispute tree (default: one)


def walk_processes(snapshot_id: str) -> int:
    """The build setting walk_processes: the case's value, or the environment's SLOPE_WALK_PROCESSES."""
    env = os.environ.get("SLOPE_WALK_PROCESSES")
    return int(env) if env else WALK_PROCESSES.get(snapshot_id, 1)


def judged_model(ctx: dict, setup: Setup, jev, records: list, sens: dict | None = None, *,
                 progress: Callable[[str], None] | None = None) -> tuple[Forecaster, EventModel]:
    """The tree for `setup` (and the chains' parameter sensitivities `sens`), with Jev's answer to every question.
    A question whose facts are unchanged is answered from Jev's cache; one whose facts changed is asked again."""
    from app.agent.jev_profiles import DisputeProfile

    fc = Forecaster(ctx["live"], ctx["findings"], borrower=ctx["borrower"], review=ctx["review"],
                    horizon=setup.horizon, hydrate=ctx["hydrate"], setup=setup,
                    basis=basis_for(ctx["feed"], setup),  # path facts are simulated before Jev is asked
                    slots=ctx["slots"], model=ctx["m"], sens=sens)
    if progress:
        progress("event_situations")
    procs = walk_processes(ctx["meta"]["snapshot_id"])
    if procs > 1:  # the pending claim's tree on a subtree queue (app/disputes/parallel.py): the single walk's result
        from app.disputes import parallel

        per = parallel.all_paths(fc, procs)
    else:
        per = fc.all_paths()
    bank_paths = fc.bank_paths()  # the bank view: the same distress decisions on the bank data alone
    judge = DisputeProfile(jev, lambda kind, obj: records.append({"kind": kind, **obj.model_dump(mode="json")}))

    async def ask_both() -> tuple[dict, dict]:  # one event loop: the adapter's HTTP client is bound to it
        return (await fc.judge(judge) if fc.nodes else {}), (await fc.judge_bank(judge) if fc.bank_nodes else {})

    if progress:
        progress("jev_forecasts")
    judgments, bank_judgments = asyncio.run(ask_both())
    model = EventModel({d.instance_id: d for d in fc.disputes}, judgments, per, fc.ordered(),
                       neutral=neutral_map(judgments), bank_paths=bank_paths, bank_judgments=bank_judgments)
    return fc, model


def say(text: str) -> None:
    """A progress line on stderr (the run's log)."""
    print(f"[{time.strftime('%H:%M:%S')}] {text}", file=sys.stderr, flush=True)


def _progress_log():
    """Analysis progress: each pass's time at 500 paths, every 5,000 and at its end."""
    start: dict = {}

    def tick(phase: str, done: int, total: int) -> None:
        start.setdefault(phase, time.time())
        if done in (501, total) or done % 5000 == 1:
            say(f"analysis {phase}: {min(done, total)}/{total} paths, {time.time() - start[phase]:.0f} s")
    return tick


def _build(run_id: str, root: Path, refresh: bool, exchanges: list[dict],
           progress: Callable[[str], None]) -> dict:
    from app.agent.jev import JevAdapter
    from app.config import judgment_provider

    progress("engine_context")
    ctx = run_context(run_id, root, refresh)
    meta, setup, borrower, feed, m = ctx["meta"], ctx["setup"], ctx["borrower"], ctx["feed"], ctx["m"]
    records: list = []
    jev = JevAdapter(provider=judgment_provider(), run_id=f"{run_id}-analysis", use_cache=not refresh)
    fc, model = judged_model(ctx, setup, jev, records, progress=progress)
    not_modelled = [{"title": d.title, "status": d.status, "requests": [r.action for r in d.evidence_requests]}
                    for d in ctx["live"] if d.status not in ("interpreted", "resolved")]
    say(f"tree: {len(model.combos)} paths, {len(model.bank_paths)} ordinary paths, {len(fc.nodes)} dispute "
        f"nodes, {len(fc.bank_nodes)} ordinary nodes; walk {getattr(fc, 'walk_stats', {})}; Jev {jev.usage_summary()}")
    progress("financial_analysis")
    t_a = time.time()
    a = Analysis(feed, setup, model, dispute_model=m, progress=_progress_log())
    say(f"analysis: {time.time() - t_a:.0f} s")
    data = payload(feed, setup, model, meta_for(model, borrower, not_modelled), analysis=a)
    data["model"] = _model_json(model)
    data["run_id"], data["snapshot_id"] = run_id, meta["snapshot_id"]
    data["base_setup"] = setup_json(setup)
    data["jev"] = jev.usage_summary()
    out = root / run_id
    text = json.dumps(data, default=str, separators=(",", ":")).encode() + b"\n"  # compact: C encoder
    (out / "analysis.json").write_bytes(text)
    (out / "analysis.json.gz").write_bytes(gzip.compress(text, compresslevel=6, mtime=0))  # committed; .json is not
    del text
    write_csv(data, out)
    scratch = VAR / "analysis" / run_id
    scratch.mkdir(parents=True, exist_ok=True)
    progress("page")
    state = run_page(a, model, fc, borrower=borrower, snapshot_id=meta["snapshot_id"], stress_rows=data["stress"])
    (out / "page.json").write_text(json.dumps(state["payload"], default=str) + "\n")
    save_page_state(run_id, state)
    write_exchanges(out / "jev_log.jsonl.gz", exchanges)
    say(f"done; peak RSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**30:.2f} GB (this process)")
    (scratch / "jev_records.jsonl").write_text("\n".join(json.dumps(r, default=str) for r in records) + "\n")
    return data


def run_page(a: Analysis, model: EventModel, fc: Forecaster, *, borrower: str, snapshot_id: str,
             stress_rows: list) -> dict:
    """The one-screen page for a recorded run (app/analysis/page.py), and the reduced state its reweight reads. The
    settings re-simulate the dev page only, so a run's page has none."""
    from app.analysis.page import page_payload

    neutral = not any(j.observation_id for j in model.judgments.values())  # no Jev answer at all: even odds
    p = page_payload(a, model, fc, borrower=borrower, snapshot_id=snapshot_id, neutral=neutral,
                     stress_rows=stress_rows)
    p["settings"] = []
    p["meta"]["snapshot_id"] = snapshot_id
    return {"payload": p, "r": a.r, "bank_r": a.bank_r, "model": model, "months": a.months}


def page_state_path(run_id: str) -> Path:
    return VAR / "analysis" / run_id / "page_state.pkl"


def save_page_state(run_id: str, state: dict) -> None:
    import pickle

    path = page_state_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:  # streamed: no second copy of the state in memory
        pickle.dump({**state, "r": state["r"].for_reweight()}, fh, protocol=pickle.HIGHEST_PROTOCOL)


def write_exchanges(path: Path, exchanges: list[dict]) -> None:
    """One JSON line per Jev exchange (request and raw response), in the order asked, gzipped."""
    text = "".join(json.dumps(e, default=str, sort_keys=True) + "\n" for e in exchanges)
    path.write_bytes(gzip.compress(text.encode(), mtime=0))


def read_analysis(run_dir: Path) -> dict | None:
    """The run's analysis: analysis.json where `slope analyze` ran here, else the committed analysis.json.gz."""
    if (run_dir / "analysis.json").exists():
        return json.loads((run_dir / "analysis.json").read_text())
    if (run_dir / "analysis.json.gz").exists():
        return json.loads(gzip.decompress((run_dir / "analysis.json.gz").read_bytes()))
    return None


def load_page_state(run_dir: Path) -> dict:
    """The run page's reweight state: from var/ if `slope analyze` ran here, else rebuilt once from the run's
    analysis.json and page.json (the same judgments, seeds and trajectories; no agent or Jev call)."""
    import pickle

    path = page_state_path(run_dir.name)
    if path.exists():
        with path.open("rb") as fh:
            return pickle.load(fh)
    data = read_analysis(run_dir)
    p = json.loads((run_dir / "page.json").read_text())
    model, setup = model_from_json(data["model"]), setup_from_json(data["base_setup"])
    a = Analysis(load_feed(data["snapshot_id"]), setup, model, dispute_model=load_model(data["snapshot_id"]))
    state = {"payload": p, "r": a.r, "bank_r": a.bank_r, "model": model, "months": a.months}
    save_page_state(run_dir.name, state)
    return state


def write_csv(data: dict, out: Path) -> None:
    ds, v = data["dates"], data["views"]
    rows = ["date,view,drawn_expected_cumulative_cents,contractual_due_expected_cumulative_cents,"
            "collected_expected_cumulative_cents,collected_p5_cents,collected_p50_cents,collected_p95_cents,"
            "outstanding_principal_expected_cents,limit_expected_cents,limit_p5_cents,petition_cumulative_p"]
    for view in ("bank_only", "event_adjusted"):
        d = v[view]["daily"]
        rows += [f"{ds[t]},{view},{d['drawn_mean'][t]},{d['contractual'][t]},{d['collected_mean'][t]},"
                 f"{d['collected_p5'][t]},{d['collected_p50'][t]},{d['collected_p95'][t]},{d['outstanding_mean'][t]},"
                 f"{d['limit_mean'][t]},{d['limit_p5'][t]},{d['petition_cum_p'][t]:.6f}" for t in range(len(ds))]
    (out / "collections.csv").write_text("\n".join(rows) + "\n")
    rows = ["date,view,available_cash_expected_cents,available_cash_p5_cents,available_cash_p50_cents,"
            "available_cash_p95_cents,cash_locked_expected_cents,credit_capacity_committed_expected_cents"]
    for view in ("bank_only", "event_adjusted"):
        d = v[view]["daily"]
        rows += [f"{ds[t]},{view},{d['cash_mean'][t]},{d['cash_p5'][t]},{d['cash_p50'][t]},{d['cash_p95'][t]},"
                 f"{d['locked_mean'][t]},{d['capacity_mean'][t]}" for t in range(len(ds))]
    (out / "cashflows.csv").write_text("\n".join(rows) + "\n")


def setup_json(setup: Setup) -> dict:
    controls = {**controls_json(setup), "exposure": exposure_json(setup.exposure)}
    return {k: (controls[k] if k in controls else v.isoformat() if isinstance(v, date) else v)
            for k, v in asdict(setup).items() if (k != "exposure" or setup.exposure != Exposure())
            and QUIET_DEFAULTS.get(k, object()) != v}


def setup_from_json(d: dict) -> Setup:
    return Setup(**{k: (date.fromisoformat(v) if k in ("review", "horizon", "funding", "invoice_due") else
                        tuple(v) if k == "collateral_share" and v is not None else v) for k, v in d.items()
                    if k not in ("need_days", "collection", "financing", "cost_plan", "exposure", *QUIET_DEFAULTS)},
                 **controls_from_json(d),
                 exposure=exposure_from_json(d.get("exposure")))


@lru_cache(maxsize=4)
def _context(path: str):
    data = json.loads(Path(path).read_text())
    return load_feed(data["snapshot_id"]), setup_from_json(data["base_setup"]), model_from_json(data["model"]), data


_ANALYSES: dict = {}


def recompute(path: Path, controls: dict, overrides: dict | None) -> dict:
    """The page's recalculation from a self-contained analysis.json: same judgments, same seeds; no agent or Jev
    call. A probability override only reweights; a financial control re-simulates (cached per setup)."""
    feed, base, model, data = _context(str(path))
    setup = base.with_controls(controls or {})
    clean = {k: {b: min(max(float(p), 0.0), 1.0) for b, p in v.items()} for k, v in (overrides or {}).items()
             if k in model.judgments}
    for k, v in list(clean.items()):  # an override keeps the node's distribution summing to one; all-zero is ignored
        total = sum(v.values())
        if total <= 0:
            del clean[k]
            continue
        clean[k] = {b: v.get(b, 0.0) / total for b in model.judgments[k].distribution}
    key = (str(path), setup)
    if key not in _ANALYSES:
        if len(_ANALYSES) > 8:
            _ANALYSES.clear()
        _ANALYSES[key] = (Analysis(feed, setup, model, dispute_model=load_model(data.get("snapshot_id"))), None)
    a, _ = _ANALYSES[key]
    meta = {k: data[k] for k in ("borrower", "probability_label", "disputes", "not_modelled", "judgments")}
    out = payload(feed, setup, model, meta, clean, analysis=a, stressed=stress(feed, setup, model, clean, dispute_model=a.m))
    out["run_id"] = data.get("run_id")
    return out


def money(cents: float) -> str:
    return usd(int(round(cents)))
