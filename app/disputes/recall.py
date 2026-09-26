"""Recall check (spec §9): re-ask every conditional forecast with the parties' names replaced by their roles.

A forecast that moves when the names go away may be drawing on what Jev remembers about the parties rather than on
the record in front of it. Every place Jev sees the parties is rewritten: the case block, the question's context and
assumptions, the path facts, the record quotes and the evidence context. Aliases come from the instance's parties
(borrower, counterparty, the notes' issuer, the case reference) plus a short explicit list of tickers, case numbers,
judges, the trustee and product or supplier names that identify a party. The check is stored beside the analysis and
never changes its probabilities.
"""

from __future__ import annotations

import asyncio
import re

from app.disputes.forecast import Forecaster, ForecastJudge, Judgment, answer_distribution
from app.domain.investigation import DisputeInstance

DEFENDANT = "the defendant (the issuer of the notes)"
PLAINTIFF = "the plaintiff"
MOVED = 0.10  # a question moving more than this (10 points) is listed in the summary

# Names Jev can see in the record that identify a party (explicit, small). Longest match wins.
EXTRA: dict[str, str] = {
    "Qorvo, Inc. v. Akoustis Technologies, Inc.": "the plaintiff v. the defendant",
    "Qorvo v. Akoustis": "the plaintiff v. the defendant",
    "Akoustis Technologies, Inc.": DEFENDANT, "Akoustis Technologies": DEFENDANT, "Akoustis, Inc.": DEFENDANT,
    "Akoustis Inc.": DEFENDANT, "Akoustis": DEFENDANT, "AKTS": DEFENDANT, "1584754": "the defendant's filer number",
    "Qorvo, Inc.": PLAINTIFF, "Qorvo US": PLAINTIFF, "Qorvo": PLAINTIFF, "QRVO": PLAINTIFF,
    "1604778": "the plaintiff's filer number",
    "1:21-cv-01417": "the case", "21-cv-01417": "the case", "21-cv-1417": "the case", "21-1417": "the case",
    "2:23-cv-00180": "the defendant's own suit", "23-cv-00180": "the defendant's own suit",
    "Jon P. McCalla": "the judge", "McCalla": "the judge", "Richard G. Andrews": "the earlier judge",
    "-JPM": "", "-RGA": "", "(JPM)": "", "-JRG": "",
    "The Bank of New York Mellon Trust Company, N.A.": "the trustee", "Bank of New York Mellon": "the trustee",
    "BNY Mellon": "the trustee",
    "XBAW": "the defendant's filter technology", "Tai-Saw": "the defendant's packaging supplier",
    "RFMi": "the defendant's subsidiary", "Grinding and Dicing Services": "the defendant's subsidiary",
}


def aliases(disputes: list[DisputeInstance], borrower: str) -> dict[str, str]:
    """Alias -> role: the instances' parties (and their first word) plus the explicit list."""
    out = dict(EXTRA)
    for d in disputes:
        debtor = d.borrower_role == "debtor"
        mine, theirs = (DEFENDANT, PLAINTIFF) if debtor else (PLAINTIFF, "the defendant")
        for name, role in ((borrower, mine), (d.counterparty, theirs)):
            name = (name or "").strip()
            if len(name) >= 4:
                out.setdefault(name, role)
                first = re.split(r"[ ,]", name)[0]
                if len(first) >= 4:
                    out.setdefault(first, role)
        for f in d.financing:
            if f.issuer and len(f.issuer) >= 4:
                out.setdefault(f.issuer, DEFENDANT)
        ref = re.search(r"\d+:\d{2}-cv-\d+", d.order_reference or "")
        if ref:
            out.setdefault(ref.group(0), "the case")
    return out


class Roles:
    """Case-insensitive, longest-first replacement of every alias by its role, applied to a whole Jev state."""

    def __init__(self, table: dict[str, str]) -> None:
        self.table = {k.lower(): v for k, v in table.items()}
        keys = sorted(self.table, key=len, reverse=True)
        self.rx = re.compile("|".join(re.escape(k) for k in keys), re.IGNORECASE)

    def text(self, s: str) -> str:
        for _ in range(3):  # a role never contains an alias; repeat only for overlaps a replacement exposes
            new = self.rx.sub(lambda m: self.table[m.group(0).lower()], s)
            if new == s:
                break
            s = new
        return s

    def __call__(self, obj):
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, dict):
            return {self(k) if isinstance(k, str) else k: self(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return type(obj)(self(v) for v in obj)
        return obj


def change(a: dict[str, float], b: dict[str, float]) -> float:
    """Largest absolute change in any branch (probability units)."""
    return max(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in set(a) | set(b))


async def ask_roles(fc: Forecaster, judge: ForecastJudge, roles: Roles) -> dict[str, dict[str, float]]:
    """Every forecast node's distribution under role names (the same questions, branches and path facts)."""
    async def one(n):
        st, fids, _ = fc.state(n)
        o = await judge.forecast(n.question_id, roles(st), (n.instance_id, *fids), n.branches)
        return n.key, answer_distribution(n.key, n.branches, o)

    return dict(await asyncio.gather(*(one(n) for n in fc.nodes.values())))


def recall_check(fc: Forecaster, judgments: dict[str, Judgment], judge: ForecastJudge, borrower: str) -> dict:
    """Per node {original, roles, max_change}; summary over the questions. `judgments` is not modified."""
    roles = Roles(aliases(fc.disputes, borrower))
    role_dist = asyncio.run(ask_roles(fc, judge, roles))
    nodes = {}
    for k, j in judgments.items():
        r = role_dist[k]
        nodes[k] = {"original": dict(j.distribution), "roles": r, "max_change": round(change(j.distribution, r), 6)}
    return {"nodes": nodes, "summary": summarize(nodes, judgments)}


def summarize(nodes: dict[str, dict], judgments: dict[str, Judgment]) -> dict:
    ch = [v["max_change"] for v in nodes.values()]
    moved = sorted(((k, v) for k, v in nodes.items() if v["max_change"] > MOVED), key=lambda kv: -kv[1]["max_change"])
    return {"questions": len(ch), "mean_abs_change": round(sum(ch) / len(ch), 6) if ch else 0.0,
            "max_abs_change": max(ch, default=0.0), "threshold": MOVED,
            "moved": [{"key": k, "question_id": judgments[k].question_id, "event": judgments[k].event,
                       "original": v["original"], "roles": v["roles"], "max_change": v["max_change"]}
                      for k, v in moved]}
