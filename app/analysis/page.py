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

# Every text below that names a party uses {company} (the borrower) or {claimant} (the other side of the dispute);
# page_payload fills them from the run's own inputs (`parties`), so no party name is written here.
DECIDERS = ("Court and jury", "{claimant}", "{company}", "Noteholders and the exchange")
ACTOR_GROUP = {"court": "Court and jury", "jury": "Court and jury", "judgment creditor": "{claimant}",
               "judgment debtor": "{company}", "issuer": "{company}", "board": "{company}", "stockholders": "{company}",
               "borrower": "{company}", "the company": "{company}",
               "holders of 25% (or the trustee)": "Noteholders and the exchange",
               "holders": "Noteholders and the exchange", "noteholders (three or more)": "Noteholders and the exchange",
               "hearings panel": "Noteholders and the exchange"}  # keys: the contract's actor names
INTERVALS = {"I0": "before the verdict", "entry": "on the day the judgment is entered", "I1": "before the ruling", "I2": "after the ruling, before the appeal deadline",
             "I3": "after the appeal deadline", "I4": "during a stayed appeal", "post": "after the ruling"}
CONTEXT = {"claimed": "before the verdict", "claimant_theory": "after a verdict on the claimant's theory",
           "without_principal_measure": "after a lower award", "no_award": "after no award",
           "raise": "equity can be raised", "noraise": "no equity can be raised", "levied": "after a seizure of cash", "unlevied": "no seizure of cash", "appealed": "on appeal", "final": "no appeal",
           "stay_pending": "request to pause collection pending", "pay": "", "nopay": "paying in full is out of reach",
           "first": "", "after_seek": "after seeking a sale or financing", "entered": "",
           "delisted_panel": "delisted after the panel", "delisted_suspension": "suspended without a hearing",
           "ripe": "on the notes' judgment-default date", "entered_not_acted": "no repayment demand on the entered judgment",
           "bank": "on bank data alone", "cash_exhausted": "cash has run out", "motions_pending": "before the ruling"}
# Outcome by the end of the period, in the order the bar shows them.
CLASSES = (("filed_enforcement", "Filed: {claimant} enforcement"), ("filed_notes", "Filed: notes"),
           ("filed_cash", "Filed: short of cash"), ("settled", "Settled"), ("paid", "Paid"),
           ("stayed", "Stayed on appeal"), ("unresolved", "Unresolved"), ("vacated", "Vacated or new trial"))
OUTCOME_CLASS = {"settled": "settled", "paid": "paid", "stayed": "stayed", "unresolved": "unresolved",
                 "vacated": "vacated", "new_trial": "vacated"}


def decider(actor: str) -> str:
    """The group a contract actor decides for; an actor named with its body's own name ('<exchange> hearings panel')
    matches by the role that ends it."""
    return next((g for k, g in ACTOR_GROUP.items() if actor == k or actor.endswith(" " + k)), "{company}")


def parties(borrower: str, d) -> dict[str, str]:
    """The names the page's texts use, from the run's own inputs: the borrower and the other side of its dispute."""
    from app.analysis.build import short_name

    return {"company": short_name(borrower), "claimant": short_name(d.counterparty) if d is not None else "the claimant"}


def named(text: str, names: dict[str, str]) -> str:
    """Fill {company} and {claimant} in a page text."""
    import re

    return re.sub(r"\{(company|claimant)\}", lambda m: names[m.group(1)], str(text))


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
            parts.append({"judgment": "after noteholders demand repayment on the judgment default" + (
                              " on the entered judgment" if rest == "I1" else ""),
                          "delisting": "after noteholders demand repayment on the delisting",
                          "repurchase": "after an unpaid repurchase"}[head]
                         + (f" ({INTERVALS.get(rest, CONTEXT.get(rest, rest))})"
                            if rest and not (head == "judgment" and rest == "I1") else ""))
        else:
            parts.append(class_text(c, ranges) or SHORT_TAG.get(c, ""))
    return "; ".join(dict.fromkeys(p for p in parts if p))


# Short row labels for the question list, by question id (the full question text is in the drill-down).
SHORT_LABELS = {
    "forecast_stay_approved": "Court pauses collection", "forecast_1963_good_cause": "Early collection in other states",
    "forecast_ts_liability_jmol": "Trade-secret liability set aside", "forecast_patent_jmol": "Patent verdict set aside",
    "forecast_ts_damages_ruling": "Damages: stands / remit / new trial", "forecast_trebling": "Trebling",
    "forecast_fees_awarded": "Fees awarded", "forecast_prejudgment_interest": "Pre-judgment interest",
    "forecast_injunction_ts": "Injunction", "forecast_execution_pending_motions": "{claimant} enforces before the ruling",
    "forecast_remittitur_accepted": "{claimant} accepts remittitur",
    "forecast_enforcement_after_final": "{claimant} enforces after ruling",
    "forecast_settlement_accept": "{claimant} accepts settlement", "forecast_stay_motion": "{company} asks to pause collection",
    "forecast_appeal": "{company} appeals", "forecast_settlement_offer": "{company} offers settlement",
    "forecast_debtor_response": "{company} response to enforcement",
    "forecast_petition_on_notes": "{company} files (noteholders demand repayment)",
    "forecast_petition_cash_floor": "{company} files (cash floor)",
    "forecast_petition_cash_out": "{company} files (cash runs out)", "forecast_reverse_split_board": "Board calls reverse split",
    "forecast_nasdaq_hearing": "Exchange hearing requested", "forecast_split_approved": "Stockholders approve split",
    "forecast_panel_exception": "Panel grants exception",
    "forecast_holders_act_judgment": "Noteholders demand repayment (judgment default)",
    "forecast_holders_act_delisting": "Holders act on delisting",
    "forecast_holders_involuntary": "Holders file after no-action period"}
SHORT_WHEN = {"I0": "Before verdict", "entry": "At entry", "I1": "Before ruling", "I2": "After ruling", "I3": "After appeal deadline", "I4": "During appeal",
              "post": "After ruling", "entered": "Before ruling"}
SHORT_TAG = {"levied": "after a seizure of cash", "unlevied": "no seizure of cash", "appealed": "on appeal", "final": "no appeal",
             "stay_pending": "pause requested", "nopay": "can't pay in full", "after_seek": "after seeking a sale",
             "delisted_panel": "delisted after the panel", "delisted_suspension": "suspended, no hearing",
             "none": "judgment set aside", "retrial": "new trial on damages", "stay_moved": "pause of collection requested",
             "stayed": "collection paused", "settled": "after a settlement", "paid": "after payment", "seeking": "seeking a sale",
             "notes_due": "notes due, unpaid", "delisted": "delisted", "motions_pending": "before ruling",
             "executing": "{claimant} enforcing", "cash_exhausted": "cash run out", "ripe": "notes' default date",
             "entered_not_acted": "entered judgment not acted on", "bank": "bank data alone",
             "claimed": "", "claimant_theory": "{claimant}'s theory", "no_award": "no award",
             "without_principal_measure": "lower award", "set_aside": "judgment set aside", "raise": "can raise equity",
             "noraise": "can't raise equity"}


def money_round(cents: int) -> str:
    v = cents / 100
    return f"${v / 1e6:.0f}M" if abs(v) >= 1e7 else f"${v / 1e6:.1f}M".replace(".0M", "M") if abs(v) >= 1e6 \
        else f"${v / 1e3:.0f}k"


def short_context(context: str, ranges: dict[str, tuple[int, int]]) -> str:
    """A row's second line: when (before or after the ruling), the amount class ('$42M-$113M owed') and whatever else
    tells the row apart."""
    when, amount, rest = "", "", []
    for c in (c for c in context.split("|") if c):
        base = c.removesuffix("_retrial")
        if c in SHORT_WHEN:
            when = when or SHORT_WHEN[c]
        elif base.startswith("amt") and base[3:].isdigit() or base in ranges:
            lo, hi = (int(base[3:]),) * 2 if base.startswith("amt") else ranges[base]
            amount = (f"{money_round(lo)} owed" if lo == hi else f"{money_round(lo)}–{money_round(hi)} owed") \
                + (", new trial" if c.endswith("_retrial") else "")
            when = when or "After ruling"
        elif c.startswith(("judgment_", "delisting_", "repurchase_")):
            head, _, sub = c.partition("_")
            rest.append({"judgment": "judgment default" + (" on the entered judgment" if sub == "I1" else ""),
                         "delisting": "delisting", "repurchase": "unpaid repurchase"}[head])
            if sub in SHORT_WHEN:
                when = when or SHORT_WHEN[sub]
            elif sub:
                rest.append(SHORT_TAG.get(sub, sub.replace("_", " ")))
        elif c == "motions_pending":
            when = when or "Before ruling"
        elif c in SHORT_TAG:
            rest.append(SHORT_TAG[c])
            when = when or ("After ruling" if c in ("none", "retrial") else "")
        elif c not in ("pay", "first"):
            rest.append(c.replace("_", " "))
    out = " · ".join(dict.fromkeys(x for x in (when, amount, *rest) if x))
    return out[:1].upper() + out[1:]


def filing_cause(steps: tuple) -> str:
    """The class of a filing on this path: its first filing step (the claimant's enforcement if none is named)."""
    for node, _ctx, branch in steps:
        if node in ("debtor_response", "judgment_response") and branch == "file":
            return "filed_enforcement"
        if (node == "judgment_default" and branch in ("yes", "holders_file")) or (
                node == "delisting_notes" and branch.startswith("petition")):
            return "filed_notes"
        if node in ("cash_floor", "cash_out") and branch in ("yes", "file"):
            return "filed_cash"
    return "filed_enforcement"


FILED_BY = {1: "{company} files", 2: "Noteholders demand repayment; filing", 3: "{company} files, short of cash"}
CAUSE_CLASS = {1: "filed_enforcement", 2: "filed_notes", 3: "filed_cash"}  # events.PETITION_CAUSES indices


def draw_classes(steps: tuple, marks: dict, days: int) -> np.ndarray:
    """Each trajectory's class by what the engine booked on it by the period's end (for trajectories with no
    petition in the period): a positive settlement paid, the judgment paid, the award vacated or set for a new trial,
    an effective stay, else Unresolved. A path's 'settles' or 'pays' branch books nothing where the amount is zero or
    cash falls short, and those trajectories are not Settled or Paid."""
    n = len(next(iter(marks.values())))
    out = np.full(n, "unresolved", dtype=object)
    vacated = any(s[0] == "ruling" and s[2] in ("none", "retrial") for s in steps)
    for name, cls in (("stayed", "stayed"), ("ruled", "vacated" if vacated else None), ("paid", "paid"),
                      ("settled", "settled")):  # later rows win: a settlement or payment ends a stay
        if cls is not None and name in marks:
            out = np.where(np.asarray(marks[name]) < days, cls, out)
    return out


def outcome_shares(steps: tuple, outcome: str, petition_p: float, cause: np.ndarray | None = None,
                   marks: dict | None = None, days: int | None = None) -> dict[str, float]:
    """The path's draws by outcome class at the end of the period, classed by draw, not by path: the draws whose
    petition falls inside the horizon are Filed (by the rule that booked it); the rest are classed by what the engine
    booked on each (`draw_classes`, given the trace's marks), or else by the path's outcome. So the Filed shares,
    weighted by path probability, sum to the bankruptcy probability exactly, and Settled counts only draws that
    paid a settlement."""
    rest = {OUTCOME_CLASS.get(outcome, "unresolved"): 1.0}
    if marks is not None and days is not None:
        unfiled = (cause == 0) if cause is not None else np.ones(len(next(iter(marks.values()))), dtype=bool)
        if unfiled.any():
            names, counts = np.unique(draw_classes(steps, marks, days)[unfiled], return_counts=True)
            rest = {str(k): int(c) / int(unfiled.sum()) for k, c in zip(names, counts, strict=True)}
    out = {k: v * (1.0 - petition_p) for k, v in rest.items()} if petition_p < 1.0 else {}
    if petition_p > 0.0:
        filed = cause[cause > 0] if cause is not None else np.zeros(0)
        if filed.size:  # by the rule that booked each trajectory's earliest petition
            for code, name in CAUSE_CLASS.items():
                n = int((filed == code).sum())
                if n:
                    out[name] = out.get(name, 0.0) + petition_p * n / filed.size
        else:
            out[filing_cause(steps)] = petition_p
    return out


def _when(review: date, day: np.ndarray | None, exact: bool = False) -> str:
    """The median day inside the horizon, as '5 Dec' (exact) or 'Dec'."""
    if day is None:
        return ""
    d = np.asarray(day)
    if not d.size:
        return ""
    t = review + timedelta(days=int(np.median(d)) + 1)
    return f"{t.day} {t:%b}" if exact else f"{t:%b}"


VERDICT = {"no_award": "Jury: no award", "without_principal_measure": "Jury: lower award",
           "claimant_theory": "Jury adopts {claimant}'s damages theory"}
SETTLE = {"I0": "Settles before the verdict", "I1": "Settles before the ruling", "I2": "Settles after the ruling", "I3": "Settles after the appeal deadline",
          "I4": "Settles during the appeal"}
FILING = {("debtor_response", "file"): "{company} files", ("judgment_response", "file"): "{company} files",
          ("judgment_default", "yes"): "Noteholders demand repayment; filing",
          ("judgment_default", "holders_file"): "Noteholders demand repayment and file",
          ("delisting_notes", "petition_delist"): "Noteholders demand repayment after the delisting; filing",
          ("delisting_notes", "petition_delist_holders"): "Noteholders demand repayment after the delisting and file",
          ("delisting_notes", "petition_repurchase"): "Notes not bought back as required; filing",
          ("delisting_notes", "petition_repurchase_holders"): "Notes not bought back as required; noteholders file",
          ("cash_floor", "yes"): "{company} files at the cash floor", ("cash_floor", "file"): "{company} files at the cash floor",
          ("cash_out", "yes"): "{company} files when its cash runs out"}


def step_phrase(node: str, ctx: str, branch: str, ranges: dict[str, tuple[int, int]]) -> str | None:
    from app.disputes.forecast import _label

    if (node, branch) in FILING:
        return FILING[(node, branch)]
    if node == "ruling":
        if branch == "none":
            return "Court sets the judgment aside"
        if branch == "retrial":
            return "Court orders a new trial on damages"
        return "Court's ruling " + class_text(_label(branch), ranges).removeprefix("ruling ")
    if node == "verdict":  # the verdict's outcome class (contract pending_money_claim verdict_branches)
        return VERDICT.get(branch)
    return {("settle", "yes"): SETTLE.get(ctx, "Settles"),
            ("execute_pre_ruling", "yes"): "{claimant} enforces the judgment before the ruling",
            ("stay", "yes"): "Court pauses collection", ("registration_early", "yes"): "Court lets {claimant} collect in other states early",
            ("debtor_response", "pay"): "{company} pays", ("judgment_response", "pay"): "{company} pays the judgment",
            ("debtor_response", "seek_sale_or_financing"): "{company} seeks a sale or financing",
            ("post_trial_ruling", "set_aside"): "Court sets the judgment aside",
            ("cash_floor", "raise_equity"): "{company} raises equity at its cash floor",
            ("appeal", "yes"): "{company} appeals", ("enforce", "levy"): "{claimant} seizes cash to collect",
            ("listing", "delisted_panel"): "The exchange delists",
            ("listing", "delisted_suspension"): "The exchange suspends trading",
            ("judgment_default", "accelerated"): "Noteholders demand repayment; no filing",
            ("delisting_notes", "accelerated"): "Noteholders demand repayment after the delisting; no filing",
            ("delisting_notes", "repurchase_unpaid"): "Notes not bought back as required; no filing"
            }.get((node, branch))


def sequence(steps: tuple, day: list, petition: np.ndarray, review: date, days: int,
             ranges: dict[str, tuple[int, int]], cause: np.ndarray | None = None) -> str:
    """The path's events in order, each dated by its median day across the draws where it falls inside the period:
    'Ruling leaves $38.6M (Nov) → {claimant} levies (Dec) → {company} files (5 Dec)'."""
    # Listed by date, not by the tree's order: a filing at the cash floor is a tail step in the tree but can fall
    # before a later decision on the same path.
    out: list[tuple[float, str]] = []
    for i, (node, ctx, branch) in enumerate(steps):
        text = step_phrase(node, ctx, branch, ranges)
        if text is None:
            continue
        if (node, branch) in FILING:
            inside_p = (petition >= 0) & (petition < days)
            p = petition[inside_p]
            if cause is not None and p.size:  # named by the rule that booked most of these petitions
                codes, counts = np.unique(cause[inside_p], return_counts=True)
                text = FILED_BY.get(int(codes[np.argmax(counts)]), text)
            out.append((float(np.median(p)), f"{text} ({_when(review, p, exact=True)})") if p.size
                       else (float("inf"), f"{text}, after the period"))
            break
        d = np.asarray(day[i]) if i < len(day) else np.zeros(0)
        inside = d[d < days]
        out.append((float(np.median(inside)), f"{text} ({_when(review, inside)})") if inside.size
                   else (float("inf"), f"{text}, after the period"))
    return " → ".join(t for _, t in sorted(out, key=lambda x: x[0])) or "Nothing decided in the period"


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


def _short_money(text: str) -> str:
    """'$39,165,700.64' -> '$39.2M' in a computed figure; whole dollars ('$279,808') below $1M."""
    import re

    def one(m) -> str:
        v = float(m.group(0)[1:].replace(",", ""))
        return f"${v / 1e6:.1f}M" if v >= 1e6 else f"${v:,.0f}"
    return re.sub(r"\$[\d,]+(?:\.\d+)?", one, str(text))


def _whole(text) -> str:
    """'$10,000,000.00' -> '$10,000,000' inside a sourced term."""
    import re

    return re.sub(r"(\$[\d,]+)\.00\b", r"\1", str(text))


def _dist_text(v: dict) -> str:
    """{'p5': x, 'p50': y, 'max': z} -> 'P5 x, median y, highest z'."""
    names = {"p5": "P5", "p50": "median", "p95": "P95", "max": "highest"}
    return ", ".join(f"{names.get(k, k.replace('_', ' '))} {_short_money(x)}" for k, x in v.items() if k != "basis")


# What code did to get each computed figure a question is given (forecast.Forecaster.path_facts), in words.
ARITHMETIC = {
    "decision_date": "when the decision falls, across the simulations that reach it",
    "projected_available_cash_at_decision_date": "opening cash + projected operating flows from the bank data + the "
                                                  "dispute's cash to that date, across the simulations that reach "
                                                  "the decision",
    "cash_balance_at_decision": "opening cash + projected operating flows from the bank data + the dispute's cash to "
                                "that date",
    "amount_owed_at_decision": "the judgment on this path (as entered, or as the ruling leaves it) + statutory "
                               "interest to the decision date - amounts already collected",
    "operating_need_30_days_at_decision": "the lowest point of the next 30 days' cumulative operating flows",
    "bond_collateral_required": "the amount owed + 28 U.S.C. §1961 interest over the appeal, times the collateral "
                                "share",
    "reduced_security_offered": "the company's cash above its 30-day operating need on the approval day, when the security is posted",
    "judgment_after_ruling": "the range of judgment amounts the ruling outcomes shown together here leave",
}


def _fact_lines(facts: dict) -> list[str]:
    """Path facts code computed for a question, one plain line each, each amount with its arithmetic. The judgment
    components are Record steps of their own (drill_down)."""
    out = []
    for k, v in facts.items():
        name = k.replace("_", " ").capitalize()
        how = ARITHMETIC.get(k, "")
        if k in ("components", "pending_motions", "notes"):  # Record and Law steps of their own (drill_down)
            continue
        if k == "settlement_offer" and isinstance(v, dict):
            amt = _dist_text(v["amount"]) if "amount" in v else "none in these simulations"
            need = f" (30-day need: median {_short_money(v['thirty_day_operating_need'])})" \
                if "thirty_day_operating_need" in v else ""
            out.append(f"Settlement offer: {amt} = {v.get('basis', '')}{need}; paid as {v.get('payment', '')}")
        elif k == "contract_dates" and isinstance(v, dict):
            out += [f"{what[0].upper()}{what[1:]}: {when}" for what, when in v.items()]
        elif isinstance(v, dict):
            basis = v.get("basis") or how
            out.append(f"{name}: {_dist_text(v)}" + (f" = {basis}" if basis else ""))
        elif isinstance(v, list):
            if v:
                out.append(f"{name}: " + "; ".join(", ".join(str(x) for x in e.values()) if isinstance(e, dict)
                                                   else str(e) for e in v))
        elif k == "share_of_trajectories_where_it_arises":
            out.append(f"Arises in {v:.0%} of simulations inside the period")
        else:
            out.append(f"{name}: {_short_money(v)}" + (f" = {how}" if how else ""))
    return out


def _ref_label(ref: str) -> str:
    """'D. Del. 1:21-cv-01417, Dkt. 602' -> 'D.I. 602' (the link's text)."""
    import re

    m = re.search(r"(?:D\.I\.|Dkt\.)\s*(\d+(?:-\d+)?)", ref or "")
    return f"D.I. {m.group(1)}" if m else "source"


def docket_url(ref: str, links: dict[str, str]) -> str:
    """The catalog URL of a docket entry named in `ref` ('D.I. 616-1', 'Dkt. 602'); the docket report if the entry
    itself is not in the catalog (a sealed filing); '' if `ref` names none."""
    import re

    m = re.search(r"(?:D\.I\.|Dkt\.)\s*(\d+)(?:-(\d+))?", ref or "")
    if not m:
        return ""
    tail = f".{m.group(1)}.{m.group(2) or 0}.pdf"
    return next((u for u in links.values() if u.endswith(tail)),
                next((u for t, u in links.items() if "docket report" in t), ""))


def loan_rule(setup) -> str:
    """How the company's cash reaches Slope's line, from the setup's collection mode and cash floor (Setup.collection,
    Setup.need_days): the sentence the page shows for every decision that moves the company's cash."""
    if setup.collection == "debit":
        how = ("Slope debits each installment in full on its due date when the company's available cash covers it; a "
               "debit that fails stays past due and is retried on the next due date or month-end")
    elif setup.collection == "protect_need":
        how = f"Slope collects each installment only from cash above the {setup.need_days}-day operating need"
    else:
        raise ValueError(f"No collection rule {setup.collection!r}")
    return (f"On the loan: through the company's cash. {how}. Slope funds a draw only while nothing is overdue and "
            "there is no bankruptcy filing")


def mechanism(node: str, spec: dict, model: dict, setup) -> list[str]:
    """How the decision moves cash, and how that reaches the loan: the model node's effects by branch (parameters
    and rules in words), then the line's rule for a filing or for the company's cash."""
    import re

    rules, params = model.get("rules", {}), model.get("parameters", {})

    def plain(t: str) -> str:
        def one(m) -> str:
            k = m.group(0)
            if k in params and params[k].get("value") is not None:
                return f"{params[k]['value']} days" if k.endswith("_days") else str(params[k]["value"])
            if k in rules:
                return rules[k].get("citation", k).split(":")[0]
            return k.replace("_", " ")
        return re.sub(r"\b[a-z0-9]+(?:_[a-z0-9]+)+\b", one, t)
    out = [f"{b.replace('_', ' ').capitalize()}: {plain(e)}" for b, e in (spec.get("effects") or {}).items()]
    if spec.get("effect"):
        out.append(plain(spec["effect"]))
    files = node.startswith("petition") or node in ("holders_involuntary", "debtor_response")
    if files:
        eff = model["templates"].get("bankruptcy_effects", {}).get("effects", {})
        out += [f"A filing: {plain(eff['petition'])}"] if "petition" in eff else []
        out.append("On the loan: from the petition Slope collects nothing and funds no draw; the balance owed is "
                   "frozen, and collections in the 90 days before it are clawback exposure")
    else:
        out.append(loan_rule(setup))
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
               links: dict[str, str], node: str = "", d=None, *, setup) -> dict:
    """The 'why this probability' chain: each step tagged Law / Record / Data / Calculation / Cash / Jev, sourced
    steps with links to their evidence, computed amounts with their arithmetic, the facts Jev is given, the source
    quotes and Jev's answer."""
    terms = {k: v for t in model["templates"].values() for k, v in t.get("terms_from_instrument", {}).items()}
    steps = [{"tag": "Law", "text": model["rules"][r]["citation"] if r in model["rules"] else terms.get(r, r)}
             for r in spec["standard"]]
    supplied: dict[str, list[str]] = {}
    for e in (judgment.evidence if judgment is not None else []) or []:
        if isinstance(e, dict):
            for item in e.get("supplies", []):
                supplied.setdefault(item, []).append(e.get("source", ""))
    for x in spec["record_items"]:
        srcs = list(dict.fromkeys(supplied.get(x, [])))
        steps.append({"tag": "Record", "text": f"{x}: " + ("; ".join(srcs) if srcs else "not in the record"),
                      "links": [{"text": t, "url": links[t]} for t in srcs if links.get(t)]})
    comps = {label_head(c.label): c for c in (d.components if d else [])}
    for f in facts.get("components", []):
        c = next((c for lab, c in comps.items() if lab.startswith(f["component"]) or f["component"].startswith(lab)),
                 None)
        row = component_row(c, d, model) if c is not None else {}
        cited = [t for t in (f.get("source") or "").split("; ") if t]  # the passages the component cites
        refs = ([d.order_reference] + ([c.motion] if c.motion else []) if c is not None and c.status == "awarded"
                and not cited else [row["source"]] if row.get("source") and not cited else [])
        text = f"{f['component']}: {_whole(f['amount'])}, {f['status']}" + (
            f" ({'; '.join(cited)})" if cited else f" ({refs[0]})" if refs else "")
        if len(refs) > 1:
            text += f"; the pending motion {refs[1]} decides it"
        if f.get("remittitur_scenario") and row.get("remittitur"):
            text += f"; {row['remittitur'][0].lower()}{row['remittitur'][1:]}"
            refs.append(row["remittitur"])
        steps.append({"tag": "Record", "text": text,
                      "links": [{"text": t, "url": links[t]} for t in cited if links.get(t)]
                      + [{"text": _ref_label(r), "url": u} for r in refs if (u := docket_url(r, links))]})
    for mo in facts.get("pending_motions", []):
        steps.append({"tag": "Record", "text": f"Pending: {mo['motion']}, {mo['kind']}; briefing closes "
                                               f"{mo['briefing_closes']}",
                      "links": [{"text": _ref_label(mo["motion"]), "url": u}]
                      if (u := docket_url(mo["motion"], links)) else []})
    steps += [{"tag": "Data", "text": model["parameters"][name]["basis"]} for name in spec.get("timing_parameters", [])
              if model["parameters"][name].get("basis")
              and model["parameters"][name].get("disposition") in ("data", "sourced")]
    if isinstance(facts.get("notes"), dict):  # the notes' terms, as the indenture states them
        steps.append({"tag": "Law", "text": "Notes: " + "; ".join(
            f"{a.replace('_', ' ')}: {_short_money(b) if a == 'principal' else _whole(b)}"
            for a, b in facts["notes"].items())})
    steps.append({"tag": "Calculation", "text": f"When it is decided: {spec['timing']}"})
    steps += [{"tag": "Calculation", "text": line} for line in _fact_lines(facts)]
    steps += [{"tag": "Cash", "text": line} for line in mechanism(node, spec, model, setup)]
    quotes = []
    for e in (judgment.evidence if judgment is not None else []) or []:
        if isinstance(e, dict):
            for q in e.get("quotes", []):
                quotes.append({"quote": q, "source": e.get("source", ""), "date": e.get("date", ""),
                               "link": links.get(e.get("source", ""), "")})
    answer = None
    if judgment is not None and not neutral:
        answer = {"distribution": judgment.distribution, "confidence": judgment.confidence,
                  "observation_id": judgment.observation_id,
                  "readings": {k: {**v, "link": links.get(v.get("source", ""), "")} if isinstance(v, dict) else v
                               for k, v in (judgment.readings or {}).items()}}
    steps.append({"tag": "Jev", "text": question})
    return {"steps": steps, "facts": facts, "assumptions": list(judgment.assumptions) if judgment else [],
            "quotes": quotes, "answer": answer}


def label_head(label: str) -> str:
    """A component's label up to its first ';' outside parentheses (the rest is its source note)."""
    depth = 0
    for i, ch in enumerate(label):
        depth += (ch == "(") - (ch == ")")
        if ch == ";" and depth == 0:
            return label[:i].strip()
    return label.strip()


def component_row(c, d, model: dict) -> dict:
    """A judgment component as the engine prices it: a compensatory award shows the model's remitted-amount scenario
    and its source (never a party's own remittitur request); a requested component (a pending claim) has none, since
    nothing has been awarded to remit; a sealed request shows as sealed; interest shows the rates the engine computes
    it at."""
    import re

    row = {"label": label_head(c.label), "amount": usd(c.amount_cents) if c.amount_cents is not None
           else "computed by statute" if c.statutory else "unknown", "status": c.status,
           "source": d.order_reference if c.status == "awarded" else c.motion,  # a request cites its own passage
           "theory": getattr(c, "theory", "")}
    if c.kind == "compensatory" and c.status == "awarded":  # a remittitur can only reduce an award already made
        sc = model.get("remittitur_scenarios", {})
        rem = sc.get("scenarios", {}).get("remitted", {})
        if sc.get("base") == "remitted" and rem.get("amount_cents"):
            ref = re.search(r"D\.I\. [\d-]+", rem.get("basis", ""))
            row["remittitur"] = (f"If remitted and accepted, the model uses {usd(rem['amount_cents'])}"
                                 + (f" ({ref.group(0)})" if ref else ""))
        elif sc.get("base"):
            row["remittitur"] = "If remitted and accepted, the model uses the verdict amount"
    if c.kind == "prejudgment_interest":
        sealed = re.search(r"sealed[^;]*?(D\.I\. \d+)", c.label)
        if c.amount_cents is None and sealed:
            row.update(amount="sealed; amount not public", source=sealed.group(1))
        rules = model.get("rules", {})
        bps = rules.get(rules.get("nc_24_5_b", {}).get("rate_rule", ""), {}).get("value")
        if bps and d.commenced:
            row["model"] = (f"Model, if granted: {bps / 100:g}% a year simple on compensatory damages from "
                            f"{d.commenced:%-d %b %Y} to entry (N.C. Gen. Stat. §24-5(b), §24-1); after entry, "
                            f"the 28 U.S.C. §1961 rate")
    return row


def case_terms(d, review: date, horizon: date, model: dict, links: dict[str, str] | None = None,
               cited: dict[str, str] | None = None) -> dict:
    """Judgment components with status and source (linked to the filing): the passage a component cites
    (`cited`: component id -> its findings' source titles), else the order or motion; the notes' default terms;
    the dated deadlines."""
    from app.disputes.forecast import notes_default_text

    comps = []
    for c in d.components:
        r = component_row(c, d, model)
        if (cited or {}).get(c.component_id):
            r["source"] = cited[c.component_id]
            r["link"] = (links or {}).get(r["source"].split("; ")[0], "")
        else:
            r["link"] = docket_url(r["source"], links or {})
        comps.append(r)
    for r in comps:
        if r.get("remittitur"):
            r["remittitur_link"] = docket_url(r["remittitur"], links or {})
    notes = []
    missing = "not recorded"  # a term the agent did not instantiate is shown as such, never as zero
    for f in (x for x in d.financing if x.status != "superseded"):
        money = (lambda c: usd(c) if c is not None else missing)  # noqa: E731
        dates = ", ".join(x.strftime("%-d %b %Y") for x in f.interest_dates)
        coupon = missing if f.coupon_cents is None else f"{usd(f.coupon_cents)}" + (f" due {dates}" if dates else "")
        default = notes_default_text(model, f) or missing  # the indenture's words, as Jev's facts quote them
        rep = f.repurchase_business_days
        listing = (f"delisting is a fundamental change: repurchase within {rep[0]}–{rep[1]} business days of notice"
                   if rep and len(rep) == 2 else missing)
        notes.append({"title": f.title, "terms": [("Principal", money(f.principal_cents)), ("Coupon", coupon),
                                                   ("Judgment default", default), ("Listing", listing)]})
    deadlines = [(m.briefing_close, f"Briefing closes on {m.motion_id}") for m in d.motions if m.briefing_close]
    lags = model["parameters"]["ruling_lag_days"]["sample"]
    if deadlines:
        close = min(x for x, _ in deadlines)
        deadlines.append((close + timedelta(days=min(lags)), f"Earliest ruling (briefing close + {min(lags)} days, "
                                                             f"measured on this docket)"))
    for f in d.financing:
        if f.listing_deadline:
            deadlines.append((f.listing_deadline, "Listing compliance deadline"))
        deadlines += [(x, f"Notes coupon due ({usd(f.coupon_cents)})") for x in f.interest_dates]
    deadlines.append((horizon, "End of the period"))
    seen, rows = set(), []
    for when, what in sorted(deadlines):
        if (when, what.split(" on ")[0]) in seen:
            continue
        seen.add((when, what.split(" on ")[0]))
        rows.append({"date": when.isoformat(), "what": what.split(" on D.I.")[0] if "Briefing" in what else what})
    return {"components": comps, "notes": notes, "deadlines": rows}


# Per-path means the browser reweights: the lender bridge's tiles (funded -> payments due -> collected, then due and
# unpaid split into past due and frozen by a filing, the chance of a filing and clawback exposure) and the Exposure
# tab's peak outstanding and capital tied up (time-weighted outstanding).
TILES = ("funded", "due", "collected", "unpaid", "past_due", "frozen_due", "not_yet_due", "petition_p", "clawback",
         "stayed", "peak_outstanding", "avg_outstanding")


def path_scalars(r) -> dict[str, np.ndarray]:
    """Per path, the mean over its draws of each TILES figure. Payments due, past due and frozen by the end of the
    period are the per-day records' last day (core.Reduction.per_day). Amounts are whole cents; due and unpaid is
    payments due less collected and frozen is due and unpaid less past due, so on every path collected + due and
    unpaid = payments due and past due + frozen = due and unpaid exactly (the bridge's two parts reconcile). Not yet
    due is the analysis's own per-trajectory figure (engine: installments falling due after the horizon, zero where a
    petition froze the claim), so 'due' stays due by the horizon and the contractual total is never shown."""
    last = {k: np.rint(r.per_day[k][:, -1] / r.draws) for k in ("due_cum", "past_due")}
    collected = np.rint(r.means["collected"])
    unpaid = last["due_cum"] - collected
    return {"funded": np.rint(r.means["drawn"]), "due": last["due_cum"], "collected": collected, "unpaid": unpaid,
            "past_due": last["past_due"], "frozen_due": unpaid - last["past_due"],
            "not_yet_due": np.rint(r.means["not_yet_due"]),  # installments due after the horizon, on no-filing draws
            "petition_p": np.round(r.means["petition_p"], 4), "clawback": np.rint(r.means["preference"]),
            "stayed": np.rint(r.means["stayed"]),  # owed on the filing day (balance at filing); zero with no filing
            "peak_outstanding": np.rint(r.means["peak_outstanding"]),
            "avg_outstanding": np.rint(r.means["avg_outstanding"])}


def bridge(bank: dict[str, float], research: dict[str, float]) -> dict[str, float]:
    """What research adds to collections, in two parts (page.js bridgeLine): the change in payments due (Slope funds
    more or less, so more or fewer installments fall due) and the change in due and unpaid, sign reversed. Collected
    = payments due - due and unpaid in each view, so the parts sum to the collected difference."""
    return {"collected": research["collected"] - bank["collected"], "due": research["due"] - bank["due"],
            "unpaid": -(research["unpaid"] - bank["unpaid"])}
CHART = ("limit_mean", "outstanding_mean", "petition_cum_p", "frozen_mean", "cash_mean", "cash_p5", "cash_p50",
         "cash_p95", "collected_mean", "contractual")


def monthly_table(r, probs: np.ndarray, d: dict, months: list[tuple[int, int]]) -> list[dict]:
    """Per month, expected under `probs`: drawn, due, collected; at month end, past due and frozen by a filing, split
    into installments already due and not yet due; clawback-exposed collections; and the P5 of cash above the 30-day
    need at the month's due dates, after the amount due. Cumulatively, due - collected = past due + frozen (due), and
    frozen (due) + frozen (not yet due) = the frozen claim."""
    head = r.counts["headroom"].weighted(probs)
    q5 = r.bins["headroom"].quantiles(head, (0.05,))[0]
    due = np.diff(np.concatenate([[0], d["contractual"]]))
    coll = np.diff(np.concatenate([[0], d["collected_mean"]]))  # from the cumulative series, so the sums telescope
    rows = []
    for i, (y, m) in enumerate(months):
        idx = np.flatnonzero(r.month_of_day == i)
        last = int(idx[-1])
        rows.append({"month": f"{y}-{m:02d}", "drawn": int(sum(d["fundings_mean"][t] for t in idx)),
                     "due": int(due[idx].sum()), "collected": int(coll[idx].sum()),
                     "past_due": d["past_due_mean"][last], "frozen_due": d["frozen_due_mean"][last],
                     "frozen_not_due": d["frozen_mean"][last] - d["frozen_due_mean"][last],
                     "clawback": int(sum(d["clawback_mean"][t] for t in idx)),
                     "above_need_p5": int(q5[i]) if head[i].sum() > 0 else None})
    return rows


def collected_range(r, probs: np.ndarray, months: list[tuple[int, int]]) -> list[list[int]]:
    """Cumulative collected at each month end, P5 and P95 over the draws under Jev's answers, from the full
    reduction's collected counts (the reweight state drops them, so the page shows this range on Jev's answers only);
    the horizon's own figures exact (core.Reduction.collected_quantiles)."""
    if "collected" not in getattr(r, "counts", {}):
        return []
    p = np.asarray(probs, dtype=np.float64) / np.sum(probs)
    kq = r.bins["collected"].quantiles(r.counts["collected"].weighted(p), (0.05, 0.95))
    ends = [int(np.flatnonzero(r.month_of_day == i)[-1]) for i in range(len(months))]
    out = [[int(kq[0][t]), int(kq[1][t])] for t in ends]
    if hasattr(r, "collected_quantiles"):
        q = r.collected_quantiles(p)  # (P5, P50, P95) at the horizon
        out[-1] = [int(q[0]), int(q[-1])]
    return out


def chart_view(r, probs: np.ndarray, months: list[tuple[int, int]]) -> dict | None:
    """The daily series and monthly table the page draws, under `probs` (renormalised; None if they sum to zero)."""
    probs = np.asarray(probs, dtype=np.float64)
    total = probs.sum()
    if total <= 0:
        return None
    probs = probs / total
    d = r.daily(probs, collected_q=False)
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
        form = form_words(fc, fc.nodes[k]) if j.node.startswith("verdict_") and k in fc.nodes \
            and hasattr(fc, "verdict_context") else None  # a verdict-form question: the form's own words
        nodes.append({"key": k, "node": j.node, "question": q, "context": context_text(ctx, ranges), "form": form,
                      "label": SHORT_LABELS.get(j.question_id, q), "sub": short_context(ctx, ranges),
                      "actor": sp["actor"], "decider": decider(sp["actor"]),
                      "branches": branches, "jev": [j.distribution[b] for b in branches],
                      "detail": drill_down(sp, m, q, facts, j, neutral, links, j.node,
                                           model.disputes.get(j.instance_id), setup=setup)})
    bank = bank_rows(model, fc, m, spec, questions, links, neutral, off=len(nodes))
    nodes += bank["nodes"]
    lead = [c[0] for c in model.combos]
    d0 = model.disputes[lead[0].instance_id] if lead and lead[0].steps else None
    classes, seqs, seq_ix = [], [], {}
    pet = np.round(a.r.means["petition_p"], 4)  # the tile's own per-path values, so Filed sums to the tile exactly
    shares = []
    for i, p in enumerate(lead):
        tr = fc.trace(d0, p.steps) if d0 is not None and p.steps else None
        sh = outcome_shares(p.steps, p.outcome, float(pet[i]), tr.cause if tr is not None else None,
                            tr.marks if tr is not None else None, a.days)
        shares.append(sh)
        classes.append(max(sh, key=sh.get))  # the main class, for the worst-paths table
        text = (sequence(p.steps, tr.day, tr.petition, setup.review, a.days, ranges, tr.cause) if tr
                else "No dispute events")
        seqs.append(seq_ix.setdefault(text, len(seq_ix)))
    cls_ix = {c: i for i, (c, _) in enumerate(CLASSES)}
    names = parties(borrower, d0)
    for n in nodes:
        n.update({k: named(n[k], names) for k in ("label", "sub", "context", "decider")})
        if n.get("form"):
            f = n["form"]
            f.update(answers={a: named(t, names) for a, t in f["answers"].items()}, plain_asks=named(f["plain_asks"], names),
                     earlier_plain=[named(t, names) for t in f["earlier_plain"]])
    seq_ix = {named(s, names): i for s, i in seq_ix.items()}
    probs = model.probs() if probs is None else probs
    months = [f"{y}-{mo:02d}" for y, mo in a.months]
    inputs, scenario = case_inputs(snapshot_id)
    vb = verdict_block(fc, d0, lead)
    return {
        "meta": {"borrower": borrower, "review": setup.review.isoformat(), "horizon": setup.horizon.isoformat(),
                 "limit_cents": int(a.line.limit[:, 0].min()), "fee_bps": setup.fee_bps,
                 "installments": setup.installments, "draws": a.ops.draws, "paths": len(model.combos),
                 "judgments": "neutral" if neutral else "jev",
                 "judgments_note": "No Jev answers yet: all questions at even odds" if neutral else "",
                 "probability_label": m["probability_label"]},
        "dates": [(setup.review + timedelta(days=t + 1)).isoformat() for t in range(a.days)], "months": months,
        "need_mean": np.rint(a.line.need.mean(axis=0)).astype(np.int64).tolist(),
        "pins": _pins(d0, m) if d0 is not None else {},
        "nodes": nodes, "composites": enc["composites"],
        "paths": {"edges": enc["paths"], "class": [[[cls_ix[c], round(v, 4)] for c, v in sh.items()] for sh in shares],
                  "seq": seqs,
                  "scalars": {k: v.tolist() for k, v in path_scalars(a.r).items()}},
        "classes": [named(label, names) for _, label in CLASSES], "sequences": list(seq_ix), "parties": names,
        "filing_steps": sorted({named(x, names) for x in (*FILING.values(), *FILED_BY.values())}),
        "bank": {"scalars": {k: float(model.bank_probs() @ v) for k, v in path_scalars(a.bank_r).items()},
                 "edges": bank["edges"], "path_scalars": {k: v.tolist() for k, v in path_scalars(a.bank_r).items()},
                 **chart_view(a.bank_r, model.bank_probs(), a.months)},
        "event": chart_view(a.r, probs, a.months),
        "collected_range": collected_range(a.r, probs, a.months),
        "worst": [{**w, "class": named(w["class"], names)} for w in _worst(stress_rows, classes, seqs, list(seq_ix))]
        if stress_rows else [],
        "case_terms": case_terms(d0, setup.review, setup.horizon, m, links, {
            c.component_id: "; ".join(dict.fromkeys(fc.hydrate(fc.findings[f])["source"] for f in c.finding_ids
                                                    if f in fc.findings)) for c in d0.components}
        ) if d0 is not None else {},
        "settings": [],
        "line": line_block(setup, inputs), "common": common_block(setup),
        "opening_cash_cents": int(getattr(getattr(getattr(fc, "draws", None), "basis", None), "opening", 0) or 0),
        "verdict": {**vb, "branches": [{**r, "label": named(r["label"], names)} for r in vb["branches"]]} if vb else {},
        "dispute": {"trial_started": d0.trial_started.isoformat() if d0 is not None and d0.trial_started else None,
                    "commenced": d0.commenced.isoformat() if d0 is not None and d0.commenced else None,
                    "notes": [{"principal_cents": f.principal_cents, "default_threshold_cents": f.judgment_default_threshold_cents,
                               "default_days": f.judgment_default_days,
                               "listing_deadline": f.listing_deadline.isoformat() if f.listing_deadline else None}
                              for f in (d0.financing if d0 is not None else ()) if f.status != "superseded"]},
        "narrative": [named(x, names) for x in scenario.get("narrative", [])],
        "assumption_text": assumption_text(scenario, names),
    }


def assumption_text(scenario: dict, names: dict[str, str]) -> dict[str, dict[str, str]]:
    """Each economic-assumption variant's label and note for the reader (case inputs: scenario.json
    assumption_variants), by id; 'central' for the central setting."""
    av = scenario.get("assumption_variants", {})
    out = {"central": {"label": av.get("central_label", ""), "note": named(av.get("central_note", ""), names)}}
    for v in av.get("variants", []):
        out[v["id"]] = {"label": named(v.get("label", ""), names), "note": named(v.get("note", ""), names)}
    return out


def case_inputs(snapshot_id: str) -> tuple[dict, dict]:
    """The case's run inputs and scenario (cases/<snapshot>/), or {} where a file is missing."""
    import json

    from app.config import CASES_DIR

    out = []
    for name in ("run_inputs.json", "scenario.json"):
        f = CASES_DIR / snapshot_id / name
        out.append(json.loads(f.read_text()) if f.exists() else {})
    return out[0], out[1]


def line_block(setup, inputs: dict) -> dict:
    """The line on the review date: when it opened and at what limit (run inputs), its outstanding principal, any
    past due amount and the installments still to fall due (Setup.exposure), and its draw rule."""
    e, opened = setup.exposure, (inputs.get("financing_plan", {}).get("line", {}).get("opened") or {})
    return {"opened": opened.get("date"), "opened_limit_cents": opened.get("limit_cents"),
            "principal_cents": e.principal_cents, "past_due_cents": e.past_due_cents,
            "installments": [[d.isoformat(), c] for d, c in e.installments],
            "draw_rule": "Slope funds a draw only while nothing is overdue and there is no bankruptcy filing"}


def common_block(setup) -> list[list[str]]:
    """The settings every path shares (the common financial model), in words, from the setup."""
    how = loan_rule(setup).split(". ")[1] if ". " in loan_rule(setup) else loan_rule(setup)
    fin = "; ".join(f"{f.kind} of {usd(f.amount_cents)} on {f.date:%-d %b %Y}" for f in setup.financing) \
        or "none booked: any raise is a decision on the path"
    cost = (f"from {setup.cost_plan.start:%-d %b %Y}, operating outflows other than legal fees and debt service "
            f"{setup.cost_plan.share_bps / 100:g}% lower" if setup.cost_plan else
            "the bank feed's own flows continued (receipts and outflows at their recent run rate)")
    return [["Collection", how], ["Cash floor", f"{setup.need_days} days of operating outflows; below it the company "
             "decides whether to raise equity, keep going or file"], ["Financing", fin], ["Costs", cost]]


def form_words(fc, n) -> dict:
    """A verdict-form question for the page: the form's own words (as Jev reads them) and, from the case inputs'
    verdict form, the reader's: each answer named by where it leads (a verdict branch by section 3's name, else the
    question's plain gloss of that answer), what is asked in plain words, and each earlier answer glossed."""
    out = dict(fc.verdict_context(n))
    qs = fc.m["case_verdict_form"]["questions"]
    tags = [c for c in n.context.split("|") if c]
    q = qs[tags[0]]
    plain = q.get("plain", {})
    out["answers"] = {a: VERDICT.get(q[a]) or plain.get(a, a) for a in ("yes", "no")}
    out["plain_asks"] = plain.get("asks", "")
    out["earlier_plain"] = []
    for x in tags[1:]:
        e, ans = x.split("=")
        gloss = qs[e].get("plain", {}).get(ans, "")
        out["earlier_plain"].append(f"{qs[e]['form']}: {ans.capitalize()}" + (f" ({gloss})" if gloss else ""))
    return out


def verdict_block(fc, d, lead: list) -> dict:
    """A pending claim's verdict: each branch's short label, its label template filled from the case inputs and its
    judgment amount (low and high: the claimant's branch over its enhancement settings), and each path's branch
    (index; -1 where it settles before the verdict, -2 where no verdict falls on it). {} for a dispute past trial."""
    labels = fc.labels(d) if d is not None and hasattr(fc, "labels") else {}
    if not labels:
        return {}
    from app.analysis.events import verdict_amount

    names = list(fc.m["templates"]["pending_money_claim"]["verdict_branches"])
    rows = []
    for b in names:
        amts = [verdict_amount(d, fc.m, b, {**(fc.sens or {}), "claimant_enhancements": x}) for x in (False, True)] \
            if b == "claimant_theory" else [verdict_amount(d, fc.m, b, fc.sens)] * 2
        rows.append({"key": b, "label": VERDICT.get(b, b), "text": labels.get(b, ""), "lo": min(amts), "hi": max(amts)})
    path = []
    for p in lead:
        v = next((names.index(s[2]) for s in p.steps if s[0] == "verdict" and s[2] in names), None)
        path.append(v if v is not None else -1 if any(s[0] == "settle" and s[2] == "yes" for s in p.steps) else -2)
    return {"branches": rows, "path": path, "before": SETTLE.get("I0", "Settles"), "claimed": labels.get("claimed", "")}


BANK_LABELS = {"forecast_petition_cash_floor": "The company files at its cash floor",
               "forecast_petition_cash_out": "The company files when its cash runs out"}


def bank_rows(model, fc, m: dict, spec: dict, questions: dict, links: dict, neutral: bool, off: int) -> dict:
    """The bank view's questions as page rows (after the research rows, from index `off`): each asked on bank data
    alone, with its drill-down (its facts, Jev's question and answer); and each bank path's edges over those rows,
    so page.js reweights the bank view as it does the research view."""
    from app.disputes.forecast import bank_state

    enc = encode_paths(model.bank_combos, model.bank_judgments)
    assert not enc["composites"]  # the bank view's chain has none
    ordinary = getattr(fc, "ordinary", False)  # spec §16.1: the forecast's own questions, the event given no cash effect
    rows = []
    for k, branches in zip(enc["keys"], enc["branches"], strict=True):
        j = model.bank_judgments[k]
        q = questions.get(j.question_id, j.event)
        facts = j.path_facts or (bank_state(fc, fc.bank_nodes[k])["path_facts"] if k in fc.bank_nodes else {})
        if ordinary:  # asked on the same record as the forecast: its record items, and its situation without the
            sp, ctx = spec[j.node], "|".join(k.split("|")[2:])  # dispute (page.js adds the no-cash-effect words)
            rows.append({"key": k, "node": j.node, "view": "bank", "ordinary": True, "question": q,
                         "context": context_text(ctx, fc.class_range), "label": SHORT_LABELS.get(j.question_id, q),
                         "sub": short_context(ctx, fc.class_range), "actor": sp["actor"],
                         "decider": decider(sp["actor"]), "branches": branches,
                         "jev": [j.distribution[b] for b in branches],
                         "detail": drill_down(sp, m, q, facts, j, neutral, links, j.node, setup=fc.setup)})
        else:
            sp = {**spec[j.node], "record_items": []}
            rows.append({"key": k, "node": j.node, "view": "bank", "question": q, "context": "asked on bank data alone",
                         "label": BANK_LABELS.get(j.question_id, q), "sub": "Asked on bank data alone",
                         "actor": sp["actor"], "decider": "Bank data", "branches": branches,
                         "jev": [j.distribution[b] for b in branches],
                         "detail": drill_down(sp, m, q, facts, j, neutral, links, j.node, setup=fc.setup)})
        for s in rows[-1]["detail"]["steps"]:  # the bank view's cash has no dispute: its event cash is the coupon
            s["text"] = s["text"].replace(" + the dispute's cash to that date", " + the notes' coupon to that date"
                                          if fc.instrument() is not None else " to that date")
    edges = [[x + off if i % 2 == 0 else x for i, x in enumerate(flat)] for flat in enc["paths"]]
    return {"nodes": rows, "edges": edges}


def _pins(d, m: dict) -> dict:
    close = min((x.briefing_close for x in d.motions if x.briefing_close), default=None)
    lags = m["parameters"]["ruling_lag_days"]["sample"]
    fin = next((f for f in d.financing if f.status != "superseded"), None)
    return {"briefing_close": close.isoformat() if close else None,
            "ruling_window": [(close + timedelta(days=min(lags))).isoformat(),
                              (close + timedelta(days=max(lags))).isoformat()] if close else None,
            "listing": fin.listing_deadline.isoformat() if fin and fin.listing_deadline else None,
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
def dev_settings() -> list[dict]:
    """The dev page's settings, from its case inputs (cases/<dev snapshot>/dev_settings.json)."""
    import json

    from app.config import CASES_DIR
    from app.disputes.akoustis_pre_d import SNAP

    f = CASES_DIR / SNAP / "dev_settings.json"
    return json.loads(f.read_text()) if f.exists() else []


PAGE_FORMAT = 8  # bumped when the cached dev state's shape changes, so an older pickle in var/dev is rebuilt


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

    specs = dev_settings()
    settings = {s["key"]: (settings or {}).get(s["key"], s["value"]) for s in specs}
    line_keys = {s["key"] for s in specs if s["kind"] == "line"}
    step = progress or (lambda *_: None)
    inputs = json.loads((CASES_DIR / SNAP / "run_inputs.json").read_text())
    setup = setup_from_inputs(inputs, REVIEW).with_controls({k: settings[k] for k in line_keys})
    feed, m = load_feed(SNAP), load_model()
    if settings["remittitur"] != m["remittitur_scenarios"]["base"]:
        m = copy.deepcopy(m)
        m["remittitur_scenarios"]["base"] = settings["remittitur"]
    sens = {k: v for k, v in settings.items() if k not in line_keys and k != "remittitur" and v not in (False, "shares")}
    borrower = inputs["baseline_profile"]["borrower"]
    step("tree", 0, 1)
    fc = Forecaster([judgment()], {}, borrower=borrower, review=REVIEW, horizon=setup.horizon, hydrate=lambda f: {},
                    model=m, setup=setup, basis=basis_for(feed, setup), sens=sens)
    per, bank_paths = fc.all_paths(), fc.bank_paths()
    js, bank_js = ({n.key: Judgment(key=n.key, instance_id=n.instance_id, node=n.node, question_id=n.question_id,
                                    event=n.event, assumptions=n.assumptions, window=n.window,
                                    distribution={b: 1 / len(n.branches) for b in n.branches}) for n in nodes.values()}
                   for nodes in (fc.nodes, fc.bank_nodes))
    model = EventModel({d.instance_id: d for d in fc.disputes}, js, per, fc.ordered(), neutral=neutral_map(js),
                       bank_paths=bank_paths, bank_judgments=bank_js)
    a = Analysis(feed, setup, model, sens=sens, dispute_model=m, progress=step)
    rows = Analysis(feed, setup, model, stress=True, sens=sens, dispute_model=m, progress=step).stress_rows
    rows = [{"index": i, **r} for i, r in enumerate(rows)]
    payload = page_payload(a, model, fc, borrower=borrower, snapshot_id=SNAP, neutral=True, stress_rows=rows)
    payload["meta"]["dev"] = True
    payload["settings"] = specs
    payload["settings_value"] = settings
    return {"payload": payload, "r": a.r, "bank_r": a.bank_r, "model": model, "months": a.months}


def reweight(state: dict, overrides: dict[str, list[float]] | None) -> dict | None:
    """The daily series and monthly table under the browser's node distributions (branch order as encoded). No
    re-simulation."""
    model = state["model"]
    ov = {k: {b: float(p) for b, p in zip(model.judgments[k].distribution, v, strict=True)}
          for k, v in (overrides or {}).items() if k in model.judgments}
    probs = model.probs(ov)
    out = chart_view(state["r"], probs, state["months"])
    if out is not None:  # the tiles too: an assumption variant's paths are not the page's (page.js reads its own)
        p = probs / probs.sum()
        out["metrics"] = {k: float(p @ v) for k, v in path_scalars(state["r"]).items()}
    bov = {k: {b: float(p) for b, p in zip(model.bank_judgments[k].distribution, v, strict=True)}
           for k, v in (overrides or {}).items() if k in model.bank_judgments}
    if out is not None and state.get("bank_r") is not None and model.bank_judgments:
        out["bank"] = chart_view(state["bank_r"], model.bank_probs(bov), state["months"])  # the bank rows' sliders
    return out


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

        full = {s["key"]: (settings or {}).get(s["key"], s["value"]) for s in dev_settings()}
        tag = hashlib.sha1(json.dumps({**full, "_format": PAGE_FORMAT}, sort_keys=True).encode()).hexdigest()[:10]
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
