"""The analysis page's payload: one JSON the browser reweights when a probability slider moves.

The browser recomputes every path's probability from the edges (node answers, and composites by the chain rule) and
from them the tiles, the outcome bar and each question's 0% / Jev / 100% bar, exactly, from per-path means. The daily
series (exposure, cash band, the monthly collections table) come from the server's `reweight`, which sums the reduced
per-path records (core.Reduction) under the new probabilities without re-simulating: per-day series for 5,558 paths
would not fit the page.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from app.domain.values import usd

DECIDERS = ("Court", "Qorvo", "Akoustis", "Noteholders and Nasdaq")
ACTOR_GROUP = {"court": "Court", "judgment creditor": "Qorvo", "judgment debtor": "Akoustis", "issuer": "Akoustis",
               "board": "Akoustis", "stockholders": "Akoustis", "borrower": "Akoustis",
               "holders of 25% (or the trustee)": "Noteholders and Nasdaq", "holders": "Noteholders and Nasdaq",
               "noteholders (three or more)": "Noteholders and Nasdaq",
               "Nasdaq hearings panel": "Noteholders and Nasdaq"}
INTERVALS = {"I1": "before the ruling", "I2": "after the ruling, before the appeal deadline",
             "I3": "after the appeal deadline", "I4": "during a stayed appeal", "post": "after the ruling"}
CONTEXT = {"levied": "after a levy", "unlevied": "no levy", "appealed": "on appeal", "final": "no appeal",
           "stay_pending": "stay motion pending", "pay": "", "nopay": "paying in full is out of reach",
           "first": "", "after_seek": "after seeking a sale or financing", "entered": "",
           "delisted_panel": "delisted after the panel", "delisted_suspension": "suspended without a hearing"}
# Outcome by the end of the period (17 Dec for Akoustis), in the order the bar shows them.
CLASSES = (("filed_enforcement", "Filed: Qorvo enforcement"), ("filed_notes", "Filed: notes"),
           ("filed_cash", "Filed: cash floor"), ("settled", "Settled"), ("paid", "Paid"),
           ("stayed", "Stayed on appeal"), ("unresolved", "Unresolved"), ("vacated", "Vacated or new trial"))
OUTCOME_CLASS = {"settled": "settled", "paid": "paid", "stayed": "stayed", "unresolved": "unresolved",
                 "vacated": "vacated", "new_trial": "vacated"}


def money_short(cents: int) -> str:
    v = cents / 100
    return f"${v / 1e6:.1f}M" if abs(v) >= 1e6 else f"${v / 1e3:.0f}k" if abs(v) >= 1e3 else f"${v:.0f}"


def class_text(label: str, ranges: dict[str, tuple[int, int]]) -> str:
    """A ruling amount class (a node context) in words: the amount the ruling leaves on that path."""
    retrial = label.endswith("_retrial")
    base = label.removesuffix("_retrial")
    if base.startswith("amt") and base[3:].isdigit():
        text = f"ruling leaves {money_short(int(base[3:]))}"
    elif base in ranges:
        lo, hi = ranges[base]
        text = f"ruling leaves {money_short(lo)}–{money_short(hi)}"
    else:
        text = ""
    return f"{text}, new trial on damages" if retrial and text else ("new trial on damages" if retrial else text)


def context_text(context: str, ranges: dict[str, tuple[int, int]]) -> str:
    parts = []
    for c in (c for c in context.split("|") if c):
        if c in INTERVALS:
            parts.append(INTERVALS[c])
        elif c in CONTEXT:
            parts.append(CONTEXT[c])
        elif c.startswith(("judgment_", "delisting_", "repurchase_")):
            head, _, rest = c.partition("_")
            parts.append({"judgment": "after acceleration on the judgment default",
                          "delisting": "after acceleration on the delisting",
                          "repurchase": "after an unpaid repurchase"}[head]
                         + (f" ({INTERVALS.get(rest, CONTEXT.get(rest, rest))})" if rest else ""))
        else:
            parts.append(class_text(c, ranges))
    return "; ".join(p for p in parts if p)


def outcome_class(steps: tuple, outcome: str) -> str:
    """Filed (by the first filing step on the path), else the path's outcome."""
    for node, _ctx, branch in steps:
        if node == "debtor_response" and branch == "file":
            return "filed_enforcement"
        if (node == "judgment_default" and branch == "yes") or (node == "delisting_notes" and branch.startswith("petition")):
            return "filed_notes"
        if node == "cash_floor" and branch == "yes":
            return "filed_cash"
    return "filed_enforcement" if outcome == "petition" else OUTCOME_CLASS.get(outcome, "unresolved")


def _when(review: date, day: np.ndarray | None, exact: bool = False) -> str:
    """The median day inside the horizon, as '5 Dec' (exact) or 'Dec'."""
    if day is None:
        return ""
    d = np.asarray(day)
    if not d.size:
        return ""
    t = review + timedelta(days=int(np.median(d)) + 1)
    return f"{t.day} {t:%b}" if exact else f"{t:%b}"


SETTLE = {"I1": "Settles before the ruling", "I2": "Settles after the ruling", "I3": "Settles after the appeal deadline",
          "I4": "Settles during the appeal"}
FILING = {("debtor_response", "file"): "Akoustis files", ("judgment_default", "yes"): "Noteholders accelerate; filing",
          ("delisting_notes", "petition_delist"): "Noteholders accelerate on the delisting; filing",
          ("delisting_notes", "petition_repurchase"): "Repurchase unpaid; filing",
          ("cash_floor", "yes"): "Akoustis files at the cash floor"}


def step_phrase(node: str, ctx: str, branch: str, ranges: dict[str, tuple[int, int]]) -> str | None:
    from app.disputes.forecast import _label

    if (node, branch) in FILING:
        return FILING[(node, branch)]
    if node == "ruling":
        if branch == "none":
            return "Ruling vacates the judgment"
        if branch == "retrial":
            return "Ruling orders a new trial"
        return "Ruling " + class_text(_label(branch), ranges).removeprefix("ruling ")
    return {("settle", "yes"): SETTLE.get(ctx, "Settles"), ("execute_pre_ruling", "yes"): "Qorvo executes before the ruling",
            ("stay", "yes"): "Stay approved", ("registration_early", "yes"): "Early registration allowed",
            ("debtor_response", "pay"): "Akoustis pays",
            ("debtor_response", "seek_sale_or_financing"): "Akoustis seeks a sale or financing",
            ("appeal", "yes"): "Akoustis appeals", ("enforce", "levy"): "Qorvo levies",
            ("listing", "delisted_panel"): "Nasdaq delists", ("listing", "delisted_suspension"): "Nasdaq suspends"
            }.get((node, branch))


def sequence(steps: tuple, day: list, petition: np.ndarray, review: date, days: int,
             ranges: dict[str, tuple[int, int]]) -> str:
    """The path's events in order, each dated by its median day across the draws where it falls inside the period:
    'Ruling leaves $38.6M (Nov) → Qorvo levies (Dec) → Akoustis files (5 Dec)'."""
    out = []
    for i, (node, ctx, branch) in enumerate(steps):
        text = step_phrase(node, ctx, branch, ranges)
        if text is None:
            continue
        if (node, branch) in FILING:
            p = petition[(petition >= 0) & (petition < days)]
            out.append(f"{text} ({_when(review, p, exact=True)})" if p.size else f"{text} after 17 Dec")
            break
        d = np.asarray(day[i]) if i < len(day) else np.zeros(0)
        inside = d[d < days]
        out.append(f"{text} ({_when(review, inside)})" if inside.size else f"{text}, after the period")
    return " → ".join(out) or "Nothing decided in the period"


def encode_paths(combos: list, judgments: dict) -> dict:
    """Nodes by index; composites as lists of conjunctions of (node index, branch index); each path's edges as flat
    [ref, branch index, ...] with ref >= 0 a node and ref < 0 composite -(ref + 1) (branch 0 = yes, 1 = no)."""
    import json as _json

    from app.disputes.forecast import COMPOSITE

    keys = list(judgments)
    node_ix = {k: i for i, k in enumerate(keys)}
    branches = [list(judgments[k].distribution) for k in keys]
    comps: dict[str, int] = {}
    table: list[list[list[list[int]]]] = []
    paths = []
    for combo in combos:
        flat: list[int] = []
        for p in combo:
            for key, branch in p.edges:
                if key.startswith(COMPOSITE):
                    if key not in comps:
                        comps[key] = len(table)
                        table.append([[[node_ix[k], branches[node_ix[k]].index(b)] for k, b in conj]
                                      for conj in _json.loads(key[len(COMPOSITE):])])
                    flat += [-(comps[key] + 1), 0 if branch == "yes" else 1]
                else:
                    flat += [node_ix[key], branches[node_ix[key]].index(branch)]
        paths.append(flat)
    return {"keys": keys, "branches": branches, "composites": table, "paths": paths}


def with_branch(dist: list[float], b: int, x: float) -> list[float]:
    """The slider's rule (page.js withBranch): branch b takes x; the other branches keep their proportions (uniform
    if they were all zero)."""
    rest = sum(v for k, v in enumerate(dist) if k != b)
    n = len(dist)
    return [x if k == b else (1 - x) * v / rest if rest > 0 else (1 - x) / (n - 1) for k, v in enumerate(dist)]


def decode_probs(enc: dict, dist: dict[str, list[float]]) -> np.ndarray:
    """The browser's arithmetic in Python (tests/test_viewer.py): path probabilities from the encoded edges and each
    node's distribution (branch order as encoded)."""
    node = [dist[k] for k in enc["keys"]]
    comp = []
    for conj in enc["composites"]:
        yes = min(max(sum(float(np.prod([node[n][b] for n, b in c])) for c in conj), 0.0), 1.0)
        comp.append((yes, 1.0 - yes))
    out = np.ones(len(enc["paths"]))
    for i, flat in enumerate(enc["paths"]):
        for j in range(0, len(flat), 2):
            ref, b = flat[j], flat[j + 1]
            out[i] *= node[ref][b] if ref >= 0 else comp[-ref - 1][b]
    return out


def _fact_lines(facts: dict) -> list[str]:
    """Path facts code computed for a question, one plain line each."""
    out = []
    for k, v in facts.items():
        name = k.replace("_", " ").capitalize()
        if k == "components":
            if not v:
                continue
            out.append("Components: " + "; ".join(f"{c['component']} {c['amount']} ({c['status']})" for c in v))
        elif isinstance(v, dict):
            out.append(f"{name}: " + ", ".join(f"{kk.replace('_', ' ')} {vv if not isinstance(vv, dict) else vv}"
                                                for kk, vv in v.items()))
        elif k == "share_of_trajectories_where_it_arises":
            out.append(f"Arises on {v:.0%} of trajectories inside the period")
        else:
            out.append(f"{name}: {v}")
    return out


def source_links(snapshot_id: str) -> dict[str, str]:
    """Source title (as the evidence passages carry it) -> URL, from the snapshot's display names and the kit's
    catalog."""
    import json

    from app.config import CASES_DIR, ROOT

    cat = ROOT / "Slope_Credit_Scenario_Research_and_Design_Kit/research/revision_v2/data/sources.json"
    snap = CASES_DIR / snapshot_id / "snapshot.json"
    if not cat.exists() or not snap.exists():
        return {}
    urls = {r["source_id"]: r.get("primary_url") or "" for r in json.loads(cat.read_text())["sources"]}
    shown = json.loads(snap.read_text()).get("source_display", {})
    return {v.get("title", k): urls.get(k, "") for k, v in shown.items()}


def drill_down(spec: dict, model: dict, question: str, facts: dict, judgment, neutral: bool,
               links: dict[str, str]) -> dict:
    """The 'why this probability' chain: each step tagged Law / Record / Data / Calculation / Jev, the facts Jev is
    given, the source quotes and Jev's answer."""
    steps = [{"tag": "Law", "text": model["rules"][r]["citation"] if r in model["rules"] else r} for r in spec["standard"]]
    steps += [{"tag": "Record", "text": x} for x in spec["record_items"]]
    steps += [{"tag": "Data", "text": f"{p['basis']}"} for name, p in model["parameters"].items()
              if name in spec.get("timing", "") and p.get("basis") and p.get("disposition") in ("data", "sourced")]
    steps.append({"tag": "Calculation", "text": f"Date set by code: {spec['timing']}"})
    steps += [{"tag": "Calculation", "text": line} for line in _fact_lines(facts)]
    quotes = []
    for e in (judgment.evidence if judgment is not None else []) or []:
        if isinstance(e, dict):
            for q in e.get("quotes", []):
                quotes.append({"quote": q, "source": e.get("source", ""), "date": e.get("date", ""),
                               "link": links.get(e.get("source", ""), "")})
    answer = None
    if judgment is not None and not neutral:
        answer = {"distribution": judgment.distribution, "confidence": judgment.confidence,
                  "observation_id": judgment.observation_id, "readings": judgment.readings}
    steps.append({"tag": "Jev", "text": question})
    return {"steps": steps, "facts": facts, "assumptions": list(judgment.assumptions) if judgment else [],
            "quotes": quotes, "answer": answer}


def case_terms(d, review: date, horizon: date, model: dict) -> dict:
    """Judgment components with status and source; the notes' default terms; the dated deadlines."""
    comps = [{"label": c.label, "amount": usd(c.amount_cents) if c.amount_cents is not None else "computed by statute",
              "status": c.status, "source": c.motion or d.order_reference,
              **({"remittitur": usd(c.remittitur_cents)} if c.remittitur_cents else {})} for c in d.components]
    notes = []
    for f in (x for x in d.financing if x.status != "superseded"):
        notes.append({"title": f.title, "terms": [
            ("Principal", usd(f.principal_cents)), ("Coupon", f"{usd(f.coupon_cents)} due "
                                                              + ", ".join(x.strftime("%-d %b %Y") for x in f.interest_dates)),
            ("Judgment default", f"final judgments above {usd(f.judgment_default_threshold_cents)} unpaid or unstayed "
                                 f"for {f.judgment_default_days} days, after notice"),
            ("Listing", f"delisting is a fundamental change: repurchase within {f.repurchase_business_days[0]}–"
                        f"{f.repurchase_business_days[1]} business days of notice"),
        ]})
    deadlines = [(m.briefing_close, f"Briefing closes on {m.motion_id}") for m in d.motions if m.briefing_close]
    lags = model["parameters"]["ruling_lag_days"]["sample"]
    if deadlines:
        close = min(x for x, _ in deadlines)
        deadlines.append((close + timedelta(days=min(lags)), f"Earliest ruling (briefing close + {min(lags)} days, "
                                                             f"measured on this docket)"))
    for f in d.financing:
        if f.listing_deadline:
            deadlines.append((f.listing_deadline, "Nasdaq compliance deadline"))
        deadlines += [(x, f"Coupon due ({usd(f.coupon_cents)})") for x in f.interest_dates]
    deadlines.append((horizon, "End of the period"))
    seen, rows = set(), []
    for when, what in sorted(deadlines):
        if (when, what.split(" on ")[0]) in seen:
            continue
        seen.add((when, what.split(" on ")[0]))
        rows.append({"date": when.isoformat(), "what": what.split(" on D.I.")[0] if "Briefing" in what else what})
    return {"components": comps, "notes": notes, "deadlines": rows}


# Per-path means the browser reweights for the tiles (core._scalars names).
TILES = ("collected", "unrecovered", "petition_p", "peak_outstanding", "stayed", "preference")
CHART = ("limit_mean", "outstanding_mean", "petition_cum_p", "frozen_mean", "cash_mean", "cash_p5", "cash_p50",
         "cash_p95", "collected_mean", "contractual")


def monthly_table(r, probs: np.ndarray, d: dict, months: list[tuple[int, int]]) -> list[dict]:
    """Drawn, due, collected, past due at month end, frozen at month end, clawback-exposed collections and the P5 of
    cash above the 30-day need at the month's due dates (after the installment), expected under `probs`."""
    head = r.counts["headroom"].weighted(probs)
    q5 = r.bins["headroom"].quantiles(head, (0.05,))[0]
    due = np.diff(np.concatenate([[0], d["contractual"]]))
    rows = []
    for i, (y, m) in enumerate(months):
        idx = np.flatnonzero(r.month_of_day == i)
        last = int(idx[-1])
        rows.append({"month": f"{y}-{m:02d}", "drawn": int(sum(d["fundings_mean"][t] for t in idx)),
                     "due": int(due[idx].sum()), "collected": int(sum(d["collections_mean"][t] for t in idx)),
                     "past_due": d["past_due_mean"][last], "frozen": d["frozen_mean"][last],
                     "clawback": int(sum(d["clawback_mean"][t] for t in idx)),
                     "above_need_p5": int(q5[i]) if head[i].sum() > 0 else None})
    return rows


def chart_view(r, probs: np.ndarray, months: list[tuple[int, int]]) -> dict | None:
    """The daily series and monthly table the page draws, under `probs` (renormalised; None if they sum to zero)."""
    probs = np.asarray(probs, dtype=np.float64)
    total = probs.sum()
    if total <= 0:
        return None
    probs = probs / total
    d = r.daily(probs)
    return {"daily": {k: d[k] for k in CHART}, "monthly": monthly_table(r, probs, d, months)}


def page_payload(a, model, fc, *, borrower: str, snapshot_id: str, neutral: bool, stress_rows: list | None = None,
                 probs: np.ndarray | None = None) -> dict:
    from app.config import question_registry

    m, setup = a.m, a.setup
    spec = {n: s for t in m["templates"].values() for n, s in t["nodes"].items()}
    questions = {q["id"]: q["question"] for q in question_registry()["questions"]}
    ranges = dict(fc.class_range)
    links = source_links(snapshot_id)
    enc = encode_paths(model.combos, model.judgments)
    nodes = []
    for k, branches in zip(enc["keys"], enc["branches"], strict=True):
        j = model.judgments[k]
        sp, ctx = spec[j.node], (k.split("|", 1)[1] if "|" in k else "")
        facts = j.path_facts or (fc.path_facts(fc.nodes[k], model.disputes[j.instance_id]) if k in fc.nodes else {})
        q = questions.get(j.question_id, j.event)
        nodes.append({"key": k, "node": j.node, "question": q, "context": context_text(ctx, ranges),
                      "actor": sp["actor"], "decider": ACTOR_GROUP.get(sp["actor"], "Akoustis"),
                      "branches": branches, "jev": [j.distribution[b] for b in branches],
                      "detail": drill_down(sp, m, q, facts, j, neutral, links)})
    lead = [c[0] for c in model.combos]
    d0 = model.disputes[lead[0].instance_id] if lead and lead[0].steps else None
    classes, seqs, seq_ix = [], [], {}
    for p in lead:
        classes.append(outcome_class(p.steps, p.outcome))
        tr = fc.trace(d0, p.steps) if d0 is not None and p.steps else None
        text = sequence(p.steps, tr.day, tr.petition, setup.review, a.days, ranges) if tr else "No dispute events"
        seqs.append(seq_ix.setdefault(text, len(seq_ix)))
    cls_ix = {c: i for i, (c, _) in enumerate(CLASSES)}
    probs = model.probs() if probs is None else probs
    months = [f"{y}-{mo:02d}" for y, mo in a.months]
    return {
        "meta": {"borrower": borrower, "review": setup.review.isoformat(), "horizon": setup.horizon.isoformat(),
                 "limit_cents": int(a.line.limit[:, 0].min()), "draws": a.ops.draws, "paths": len(model.combos),
                 "judgments": "neutral" if neutral else "jev",
                 "judgments_note": "No Jev answers yet: every question at 50% (a choice uniform)" if neutral else "",
                 "probability_label": m["probability_label"]},
        "dates": [(setup.review + timedelta(days=t + 1)).isoformat() for t in range(a.days)], "months": months,
        "need_mean": np.rint(a.line.need.mean(axis=0)).astype(np.int64).tolist(),
        "pins": _pins(d0, m) if d0 is not None else {},
        "nodes": nodes, "composites": enc["composites"],
        "paths": {"edges": enc["paths"], "class": [cls_ix[c] for c in classes], "seq": seqs,
                  "scalars": {k: np.round(a.r.means[k], 4 if k == "petition_p" else 0).tolist() for k in TILES}},
        "classes": [label for _, label in CLASSES], "sequences": list(seq_ix),
        "bank": {"scalars": {k: float(a.bank_r.means[k][0]) for k in TILES},
                 **chart_view(a.bank_r, np.array([1.0]), a.months)},
        "event": chart_view(a.r, probs, a.months),
        "worst": _worst(stress_rows, classes, seqs, list(seq_ix)) if stress_rows else [],
        "case_terms": case_terms(d0, setup.review, setup.horizon, m) if d0 is not None else {},
        "settings": SETTINGS,
    }


def _pins(d, m: dict) -> dict:
    close = min((x.briefing_close for x in d.motions if x.briefing_close), default=None)
    lags = m["parameters"]["ruling_lag_days"]["sample"]
    fin = next((f for f in d.financing if f.status != "superseded"), None)
    return {"briefing_close": close.isoformat() if close else None,
            "ruling_window": [(close + timedelta(days=min(lags))).isoformat(),
                              (close + timedelta(days=max(lags))).isoformat()] if close else None,
            "nasdaq": fin.listing_deadline.isoformat() if fin and fin.listing_deadline else None,
            "coupon": fin.interest_dates[0].isoformat() if fin and fin.interest_dates else None}


def _worst(rows: list[dict], classes: list[str], seqs: list[int], texts: list[str], n: int = 25) -> list[dict]:
    """The stress view, unweighted: paths ranked by unrecovered, with the claim frozen if a filing lands on the day
    of peak outstanding."""
    ranked = sorted(rows, key=lambda r: (-r["uncollected_maturity_cents"], -r["petition_at_peak"]["stayed_claim_mean_cents"]))
    return [{"index": r["index"], "class": dict(CLASSES)[classes[r["index"]]], "sequence": texts[seqs[r["index"]]],
             "unrecovered": round(r["uncollected_maturity_cents"]), "frozen": round(r["stayed_claim_cents"]),
             "peak_day": r["petition_at_peak"]["day"],
             "frozen_at_peak": round(r["petition_at_peak"]["stayed_claim_mean_cents"]),
             "clawback_at_peak": round(r["petition_at_peak"]["preference_exposed_mean_cents"]),
             "min_cash_p5": round(r["min_cash_p5_cents"])} for r in ranked[:n]]


# Settings. "line": the line's terms, re-simulated on the same tree; "tree": a sensitivity the chains read, so the
# tree is rebuilt too. Neither kind is precomputed (each full Akoustis run takes minutes and about 1 GB): the page
# runs one on request and shows its progress.
SETTINGS = [
    {"key": "line_usage", "label": "Line usage", "kind": "line", "value": 1.0, "options": [[1.0, "100%"], [0.5, "50%"]]},
    {"key": "limit_multiplier", "label": "Limit", "kind": "line", "value": 1.0,
     "options": [[1.0, "1x"], [1.5, "1.5x"], [2.2, "2.2x"]]},
    {"key": "fee_bps", "label": "Fee", "kind": "line", "value": 370, "options": [[250, "2.5%"], [370, "3.7%"], [500, "5.0%"]]},
    {"key": "remittitur", "label": "Remitted damages", "kind": "tree", "value": "remitted",
     "options": [["remitted", "Remitted amount (D.I. 616-1)"], ["verdict_stands", "Verdict amount"]]},
    {"key": "bond_collateral_share_bps", "label": "Bond collateral", "kind": "tree", "value": False,
     "options": [[False, "100%"], [True, "80%"]]},
    {"key": "coupon_cash_share", "label": "15 Dec coupon", "kind": "tree", "value": "shares",
     "options": [["shares", "Shares"], ["all_cash", "Cash"]]},
    {"key": "chips_credit_cents", "label": "CHIPS credit", "kind": "tree", "value": False,
     "options": [[False, "Off"], [True, "On"]]},
    {"key": "stay_restart_on_increase_days", "label": "Amended judgment: new stay clock on", "kind": "tree",
     "value": False, "options": [[False, "The increase"], [True, "The whole amount"]]},
]
LINE_KEYS = {s["key"] for s in SETTINGS if s["kind"] == "line"}


def build_dev(settings: dict | None = None, progress=None) -> dict:
    """Development data without Jev: the Akoustis pre-D record (app/disputes/akoustis_pre_d.py) on its bank feed and
    run inputs, every question NEUTRAL (a yes/no at 50%, a choice uniform: attribution step 2). Marked in the meta as
    'no Jev answers yet'; never written to runs/recorded/."""
    import copy
    import json

    from app.analysis.build import basis_for
    from app.analysis.core import Analysis, EventModel
    from app.analysis.setup import setup_from_inputs
    from app.config import CASES_DIR
    from app.disputes.akoustis_pre_d import REVIEW, SNAP, judgment
    from app.disputes.forecast import Forecaster, Judgment, neutral_map
    from app.disputes.rules import load_model
    from app.finance.bank import load_feed

    settings = {s["key"]: (settings or {}).get(s["key"], s["value"]) for s in SETTINGS}
    step = progress or (lambda *_: None)
    inputs = json.loads((CASES_DIR / SNAP / "run_inputs.json").read_text())
    setup = setup_from_inputs(inputs, REVIEW).with_controls({k: settings[k] for k in LINE_KEYS})
    feed, m = load_feed(SNAP), load_model()
    if settings["remittitur"] != m["remittitur_scenarios"]["base"]:
        m = copy.deepcopy(m)
        m["remittitur_scenarios"]["base"] = settings["remittitur"]
    sens = {k: settings[k] for k in ("bond_collateral_share_bps", "coupon_cash_share", "chips_credit_cents",
                                     "stay_restart_on_increase_days") if settings[k] not in (False, "shares")}
    borrower = inputs["baseline_profile"]["borrower"]
    step("tree", 0, 1)
    fc = Forecaster([judgment()], {}, borrower=borrower, review=REVIEW, horizon=setup.horizon, hydrate=lambda f: {},
                    model=m, setup=setup, basis=basis_for(feed, setup), sens=sens)
    per = fc.all_paths()
    js = {n.key: Judgment(key=n.key, instance_id=n.instance_id, node=n.node, question_id=n.question_id, event=n.event,
                          assumptions=n.assumptions, window=n.window,
                          distribution={b: 1 / len(n.branches) for b in n.branches}) for n in fc.nodes.values()}
    model = EventModel({d.instance_id: d for d in fc.disputes}, js, per, fc.ordered(), neutral=neutral_map(js))
    a = Analysis(feed, setup, model, sens=sens, dispute_model=m, progress=step)
    rows = Analysis(feed, setup, model, stress=True, sens=sens, dispute_model=m, progress=step).stress_rows
    rows = [{"index": i, **r} for i, r in enumerate(rows)]
    payload = page_payload(a, model, fc, borrower=borrower, snapshot_id=SNAP, neutral=True, stress_rows=rows)
    payload["meta"]["dev"] = True
    payload["settings_value"] = settings
    return {"payload": payload, "r": a.r, "bank_r": a.bank_r, "model": model, "months": a.months,
            "class_of_path": np.array(payload["paths"]["class"])}


def reweight(state: dict, overrides: dict[str, list[float]] | None, classes: list[int] | None = None) -> dict | None:
    """The daily series and monthly table under the browser's node distributions (branch order as encoded),
    optionally only over the paths of some outcome classes. No re-simulation."""
    model = state["model"]
    ov = {k: {b: float(p) for b, p in zip(model.judgments[k].distribution, v, strict=True)}
          for k, v in (overrides or {}).items() if k in model.judgments}
    probs = model.probs(ov)
    if classes is not None:
        probs = probs * np.isin(state["class_of_path"], classes)
    return chart_view(state["r"], probs, state["months"])


class DevPage:
    """The dev page's state (the reduced analysis for the current settings, cached in var/dev/) and at most one
    settings run at a time, with its progress."""

    def __init__(self) -> None:
        import threading

        self.state: dict | None = None
        self.progress = {"running": False, "phase": "", "done": 0, "total": 0, "error": ""}
        self._lock = threading.Lock()

    @staticmethod
    def path(settings: dict | None = None):
        import hashlib
        import json

        from app.config import VAR

        full = {s["key"]: (settings or {}).get(s["key"], s["value"]) for s in SETTINGS}
        tag = hashlib.sha1(json.dumps(full, sort_keys=True).encode()).hexdigest()[:10]
        return VAR / "dev" / f"akoustis_page_{tag}.pkl"

    def load(self, settings: dict | None = None) -> dict | None:
        import pickle

        p = self.path(settings)
        if p.exists():
            self.state = pickle.loads(p.read_bytes())
        return self.state

    def build(self, settings: dict | None = None) -> dict:
        import pickle

        def tick(phase: str, done: int, total: int) -> None:
            self.progress.update(phase=phase, done=done, total=total)

        state = build_dev(settings, progress=tick)
        p = self.path(settings)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(pickle.dumps(state, protocol=pickle.HIGHEST_PROTOCOL))
        self.state = state
        return state

    def start(self, settings: dict) -> bool:
        """Switch to `settings`: at once if cached, else in a background thread (False if one is running)."""
        import threading

        if self.path(settings).exists():
            self.load(settings)
            return True
        with self._lock:
            if self.progress["running"]:
                return False
            self.progress.update(running=True, phase="tree", done=0, total=1, error="")

        def work() -> None:
            try:
                self.build(settings)
            except Exception as e:  # reported to the page, which keeps the previous settings
                self.progress["error"] = str(e)
            finally:
                self.progress["running"] = False

        threading.Thread(target=work, daemon=True).start()
        return True
