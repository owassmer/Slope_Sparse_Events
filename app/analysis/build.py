"""Build the analysis of a recorded investigation, and recompute it for the page's controls.

`build` asks Jev for the conditional forecasts (cached; `refresh` re-asks), simulates Slope's reusable line, and
writes the sidecar artifacts beside the recorded run: analysis.json (views, lead-time series, the three-step
attribution, judgment sensitivity, stress), collections.csv and cashflows.csv. `recompute` reuses the stored
judgments: a probability override reweights the existing trajectories, a financial control (fee, line usage, limit
multiplier, variability, ...) re-simulates with the same seeds; neither calls the agent or Jev.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
import time
from dataclasses import asdict
from datetime import date
from functools import lru_cache
from pathlib import Path

from app.analysis.core import Analysis, EventModel, dates, stress
from app.analysis.events import BANK
from app.analysis.setup import (
    QUIET_DEFAULTS,
    Exposure,
    Setup,
    controls_from_json,
    controls_json,
    exposure_from_json,
    exposure_json,
)
from app.config import VAR, question_registry
from app.disputes.forecast import DisputePath, Forecaster, Judgment
from app.disputes.rules import load_model
from app.domain.investigation import DisputeInstance
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
    from app.disputes.forecast import pack_mask

    return {"disputes": {k: d.model_dump(mode="json") for k, d in m.disputes.items()},
            "judgments": {k: asdict(j) for k, j in m.judgments.items()},
            "paths": {i: {c: [{"steps": [list(s) for s in p.steps], "outcome": p.outcome,
                              "edges": [list(e) for e in p.edges], "cls": p.cls, **_mask_json(p)} for p in ps]
                          for c, ps in cl.items()} for i, cl in m.per.items()},
            "order": [[d.instance_id, parent.instance_id if parent else None] for d, parent in m.order],
            "neutral": m.neutral,
            "stubs": {i: [{"mask": pack_mask(t["mask"]).hex(), "day": t["day"].tolist(), "edges": t["edges"],
                           "groups": t["groups"], "group_size": t["group_size"]} for t in ts]
                      for i, ts in m.stubs.items()},
            "bank": {"judgments": {k: asdict(j) for k, j in m.bank_judgments.items()},
                     "paths": [{"steps": [list(s) for s in p.steps], "outcome": p.outcome,
                                "edges": [list(e) for e in p.edges], **_mask_json(p)} for p in m.bank_paths]}}


def _mask_json(p: DisputePath) -> dict:
    """A path's draws (DisputePath.mask, hex), only where it follows some of them (a grouped question's fork), and
    its sampling (divisor, stub, group) where the answer-first walk sampled it: without them a restored run would weigh
    a sampled path as walked exactly."""
    out = {} if p.mask is None else {"mask": p.mask.hex()}
    if p.stub >= 0 or p.divisor != 1.0:
        out.update(divisor=p.divisor, stub=p.stub, group=p.group)
    return out


def _judgments(data: dict) -> dict[str, Judgment]:
    return {k: Judgment(**{**v, "assumptions": tuple(v["assumptions"]), "finding_ids": tuple(v["finding_ids"])})
            for k, v in data.items()}


def model_from_json(data: dict) -> EventModel:
    disputes = {k: DisputeInstance.model_validate(v) for k, v in data["disputes"].items()}
    judgments = _judgments(data["judgments"])
    bank = data.get("bank") or {"judgments": {}, "paths": []}
    mask = lambda p: bytes.fromhex(p["mask"]) if p.get("mask") else None  # noqa: E731
    sampled = lambda p: {"divisor": p.get("divisor", 1.0), "stub": p.get("stub", -1),  # noqa: E731
                         "group": p.get("group", -1)}
    bank_paths = [DisputePath(instance_id=BANK, steps=tuple(tuple(s) for s in p["steps"]), outcome=p["outcome"],
                              edges=tuple(tuple(e) for e in p["edges"]), mask=mask(p)) for p in bank["paths"]]
    per = {i: {c: [DisputePath(instance_id=i, steps=tuple(tuple(s) for s in p["steps"]), outcome=p["outcome"],
                               edges=tuple(tuple(e) for e in p["edges"]), cls=p["cls"], mask=mask(p), **sampled(p))
                    for p in ps]
               for c, ps in cl.items()} for i, cl in data["paths"].items()}
    order = [(disputes[i], disputes[p] if p else None) for i, p in data["order"]]
    import numpy as np

    from app.analysis.setup import DRAWS
    from app.disputes.forecast import path_mask

    stubs = {i: [{"mask": path_mask(DisputePath(instance_id=i, steps=(), outcome="", edges=(),
                                                mask=bytes.fromhex(t["mask"])), DRAWS),
                  "day": np.asarray(t["day"], dtype=np.int64), "edges": t["edges"], "groups": t["groups"],
                  "group_size": t["group_size"]} for t in ts]
             for i, ts in (data.get("stubs") or {}).items()}
    return EventModel(disputes, judgments, per, order, neutral=data.get("neutral"), bank_paths=bank_paths,
                      bank_judgments=_judgments(bank["judgments"]), stubs=stubs)


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


def run_page(a: Analysis, model: EventModel, fc: Forecaster, *, borrower: str, snapshot_id: str,
             stress_rows: list, stress_search: dict | None = None) -> dict:
    """The one-screen page for a recorded run (app/analysis/page.py), and the reduced state its reweight reads. The
    settings re-simulate the dev page only, so a run's page has none."""
    from app.analysis.page import page_payload

    neutral = not any(j.observation_id for j in model.judgments.values())  # no Jev answer at all: even odds
    p = page_payload(a, model, fc, borrower=borrower, snapshot_id=snapshot_id, neutral=neutral,
                     stress_rows=stress_rows, stress_search=stress_search)
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
