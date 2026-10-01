"""The analysis page from the runner pipeline's tables (app/analysis/reduce.py `Tables`): the 14 May tree at its size,
reduced on runners, with Jev's answers (runs/recorded/<run>/tree_answers.json) and the bank view simulated here.

The page's figures are the tables' sums under the settings they carry: Jev's answers and every residual neutral (the
full settings), and each question type held to each of its answers (the scalar settings: exact 0% / 100% bars). One
judgment at a time is live: its derivative atoms (`Tables.dscal`, `dser`) make every figure and the collected, due,
past-due and outstanding series exact under a changed answer (the path probabilities are linear in it). Per-path
structures the in-process page carries (every path's edges and scalars) are not on this page: the tree is too large.

`python -m app.analysis.tables_page <run> <tables.pkl> <control.pkl> [stress.pkl] [-<variant>]` writes
runs/recorded/<run>/page.json.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import pickle
import sys
import time
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import numpy as np

from app.analysis.reduce import Tables

FULL_JEV, FULL_NEUTRAL = 0, 1  # the full settings' order (reduce.settings_for)
TILE_KEYS = ("drawn", "collected", "not_yet_due", "petition_p", "preference", "stayed", "peak_outstanding",
             "avg_outstanding", "atm_proceeds", "offering_proceeds")


def load_judgments(run_dir: Path, variant: str = "") -> tuple[dict, dict]:
    """Jev's answer to every question of the tree (pool.judge): each distribution, and each Judgment (its facts and
    evidence, for the drill-down)."""
    from app.disputes.forecast import Judgment

    suffix = f"-{variant}" if variant else ""
    answers = json.loads((run_dir / f"tree_answers{suffix}.json").read_text())["answers"]
    raw = json.loads(gzip.decompress((run_dir / f"tree_judgments{suffix}.json.gz").read_bytes()))
    js = {}
    for j in raw:
        j = {**j, "assumptions": tuple(j.get("assumptions", ())), "finding_ids": tuple(j.get("finding_ids", ()))}
        js[j["key"]] = Judgment(**{k: v for k, v in j.items() if k in Judgment.__dataclass_fields__})
    return answers, js


def bank_view(ctx: dict, setup, fc, run_id: str) -> tuple:
    """The bank view (the company's distress decisions on the bank data alone), simulated here: its paths are a
    handful. Returns the analysis (its bank reduction, months and line) and the event model carrying the view."""
    from app.agent.jev import JevAdapter
    from app.agent.jev_profiles import DisputeProfile
    from app.analysis.core import Analysis, EventModel

    bank_paths = fc.bank_paths()
    jev = JevAdapter(run_id=f"{run_id}-analysis", use_cache=True)
    prof = DisputeProfile(jev, lambda kind, obj: None)
    bank_judgments = asyncio.run(fc.judge_bank(prof)) if fc.bank_nodes else {}
    model = EventModel({d.instance_id: d for d in fc.disputes}, {}, {}, [], bank_paths=bank_paths,
                       bank_judgments=bank_judgments)
    a = Analysis(ctx["feed"], setup, model, dispute_model=ctx["m"])
    return a, model


def tile_figures(tab: Tables, i: int) -> dict[str, float]:
    """The forecast tiles under full setting i, as page.js `figures` names them (page.path_scalars' keys)."""
    e = tab.expected(i)
    due = float(tab.per_day["due_cum"][i, -1] / tab.draws)
    past = float(tab.per_day["past_due"][i, -1] / tab.draws)
    out = {"funded": e["drawn"], "due": due, "collected": e["collected"], "unpaid": due - e["collected"],
           "past_due": past, "frozen_due": due - e["collected"] - past, "not_yet_due": e["not_yet_due"],
           "petition_p": e["petition_p"], "clawback": e["preference"], "stayed": e["stayed"],
           "peak_outstanding": e["peak_outstanding"], "avg_outstanding": e["avg_outstanding"]}
    for k in ("atm_proceeds", "offering_proceeds"):
        if k in e:
            out[k] = e[k]
    return out


def event_chart(tab: Tables, i: int, a, months: list) -> dict:
    """The daily series and monthly table under full setting i (page.chart_view's shape, from the tables)."""
    from app.analysis.page import ARREARS_PREFIX, CHART
    from app.analysis.reduce import ARREARS_KEYS

    d = tab.daily(i, a.line.limit[:, :tab.days])
    arrears = {k.removeprefix(ARREARS_PREFIX): np.rint(tab.per_day[k][i] / tab.draws).astype(np.int64).tolist()
               for k in ARREARS_KEYS if k in tab.per_day and tab.per_day[k][i].any()}
    return {"daily": {k: d[k] for k in CHART}, "monthly": monthly_rows(tab, i, d, months),
            **({"arrears": arrears} if arrears else {})}


def monthly_rows(tab: Tables, i: int, d: dict, months: list) -> list[dict]:
    """page.monthly_table from the tables: the headroom P5 per month from the headroom counts under setting i."""
    b = tab.bins["headroom"]
    head = tab.counts["headroom"][i].reshape(len(b.lo), b.n)
    hn = head / np.maximum(head.sum(axis=1, keepdims=True), 1e-300)
    q5 = b.quantiles(hn, (0.05,))[0]
    due = np.diff(np.concatenate([[0], d["contractual"]]))
    coll = np.diff(np.concatenate([[0], d["collected_mean"]]))
    rows = []
    for k, (y, m) in enumerate(months):
        idx = np.flatnonzero(tab.month_of_day == k)
        last = int(idx[-1])
        rows.append({"month": f"{y}-{m:02d}", "drawn": int(sum(d["fundings_mean"][t] for t in idx)),
                     "due": int(due[idx].sum()), "collected": int(coll[idx].sum()),
                     "past_due": d["past_due_mean"][last], "frozen_due": d["frozen_due_mean"][last],
                     "frozen_not_due": d["frozen_mean"][last] - d["frozen_due_mean"][last],
                     "clawback": int(sum(d["clawback_mean"][t] for t in idx)),
                     "above_need_p5": int(q5[k]) if head[k].sum() > 0 else None})
    return rows


def collected_range(tab: Tables, i: int, months: list) -> list[list[int]]:
    """Cumulative collected at each month end, P5 and P95 over the draws under setting i; the horizon's from the
    fine histogram."""
    from app.analysis.core import QS

    b = tab.bins["collected"]
    h = tab.counts["collected"][i].reshape(len(b.lo), b.n)
    kq = b.quantiles(h / np.maximum(h.sum(axis=1, keepdims=True), 1e-300), (0.05, 0.95))
    ends = [int(np.flatnonzero(tab.month_of_day == k)[-1]) for k in range(len(months))]
    out = [[int(kq[0][t]), int(kq[1][t])] for t in ends]
    fine = tab._fine_q("collected", i, QS)
    if fine is not None:
        out[-1] = [int(fine[0]), int(fine[-1])]
    return out


# --- section 3: how the lawsuit can resolve, from the outcome sums and the prefix trie ----------------------------

def phrase(step: tuple, ranges: dict) -> str:
    """A path step in the page's words (page.step_phrase); a grouped branch by its answer."""
    from app.analysis.page import step_phrase, step_phrase_14

    n, c, b = step
    if b.startswith("@"):
        b = b.split("=", 1)[1]
    return step_phrase_14(n, b) or step_phrase(n, c, b, ranges) or f"{n}: {b}".replace("_", " ")


def is_filing(step: tuple) -> bool:
    from app.analysis.page import FILING

    n, _c, b = step
    if b.startswith("@"):
        b = b.split("=", 1)[1]
    return (n, b) in FILING or (n in ("cash_floor", "cash_out") and b == "yes")


def verdict_rows(tab: Tables, fc, d, ranges: dict) -> dict:
    """The verdict table: per verdict class the probability, the collected and filing figures given it, the
    post-trial ruling's reduced and set-aside shares, and what most often follows (the prefix trie's next step
    after the verdict), from `Tables.outcomes` and `Tables.trie` under Jev's answers."""
    from app.analysis.page import SETTLE, banded_rows

    by_verdict: dict = defaultdict(lambda: np.zeros(5))  # mass, petition, collected, reduced, set aside
    for (v, r, _o), x in tab.outcomes.items():
        acc = by_verdict[v]
        acc[:3] += x
        if r.startswith("reduced"):
            acc[3] += x[0]
        elif r.startswith("set_aside"):
            acc[4] += x[0]
    seen = [v for v in by_verdict if v]
    vb = banded_rows(fc, d, [], seen) if seen else {}
    rows = vb.get("branches", [])
    order = {r["key"]: i for i, r in enumerate(rows)}
    # what follows each verdict: the trie's prefixes one step past the verdict step, by mass
    after: dict = defaultdict(lambda: defaultdict(float))
    for prefix, x in tab.trie.items():
        j = next((i for i, s in enumerate(prefix) if s[0] == "verdict"), None)
        if j is not None and len(prefix) == j + 2:
            after[prefix[j][2]][phrase(prefix[j + 1], ranges)] += x[0]
    groups = []
    before = by_verdict.get("", np.zeros(5))
    if before[0] > 0:
        groups.append({"k": -1, "label": SETTLE.get("I0", "Settles"), "lo": None, "hi": None, "p": float(before[0]),
                       "c": float(before[2]), "f": float(before[1]), "r": 0.0, "s": 0.0, "follows": []})
    for v in sorted(seen, key=lambda x: order.get(x, 10**6)):
        acc, row = by_verdict[v], rows[order[v]] if v in order else {"kind": "other", "label": v, "lo": None,
                                                                    "hi": None, "booked": None}
        fol = sorted(after.get(v, {}).items(), key=lambda kv: -kv[1])[:3]
        groups.append({"k": order.get(v, -2), "row": row, "p": float(acc[0]), "c": float(acc[2]), "f": float(acc[1]),
                       "r": float(acc[3]), "s": float(acc[4]),
                       "follows": [[float(mass / acc[0]), text] for text, mass in fol if acc[0] > 0]})
    return {**vb, "groups": groups}


def grid_classes(tab: Tables) -> tuple[list[str], list[float]]:
    """The outcome grid: the filing mass, and the rest by outcome class (page.CLASSES' labels)."""
    from app.analysis.page import CLASSES, OUTCOME_CLASS

    labels = dict(CLASSES)
    filed, rest = 0.0, defaultdict(float)
    for (_v, _r, o), x in tab.outcomes.items():
        filed += float(x[1])
        rest[OUTCOME_CLASS.get(o, "unresolved")] += float(x[0] - x[1])
    names = ["Filed by {horizon}"] + [labels[c] for c, _ in CLASSES if c in rest and rest[c] > 1e-12]
    shares = [filed] + [rest[c] for c, _ in CLASSES if c in rest and rest[c] > 1e-12]
    return names, shares


def prefix_tree(tab: Tables, ranges: dict, depth: int = 3) -> dict:
    """The paths' first steps as a tree (page.js `tree`): each node's probability mass and filing mass under Jev's
    answers, from `Tables.trie`; a branch ends at a filing step."""
    root: dict = {"base": "", "when": "", "mass": 0.0, "filed": 0.0, "kids": {}}
    for prefix, x in sorted(tab.trie.items(), key=lambda kv: len(kv[0])):
        if len(prefix) > depth:
            continue
        node = root
        cut = False
        for s in prefix[:-1]:
            if is_filing(s):
                cut = True
                break
            node = node["kids"].get(phrase(s, ranges))
            if node is None:
                cut = True
                break
        if cut:
            continue
        key = phrase(prefix[-1], ranges)
        kid = node["kids"].setdefault(key, {"base": key, "when": "", "mass": 0.0, "filed": 0.0, "kids": {}})
        kid["mass"], kid["filed"] = float(x[0]), float(x[1])
    root["mass"] = sum(k["mass"] for k in root["kids"].values())
    root["filed"] = sum(k["filed"] for k in root["kids"].values())

    def out(n: dict) -> dict:
        return {**{k: v for k, v in n.items() if k != "kids"}, "kids": [out(k) for k in n["kids"].values()]}
    return out(root)


def worst_rows(rows: list, ranges: dict, n: int = 25) -> list[dict]:
    """The stress view (page._worst): the paths ranked by unrecovered, from the reduction's stress rows
    (steps, outcome, row)."""
    from app.analysis.page import CLASSES, OUTCOME_CLASS

    labels = dict(CLASSES)
    ranked = sorted(rows, key=lambda r: (-r[2]["uncollected_maturity_cents"],
                                         -r[2]["petition_at_peak"]["stayed_claim_mean_cents"]))
    out = []
    for i, (steps, outcome, r) in enumerate(ranked[:n]):
        text = " → ".join(phrase(s, ranges) for s in steps if s[0] not in ("settle",) or s[2] == "yes")
        cls = "Filed by {horizon}" if outcome == "petition" else labels[OUTCOME_CLASS.get(outcome, "unresolved")]
        out.append({"index": i, "class": cls, "sequence": text, "unrecovered": round(r["uncollected_maturity_cents"]),
                    "frozen": round(r["stayed_claim_cents"]), "peak_day": r["petition_at_peak"]["day"],
                    "frozen_at_peak": round(r["petition_at_peak"]["stayed_claim_mean_cents"]),
                    "clawback_at_peak": round(r["petition_at_peak"]["preference_exposed_mean_cents"]),
                    "min_cash_p5": round(r["min_cash_p5_cents"])})
    return out


# --- section 4: the judgment that matters most, exact under the tables' derivative atoms ----------------------------

def judgment_section(tab: Tables, answers: dict, judgments: dict, nodes: dict, fc, m: dict, links: dict, setup,
                     d, ranges: dict, top: int = 8) -> dict:
    """Per question type, the figures with every question of the type held to each answer (the scalar settings:
    exact), and Jev's; and the questions whose answer moves expected collections most (the swing between each
    one-hot answer, exact from `Tables.dscal`), each with its derivative atoms so the page recomputes every figure
    and the collected, due, past-due and outstanding series under a changed answer."""
    from app.analysis.page import SHORT_LABELS, context_text, decider, drill_down, short_context
    from app.analysis.reduce import SERIES
    from app.config import question_registry

    spec = {n: s for t in m["templates"].values() for n, s in t["nodes"].items()}
    questions = {q["id"]: q["question"] for q in question_registry()["questions"]}
    skeys = list(tab.skeys or [])
    e0 = np.array([tab.means[k][FULL_JEV] for k in skeys])
    ci = skeys.index("collected")
    # each question type held to each answer: the scalar settings, in reduce.settings_for's order
    types: dict = {}
    for j, name in enumerate(tab.scalar):
        t, a = name.split("=", 1)
        types.setdefault(t, {"node": t, "count": 0, "answers": []})
        types[t]["answers"].append({"answer": a, "collected": float(tab.lo_means["collected"][j]),
                                    "petition_p": float(tab.lo_means["petition_p"][j])})
    for k in answers:
        n = nodes.get(k)
        if n is not None and n.node in types:
            types[n.node]["count"] += 1
    ranked = []
    for k, dist in answers.items():
        n = nodes.get(k)
        if n is None or not any((k, a) in tab.dscal for a in dist):
            continue
        br = list(n.branches)
        p = np.array([dist.get(a, 0.0) for a in br])
        D = np.stack([tab.dscal.get((k, a), np.zeros(len(skeys))) for a in br])  # [answers, skeys]
        base = e0 - p @ D  # the expectation with every answer's atom removed: e(q) = base + q @ D
        hot = base[None, :] + D  # each one-hot answer
        swing = float(hot[:, ci].max() - hot[:, ci].min())
        p0 = float(p[0])
        ranked.append((swing * 2 * p0 * (1 - p0), swing, k, br, p, D, base))
    ranked.sort(key=lambda x: -x[0])
    out_nodes = []
    for _score, _swing, k, br, p, D, base in ranked[:top]:
        n, j = nodes[k], judgments.get(k)
        sp, ctx = spec[n.node], (k.split("|", 1)[1] if "|" in k else "")
        q = questions.get(n.question_id, n.event)
        dser = {a: {s: tab.dser[(k, a)][i].tolist() for i, s in enumerate(SERIES)} for a in br if (k, a) in tab.dser}
        out_nodes.append({
            "key": k, "node": n.node, "question": q, "context": context_text(ctx, ranges), "form": None,
            "label": SHORT_LABELS.get(n.question_id, q), "sub": short_context(ctx, ranges), "actor": sp["actor"],
            "decider": decider(sp["actor"]), "branches": br, "jev": p.tolist(),
            "detail": drill_down(sp, m, q, j.path_facts if j else {}, j, False, links, n.node, d, setup=setup)
            if j else {"facts": {}, "quotes": [], "steps": []},
            "atoms": {"base": dict(zip(skeys, base.tolist(), strict=True)),
                      "d": {a: dict(zip(skeys, D[i].tolist(), strict=True)) for i, a in enumerate(br)}},
            "series": dser})
    return {"types": sorted(types.values(), key=lambda t: t["node"]), "nodes": out_nodes,
            "skeys": skeys, "series_keys": list(SERIES)}


# --- the page -------------------------------------------------------------------------------------------------------

def build(run_id: str, tables: str, control: str, stress: str | None = None, variant: str = "",
          answers_file: str | None = None, judgments_file: str | None = None, *, root: Path | None = None) -> dict:
    """page.json for a run from the runner pipeline's outputs (the module docstring)."""
    from app.analysis.build import run_context
    from app.analysis.page import (
        _pins,
        assumption_text,
        case_inputs,
        case_terms,
        chart_view,
        common_block,
        line_block,
        named,
        offering_terms,
        parties,
        path_scalars,
        source_links,
    )
    from app.analysis.reduce import Tables  # noqa: F401 (unpickled below)
    from app.config import RECORDED
    from app.disputes import pool
    from app.disputes.forecast import PENDING
    from app.disputes.parallel import _variant

    t0 = time.time()
    root = root if root is not None else RECORDED
    run_dir = root / run_id
    ctx = run_context(run_id, root)
    setup, _sens = _variant(ctx)
    m, borrower, snapshot_id = ctx["m"], ctx["borrower"], ctx["meta"]["snapshot_id"]
    with open(control, "rb") as fh:
        ctl = pickle.load(fh)
    with open(tables, "rb") as fh:
        tab = pickle.load(fh)
    rows = []
    if stress:
        with open(stress, "rb") as fh:
            rows = pickle.load(fh)
    if answers_file:  # answers from elsewhere (a dry run on another tree): no drill-down without judgments
        answers = json.loads(Path(answers_file).read_text())["answers"]
        judgments = load_judgments(Path(judgments_file).parent, variant)[1] if judgments_file else {}
    else:
        answers, judgments = load_judgments(run_dir, variant)
    fc = pool.forecaster(run_id, ctl, root)
    d = next(x for x, _ in fc.ordered() if x.stage == PENDING and x.borrower_role == "debtor")
    a, model = bank_view(ctx, setup, fc, run_id)
    ranges, links, names = dict(fc.class_range), source_links(snapshot_id), parties(borrower, d)
    days = tab.days
    print(f"{time.time() - t0:7.0f}s tables page: {tab.paths} paths, {len(ctl['nodes'])} questions, bank view "
          f"{len(model.bank_paths)} paths", file=sys.stderr, flush=True)
    full, neutral = tile_figures(tab, FULL_JEV), tile_figures(tab, FULL_NEUTRAL)
    bank_probs = model.bank_probs()
    ordinary = {k: float(bank_probs @ v) for k, v in path_scalars(a.bank_r).items()}
    tmonths = a.months[:int(tab.month_of_day.max()) + 1]  # the tables' calendar (a shorter horizon: a dry run's)
    event = event_chart(tab, FULL_JEV, a, tmonths)
    verdict = verdict_rows(tab, fc, d, ranges)
    classes, shares = grid_classes(tab)
    judgment = judgment_section(tab, answers, judgments, ctl["nodes"], fc, m, links, setup, d, ranges)
    inputs, scenario = case_inputs(snapshot_id)
    months = [f"{y}-{mo:02d}" for y, mo in a.months]
    payload = {
        "meta": {"borrower": borrower, "review": setup.review.isoformat(), "horizon": setup.horizon.isoformat(),
                 "limit_cents": int(a.line.limit[:, 0].min()), "fee_bps": setup.fee_bps,
                 "installments": setup.installments, "draws": tab.draws, "paths": tab.paths, "judgments": "jev",
                 "judgments_note": "", "probability_label": m["probability_label"], "snapshot_id": snapshot_id,
                 "tables": True},
        "dates": [(setup.review + timedelta(days=t + 1)).isoformat() for t in range(days)],
        "months": months, "need_mean": np.rint(a.line.need[:, :days].mean(axis=0)).astype(np.int64).tolist(),
        "pins": _pins(d, m), "composites": [], "paths": {"edges": [], "class": [], "seq": [], "scalars": {}},
        "sequences": [], "parties": names, "filing_steps": [],
        "nodes": [{**n, **{k: named(n[k], names) for k in ("label", "sub", "context", "decider")}}
                  for n in judgment["nodes"]],
        "classes": [named(c, names) for c in classes],
        "bank": {"scalars": ordinary, "edges": [], "path_scalars": {},
                 **chart_view(a.bank_r, bank_probs, a.months)},
        "event": event, "collected_range": collected_range(tab, FULL_JEV, tmonths),
        "worst": [{**w, "class": named(w["class"], names)} for w in worst_rows(rows, ranges)] if rows else [],
        "case_terms": case_terms(d, setup.review, setup.horizon, m, links, {
            c.component_id: "; ".join(dict.fromkeys(fc.hydrate(fc.findings[f])["source"] for f in c.finding_ids
                                                    if f in fc.findings)) for c in d.components}),
        "settings": [], "line": line_block(setup, inputs), "common": common_block(setup),
        "opening_cash_cents": int(getattr(getattr(getattr(fc, "draws", None), "basis", None), "opening", 0) or 0),
        "verdict": {**{k: v for k, v in verdict.items() if k != "groups"},
                    "branches": [{**r, **({"label": named(r["label"], names)} if "label" in r else {})}
                                 for r in verdict.get("branches", [])]},
        "financing": {"raised": [], "terms": offering_terms(m)},
        "dispute": {"trial_started": d.trial_started.isoformat() if d.trial_started else None,
                    "commenced": d.commenced.isoformat() if d.commenced else None,
                    "notes": [{"principal_cents": f.principal_cents,
                               "default_threshold_cents": f.judgment_default_threshold_cents,
                               "default_days": f.judgment_default_days,
                               "listing_deadline": f.listing_deadline.isoformat() if f.listing_deadline else None}
                              for f in d.financing if f.status != "superseded"]},
        "narrative": [named(x, names) for x in scenario.get("narrative", [])],
        "assumption_text": assumption_text(scenario, names),
        "tables": {"figures": {"full": full, "neutral": neutral, "ordinary": ordinary},
                   "verdict_groups": [{**g, "follows": [[s, named(t, names)] for s, t in g["follows"]]}
                                      for g in verdict["groups"]],
                   "grid": shares, "tree": prefix_tree(tab, ranges), "types": judgment["types"],
                   "skeys": judgment["skeys"], "series_keys": judgment["series_keys"],
                   "settings": {"full": list(tab.full), "scalar": list(tab.scalar)}},
    }
    print(f"{time.time() - t0:7.0f}s tables page: {len(payload['nodes'])} judgments shown, "
          f"{len(judgment['types'])} question types", file=sys.stderr, flush=True)
    return payload


def write(run_id: str, payload: dict, variant: str = "") -> Path:
    from app.config import RECORDED

    out = RECORDED / run_id / (f"page-{variant}.json" if variant else "page.json")
    out.write_text(json.dumps(payload, default=str) + "\n")
    return out


if __name__ == "__main__":
    args = [x for x in sys.argv[1:] if not x.startswith("-")]
    var = next((x[1:] for x in sys.argv[1:] if x.startswith("-")), "")
    p = build(args[0], args[1], args[2], args[3] if len(args) > 3 else None, var)
    print(write(args[0], p, var), file=sys.stderr)
