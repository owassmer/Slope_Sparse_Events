"""Unbilled: explain the histories one walker lists and the other does not, from the saved comparison artifacts.

uv run python tests/explain_chronological.py ROOT  (reads var/diag/001-1f/ROOT-{current,chronological}.pkl)

Each history is its in-horizon decisions with their dates on the root's draw (the benchmark's signature, plus
dates). Each unshared history is assigned the rule (RULES) that explains it, or "unexplained".
"""
import json
import pickle
import sys
from collections import Counter, defaultdict
from pathlib import Path

import akoustis_20240514_fixture as fx
import numpy as np
from benchmark_chronological import distribution, root
from test_dated_answer_domains import SAVED  # noqa: F401  (the benchmark's roots)

from app.analysis.events import Draws, event_trace, plain
from app.disputes.forecast import Dist, Forecaster, path_mask, path_probability


def feasible(edges, dist):
    """The path's keyed probability; None where an answer is not one its question offers (no mass, not zero)."""
    try:
        return path_probability(edges, dist)
    except KeyError:
        return None


def histories(name, label):
    steps, row, cls = root(name)
    d = fx.pending(instance_id="dispute_002")
    fc = Forecaster([d], {}, borrower="B", review=fx.REVIEW, horizon=fx.setup().horizon,
                    hydrate=lambda f: {}, model=fx.model(), setup=fx.setup(), basis=fx.basis())
    with Path(f"var/diag/001-1f/{name}-{label}.pkl").open("rb") as source:
        fc.nodes, out, _, _ = pickle.load(source)
    draws = Draws(fc.draws.n, basis=fc.draws.basis)
    draws.prefixes = {}
    base = {k: distribution(n.branches, "keyed", k) for k, n in fc.nodes.items()}
    rows = []
    for p in out:
        mask = path_mask(p, fc.draws.n)
        tr = event_trace(d, p, fc.setup, fc.m, draws, fc.sens, rows=tuple(mask for _ in p.steps))
        dated = []
        for i, (n, c, b) in enumerate(p.steps):
            day = int(tr.day[i][row])
            if 0 <= day < fc.days and not b.startswith("@-1=") and (n, c, plain(b)) != ("judgment_response", "post",
                                                                                        "none"):
                dated.append((day, i, n, c, plain(b)))
        dated.sort()
        branches = {}
        for key, tags, codes in p.classes:
            code = 0 if codes is None else int(np.frombuffer(codes, dtype=np.int8)[0])
            if code >= 0:
                branches[key] = fc.class_key(key, tags[code])
        dist = Dist(base)
        dist.update({k: base[t] for k, t in branches.items()})
        rows.append(dict(steps=p.steps, classes=branches, per_edge=[dist[k].get(b) for k, b in p.edges], dated=[(day, n, c, b) for day, _, n, c, b in dated],
                         signature=tuple(sorted((n, c, b) for _, _, n, c, b in dated)),
                         keyed=feasible(p.edges, dist), edges=p.edges,
                         petition=int(tr.events.petition[row if len(tr.events.petition) == fc.draws.n else 0])))
    return rows, len(steps)


RULES = {
    "settlement_on_later_offering": "the current walk asks the ruling-day I2 settlement after the floor; its offer "
                                    "(cash on the settlement date) then includes a later-dated floor offering",
    "ruling_dropped": "the current walk answers a later-dated response or floor first, and the ruling dated "
                      "before them is never asked",
    "listing_after_petition": "the current walk books the listing before a nonpayment petition dated earlier "
                              "than it, so the listing is asked after the petition",
    "stay_settlement_skipped": "not date order: the chronological walk dates the I4 (stay) settlement on the stay's "
                              "approval, but the question's replay advances it before the waiting decisions that "
                              "fund the stay are booked, dates it never, and skips it without a step",
    "floor_moved_by_ruling": "the current walk answers floor 1 on its pre-ruling day and group; the ruling dated "
                             "before it (set aside) moves it to day 179 and group 0, where that answer is not offered "
                             "or follows a petition",
    "post_ruling_levy_response": "the response on the levy day of a writ queued before the ruling (and the "
                                 "appeal it reads), which the current walk drops with the I1 window",
}


def classify(h, mine, theirs_sigs):
    """The rule that explains why history h (of walker `mine`) is not in the other walker's list."""
    dated = {(n, c): (b, d) for d, n, c, b in h["dated"]}
    sig = set(h["signature"])
    if mine == "current":
        if ("settle", "I4") in dated:
            return "stay_settlement_skipped"
        if ("settle", "I2") in dated:
            return "settlement_on_later_offering"
        if ("post_trial_ruling", "") not in dated:
            return "ruling_dropped"
        if ("listing", "") in dated and 0 <= h["petition"] < dated[("listing", "")][1]:
            return "listing_after_petition"
        if dated.get(("post_trial_ruling", ""), ("",))[0] == "set_aside" and ("cash_floor", "1") in dated:
            return "floor_moved_by_ruling"
        return "unexplained"
    # The current walk's counterpart can carry several of its additions at once: take the smallest combination
    # that matches, and name the history by the first rule (in the order above) that combination needs.
    import itertools

    base = {x for x in sig if x[0] not in ("post_trial_ruling", "appeal")}
    listing = [set()] + [{("listing", "", b)} for b in ("compliant", "suspended", "hearing")]
    stay = [set()] + [{("settle", "I4", b)} for b in ("yes", "no")]
    combos = sorted(itertools.product((sig, base), (set(), {("settle", "I2", "no")}), listing, stay),
                    key=lambda c: (c[0] is base) + sum(map(bool, c[1:])))
    for core, i2, li, i4 in combos:
        if tuple(sorted(core | i2 | li | i4)) in theirs_sigs:
            for rule, needed in (("stay_settlement_skipped", i4), ("ruling_dropped", core is base),
                                 ("listing_after_petition", li), ("settlement_on_later_offering", i2)):
                if needed:
                    return rule
    if any(tuple(sorted(sig | {("cash_floor", "1", b)})) in theirs_sigs for b in ("initiate_offering", "file", "neither")):
        return "floor_moved_by_ruling"
    if ("judgment_response", "post") in dated or dated.get(("appeal", ""), ("",))[0] == "yes":
        return "post_ruling_levy_response"
    return "unexplained"


def explain(name):
    cur, _ = histories(name, "current")
    chrono, _ = histories(name, "chronological")
    by = {"current": cur, "chronological": chrono}
    sig = {k: {h["signature"] for h in v} for k, v in by.items()}
    report = {}
    for mine, theirs in (("current", "chronological"), ("chronological", "current")):
        only = [h for h in by[mine] if h["signature"] not in sig[theirs]]
        groups, examples, signatures = Counter(), defaultdict(list), defaultdict(set)
        for h in only:
            k = classify(h, mine, sig[theirs])
            groups[k] += 1
            signatures[k].add(h["signature"])
            if len(examples[k]) < 2:
                examples[k].append(dict(history=h["dated"], petition=h["petition"], keyed=h["keyed"]))
        report[f"{mine}_only"] = dict(paths=len(only), signatures=len({h["signature"] for h in only}),
                                      kinds={k: dict(paths=v, signatures=len(signatures[k]))
                                             for k, v in groups.most_common()}, examples=examples)
    for label, rows in by.items():
        report[f"{label}_keyed_mass"] = (None if any(h["keyed"] is None for h in rows)
                                          else sum(h["keyed"] for h in rows))
        report[f"{label}_infeasible_paths"] = sum(h["keyed"] is None for h in rows)
    report["rules"] = RULES
    out = Path(f"var/diag/001-1g/{name}-unshared.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(json.dumps({k: (v if not isinstance(v, dict) else {kk: vv for kk, vv in v.items() if kk != "examples"})
                      for k, v in report.items() if k != "rules"}, indent=1))
    return by


def excess(name, label="current"):
    """Where a walker's keyed mass leaves 1: per question on the edge trie (paths sharing their earlier edges), the
    answers' probabilities (each from the class its path resolves for the question) and their sum."""
    rows, _ = histories(name, label)
    nodes = defaultdict(lambda: defaultdict(set))  # (edge prefix, question) -> answer -> {(probability, class)}
    reach = defaultdict(set)
    for h in rows:
        mass = 1.0
        for j, ((k, b), q) in enumerate(zip(h["edges"], h["per_edge"], strict=True)):
            prefix = tuple(h["edges"][:j])
            nodes[(prefix, k)][b].add((round(q, 12), h["classes"].get(k, k)))
            reach[(prefix, k)].add(round(mass, 12))
            mass *= q
    bad = []
    for (prefix, k), answers in nodes.items():
        split = {b: v for b, v in answers.items() if len(v) > 1}
        total = sum(max(q for q, _ in v) for v in answers.values())
        if split or abs(total - 1) > 1e-9:
            bad.append(dict(question=k, depth=len(prefix), reach=sorted(reach[(prefix, k)]),
                            answers={b: sorted(v) for b, v in answers.items()}, total_max=total,
                            after=prefix[-3:]))
    print(json.dumps(dict(root=name, walker=label, mass=sum(h["keyed"] for h in rows), questions=len(nodes),
                          inconsistent=len(bad)), indent=1))
    out = Path(f"var/diag/001-1g/{name}-{label}-excess.json")
    out.write_text(json.dumps(bad, indent=1, default=str) + "\n")
    return bad


if __name__ == "__main__":
    explain(sys.argv[1]) if "--excess" not in sys.argv else excess(sys.argv[1], sys.argv[3] if len(sys.argv) > 3
                                                                    else "current")
