"""The 14 May question state (QUESTIONS_20240514 §1, §2; step 9 worker C).

A question's group is the trajectories on which it is asked inside the period before any petition (the node key's
rows, `Forecaster.live`). Its situation states one representative trajectory's joint facts: the medoid of the group
by decision date and available cash (L1 on each coordinate scaled by its range), and beside each figure the group's
range. Values are typed: zero is a value, a state that does not apply or is not available is stated in words, and
cash is never shown negative. The state separates the five kinds the `event_forecast` profile names.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from app.analysis.events import BIG, Awaiting
from app.domain.values import usd


class Unbuilt(RuntimeError):
    """A situation key whose step-9 interface accessor is not on this branch (Chain.C_INTERFACE)."""


def appeal_state(row: dict) -> np.ndarray:
    """Before-answer appeal facts: deadline unknown/open/passed (0/1/2), plus 3 if filed.

    Recorded rows retain the quiet question's classification, so an appeal's own
    answer cannot become a fact conditioning that answer.
    """
    if row.get("appeal_state") is not None:
        return np.asarray(row["appeal_state"], dtype=np.int8)
    day = np.asarray(row["day"])
    deadline = np.asarray((row.get("triggers") or {}).get("appeal_deadline", np.full(day.shape, BIG)))
    filed = np.asarray((row.get("marks") or {}).get("appealed", np.full(day.shape, BIG)))
    known = deadline < BIG
    return (np.where(known, np.where(day > deadline, 2, 1), 0)
            + 3 * ((filed < BIG) & (filed <= day))).astype(np.int8)


def ordinal(k: int) -> str:
    return f"{k}{'th' if 10 <= k % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(k % 10, 'th')}"


def _l1_sums(x: np.ndarray) -> np.ndarray:
    """For each point, the sum of |x_i - x_j| over all j, in O(n log n)."""
    order = np.argsort(x, kind="stable")
    xs = x[order].astype(np.float64)
    n = xs.size
    pre = np.concatenate([[0.0], np.cumsum(xs)])
    k = np.arange(n)
    s = xs * k - pre[:-1] + (pre[-1] - pre[1:]) - xs * (n - 1 - k)
    out = np.empty(n)
    out[order] = s
    return out


def medoid(day: np.ndarray, cash: np.ndarray) -> int:
    """The L1 medoid of the points (day, cash), each coordinate scaled by its range; ties to the earliest point."""
    tot = np.zeros(day.size)
    for a in (day, cash):
        span = float(a.max() - a.min())
        if span > 0:
            tot += _l1_sums(a) / span
    return int(np.argmin(tot))


@dataclass
class Group:
    """A question's pooled trajectories: per row the indices of its live trajectories (`idx`), the members as
    (row, trajectory) pairs in the same order, and the representative's pair."""
    rows: list
    idx: list
    members: np.ndarray  # [(row, trajectory)]
    rep: tuple[int, int]

    @classmethod
    def of(cls, rows: list, masks: list) -> Group | None:
        idx = [np.flatnonzero(m) for m in masks]
        if not sum(x.size for x in idx):
            return None
        pairs = np.concatenate([np.stack([np.full(x.size, i), x], axis=1) for i, x in enumerate(idx)]).astype(np.int64)
        day = np.concatenate([rows[i]["day"][x] for i, x in enumerate(idx)]).astype(np.int64)
        cash = np.maximum(np.concatenate([rows[i]["cash"][x] for i, x in enumerate(idx)]), 0).astype(np.int64)
        k = medoid(day, cash)
        return cls(rows, idx, pairs, (int(pairs[k][0]), int(pairs[k][1])))

    def rowwise(self, fn) -> np.ndarray:
        """fn(row, trajectory indices) -> values, concatenated over the group in member order."""
        return np.concatenate([np.asarray(fn(self.rows[i], x)) for i, x in enumerate(self.idx) if x.size])

    def _src(self, i: int, name: str, sit: bool):
        src = self.rows[i].get("sit") if sit else self.rows[i]
        if src is None:
            raise Unbuilt(f"no question-state snapshot on the rows of this question ({name})")
        v = src[name]
        if isinstance(v, Awaiting):
            raise Unbuilt(v.name)
        return v

    def field(self, name: str, sit: bool = False) -> np.ndarray:
        """A per-trajectory fact over the group (row field, or the snapshot's with `sit`)."""
        def one(i, x):
            v = self._src(i, name, sit)
            return v[x] if isinstance(v, np.ndarray) else np.full(x.size, v)
        return np.concatenate([one(i, x) for i, x in enumerate(self.idx) if x.size])

    def at_rep(self, name: str, sit: bool = False):
        i, j = self.rep
        v = self._src(i, name, sit)
        return v[j] if isinstance(v, np.ndarray) else v


def security_terms(fc, n, row):
    terms = row.get("security_terms")
    if terms is not None:
        return terms
    if n.node != "stay_approved":
        raise Unbuilt("missing approval-day security terms for the stay motion")
    day = row["day"]
    return {"day": day, "cash": row["cash"], "collateral": row["collateral"],
            "need": fc.draws.basis.need[np.arange(len(day)), np.clip(day, 0, fc.days - 1)],
            "offer": row["stay_offer"], "live": (day >= 0) & (day < fc.days)}


def security_kinds(fc, n, row):
    from app.analysis.events import pval
    terms = security_terms(fc, n, row)
    full = (terms["collateral"] > 0) & (terms["cash"] - terms["need"] >= terms["collateral"])
    noncash = pval(fc.m, "stay_security", fc.sens.get("stay_security", False)) == "noncash"
    kinds = np.where(full, "full", np.where(terms["offer"] > 0, "reduced", "noncash" if noncash else "none"))
    return np.where(terms["live"], kinds,
                    np.where(terms["day"] >= fc.days, "unavailable", "inactive"))


def signatures(fc, n, g: Group) -> dict:
    """QUESTIONS §1 Grouping: each member's legal status, available actions and ability to pay, on the dimensions the
    spec names (pay eligibility, stay security type, notes status, listing status), counted over the group. More than
    one signature means the group should split; the walk asks one answer per path for all its trajectories, so a split
    inside one path's trajectories needs per-trajectory answers (reported, not built here). A dimension whose accessor
    is not on the branch counts as '?'."""
    from collections import Counter

    day, cash, owed = g.field("day").astype(np.int64), g.field("cash").astype(np.int64), g.field("owed").astype(np.int64)
    m = len(g.members)
    pay = np.where((owed > 0) & (owed <= cash), "pay", "nopay")
    sec = np.full(m, "-", dtype=object)
    if n.node in ("stay_motion", "stay_approved"):
        sec = g.rowwise(lambda r, x: security_kinds(fc, n, r)[x])

    def sit(name):
        try:
            return g.field(name, sit=True)
        except Unbuilt:
            return None

    due, avail, listing = sit("notes_due_day"), sit("default_available"), sit("listing")
    notes = np.full(m, "?", dtype=object) if due is None or avail is None else np.where(
        due.astype(np.int64) <= day, "due", np.where(avail.astype(np.int64) <= day, "default", "current"))
    lst = np.full(m, "?", dtype=object) if listing is None else listing.astype(str)
    return dict(Counter(zip(pay.tolist(), sec.tolist(), notes.tolist(), lst.tolist(), strict=True)))


def money(rep: int, group: np.ndarray | None = None) -> str:
    """An amount (never below zero) and, where the group differs, its range."""
    text = usd(max(int(rep), 0))
    if group is not None and group.size and int(group.min()) != int(group.max()):
        text += f" (across this situation: {usd(max(int(group.min()), 0))} to {usd(max(int(group.max()), 0))})"
    return text


def when(review: date, t: int) -> str:
    """A day index as a date (day 0 is the day after the review date)."""
    d = review + timedelta(days=int(t) + 1)
    return f"{d.day} {d.strftime('%b %Y')}"


def dated(review: date, rep: int, group: np.ndarray | None = None, none: str = "not applicable") -> str:
    """A date, the group's span beside it; `none` where there is no such day (BIG)."""
    if rep >= BIG:
        return none
    text = when(review, rep)
    if group is not None:
        g = group[group < BIG]
        if g.size and (g.min() != g.max() or g.size < group.size):
            text += f" (across this situation: {when(review, g.min())} to {when(review, g.max())}"
            text += ", or none)" if g.size < group.size else ")"
    return text


STANDING = {"none": "no money judgment has been entered", "unpaid": "unpaid", "stayed": "stayed on approved security",
            "levied_in_part": "unpaid, levied in part", "reduced": "reduced by the post-trial ruling, unpaid",
            "set_aside": "set aside: no money judgment remains", "paid": "paid", "settled": "settled"}
LISTING = {"listed": "listed", "hearing_requested": "listed; a hearing is requested and suspension is stayed until "
           "the Hearings Panel decides", "suspended": "suspended from trading on Nasdaq", "delisted": "delisted"}
DUE_HOW = {"declared": "by declaration of the trustee or the holders",
           "automatic_j": "automatically under §7.02, on a continuing §7.01(j)(v) general-nonpayment default",
           "repurchase": "on the repurchase date, unpaid"}
OCCASION = {"entry": "the day the judgment is entered", "I1": "the day of a levy, before the levy",
            "post": "the day of a levy, before the levy", "ripe": "the day the notes' judgment default becomes "
            "available", "default": "the day the notes' judgment default becomes available"}


class Situation:
    """The contract's situation keys filled for one question: the representative trajectory's joint facts and the
    group's range (Group), or the question's own terms where no cash is stated (verdict questions)."""

    def __init__(self, fc, n, d, g: Group | None, tags: list[str], labels: dict) -> None:
        self.fc, self.n, self.d, self.g, self.tags, self.labels = fc, n, d, g, tags, labels
        self.review = fc.review
        self.fin = next((f for f in d.financing if f.status != "superseded"), None) if d is not None else fc.instrument()
        self.day = int(g.at_rep("day")) if g is not None else None

    def fill(self, keys: list[str], strict: bool = True) -> dict:
        out = {}
        for k in keys:
            try:
                out[k] = getattr(self, k)()
            except Unbuilt as e:
                if strict:
                    raise
                out[k] = f"UNBUILT ({e})"
        return out

    # helpers
    def _g(self):
        if self.g is None:
            raise Unbuilt("no trajectories: the question has no cash state")
        return self.g

    def _sit(self, name):
        g = self._g()
        return int(g.at_rep(name, sit=True)), g.field(name, sit=True).astype(np.int64)

    def _row(self, name):
        g = self._g()
        return int(g.at_rep(name)), g.field(name).astype(np.int64)

    def _entered(self) -> tuple[int, int]:
        entry, _ = self._sit("entry")
        return (entry, int(self._g().at_rep("entered", sit=True))) if entry < BIG else (BIG, 0)

    def _date(self, d) -> str:
        return f"{d.day} {d.strftime('%b %Y')}"

    # the judgment (§2.1)
    def judgment_amount(self):
        """As entered (QUESTIONS §1 Wording): 'a money judgment of $X entered on <date>'; an amount band states its
        band (B's band_range; None or one figure where the amount is exact)."""
        entry, amount = self._entered()
        if entry >= BIG or amount == 0:
            return "none: no money judgment has been entered"
        band = self._g().at_rep("band_range", sit=True)
        if band is not None and band[1] is None:  # the band above the top line (QUESTIONS J1b)
            return f"an amount above {usd(int(band[0]))}"
        if band is not None and int(band[0]) != int(band[1]):
            return f"an amount between {usd(int(band[0]))} and {usd(int(band[1]))}"
        return usd(amount)

    def judgment_entered_on(self):
        rep, grp = self._sit("entry")
        return dated(self.review, rep, grp, none="no judgment has been entered")

    def amount_owed(self):
        return money(*self._row("owed"))

    def judgment_standing(self):
        return STANDING[str(self._g().at_rep("standing", sit=True))]

    def judgment_status(self):
        return "; ".join((self.judgment_standing(), *self.appeal_events(), *self.stay_events()))

    def stay_events(self) -> list[str]:
        statuses = np.unique(self._g().field("stay_status", sit=True))
        if len(statuses) != 1:
            raise Unbuilt("question mixes decision-time stay statuses")
        text = {"not_requested": "", "resolved": "",
                "pending": "the current stay request is awaiting a court decision",
                "denied": "the court denied the latest stay request",
                "approved": "the court approved the latest stay request"}[str(statuses[0])]
        return [text] if text else []

    def appeal_events(self) -> list[str]:
        g = self._g()
        states = g.rowwise(lambda r, x: appeal_state(r)[x])
        deadline = self.n is None or self.fc.uses_appeal_status(self.n)
        if np.unique(states if deadline else states >= 3).size != 1:
            raise Unbuilt("question mixes decision-time appeal statuses")
        state = int(states[0])
        out = []
        if deadline and state % 3:
            prefix = "the deadline to appeal passed on" if state % 3 == 2 else "the deadline to appeal is"
            out.append(f"{prefix} {self.appeal_deadline()}")
        if state >= 3:
            dates = g.rowwise(lambda r, x: np.asarray((r.get("marks") or {}).get(
                "appealed", np.full(len(r["day"]), BIG)))[x])
            if (dates >= BIG).any() or (dates > g.field("day")).any():
                raise Unbuilt("filed appeal has no before-decision date")
            i, j = g.rep
            rep = g.rows[i]["marks"]["appealed"][j]
            out.append(f"the company filed an appeal on {dated(self.review, int(rep), dates)}")
        return out

    def historical_events(self) -> list[str]:
        g = self._g()
        i, j = g.rep
        marks = g.rows[i].get("marks") or {}
        out = []
        for name, text in (("stay_moved", "the company moved for a stay"),
                           ("stayed", "the court approved a stay"),
                           ("stay_denied", "the court denied a stay request"),
                           ("paid", "the company paid the judgment"),
                           ("settled", "the parties agreed to settle; the first settlement payment was due"),
                           ("levied", "the creditor levied on the company's cash"),
                           ("executing", "the creditor sought execution"),
                           ("notes_due", "the notes became due"),
                           ("delisted", "the stock was delisted")):
            dates = marks.get(name)
            if dates is not None and 0 <= int(dates[j]) <= self.day:
                out.append(f"{text} on {when(self.review, int(dates[j]))}")
        return out

    def interval(self):
        if self.d is not None and self.d.stage == "liability_pending" and "I0" not in self.tags:
            return "; ".join((self.post_trial_ruling(), *self.appeal_events()))
        got = next((self.labels[t] for t in self.tags if t in self.labels and t.startswith("I")), None)
        if got is None and ({"final", "appealed"} & set(self.tags)):
            return "; ".join(("after the post-trial ruling", *self.appeal_events()))
        if got is None:
            raise Unbuilt(f"no interval among the context tags {self.tags}")
        return got

    def claimed_amounts(self):
        return [self.fc._component(c, {}) for c in self.d.components]

    def post_trial_ruling(self):
        if self.d is not None and self.d.stage == "liability_pending":
            filed = self._sit("motions_filed")[0]
            deadline = self._sit("motions_deadline")[0]
            if filed > self.day:
                if self.day < deadline:
                    return f"post-trial motions may be filed until {when(self.review, deadline)}"
                return "no timely post-trial motions were filed; the judgment stands as entered"
        ruling = self._sit("ruling")[0]
        if ruling >= BIG:
            return "no post-trial motions were filed: the judgment is final as entered"
        if ruling > self.day:
            return "the post-trial motions are pending"
        return f"the court ruled on the post-trial motions on {when(self.review, ruling)}: the judgment is " \
               f"{self.judgment_standing()}"

    def appeal_deadline(self):
        # The walk clips triggers outside its horizon. A known ruling still
        # establishes the notice deadline even when that deadline is later.
        g = self._g()
        def dates(r):
            out = np.asarray((r.get("triggers") or {}).get("appeal_deadline",
                             np.full(len(r["day"]), BIG))).copy()
            if self.d is not None and self.d.stage == "liability_pending":
                ruling = np.asarray(r["sit"]["ruling"])
                known = (ruling >= 0) & (ruling < BIG) & (ruling <= r["day"])
                out = np.where((out >= BIG) & known,
                               ruling + int(self.fc.m["rules"]["frap_4a1a"]["value"]), out)
            return out
        vals = g.rowwise(lambda r, x: dates(r)[x])
        i, j = g.rep
        return dated(self.review, int(dates(g.rows[i])[j]), vals)

    def _trigger(self, name: str, none: str = "not applicable") -> str:
        g = self._g()
        get = lambda r: (r.get("triggers") or {}).get(name, np.full(len(r["day"]), BIG))  # noqa: E731
        vals = g.rowwise(lambda r, x: get(r)[x]).astype(np.int64)
        i, j = g.rep
        return dated(self.review, int(get(g.rows[i])[j]), vals, none=none)

    # the company's cash (§2.2)
    def decision_date(self):
        return dated(self.review, *self._row("day"))

    def available_cash(self):
        return money(*self._row("cash"))

    def projected_cash_after_payments(self):
        return money(*self._sit("cash_end"))

    def cash_before_scheduled_payments(self):
        return self.available_cash()

    def _need(self):
        g, need = self._g(), self.fc.draws.basis.need
        vals = g.rowwise(lambda r, x: need[x, np.minimum(r["day"][x], need.shape[1] - 1)])
        i, j = g.rep
        return int(need[j, min(int(g.rows[i]["day"][j]), need.shape[1] - 1)]), vals

    def operating_need_30_days(self):
        return money(*self._need())

    def arrears(self):
        g = self._g()
        by = g.at_rep("arrears", sit=True)
        if isinstance(by, Awaiting):
            raise Unbuilt(by.name)
        i, j = g.rep
        rep = {k: int(v[j]) for k, v in by.items()}
        due = g.rows[i]["sit"].get("notes_due_day")
        notes = (f"; notes principal of {self.notes_principal()} is due and unpaid since "
                 f"{when(self.review, int(due[j]))}" if due is not None and int(due[j]) <= self.day else "")
        if not any(rep.values()):
            return "no operating expenses, loan payments, notes interest, settlement payments or judgment " \
                   "payments are overdue" + notes
        first, _ = self._sit("first_unpaid")
        out = {k.replace("_", " "): usd(v) for k, v in rep.items() if v}
        out["first recorded nonpayment"] = when(self.review, first) if first <= self.day else "not applicable"
        if notes:
            out["notes principal"] = notes.removeprefix("; ")
        return out

    def unpaid_obligation(self):
        return self.arrears()

    def days_since_first_nonpayment(self):
        first, grp = self._sit("first_unpaid")
        days = self._g().field("day").astype(np.int64)
        valid = (grp >= 0) & (grp < BIG) & (grp <= days)
        elapsed = days[valid] - grp[valid]
        rep = f"{self.day - first} days" if 0 <= first < BIG and first <= self.day else "no recorded nonpayment"
        if elapsed.size and (not valid.all() or elapsed.min() != elapsed.max()):
            rep += f" (across this situation: {int(elapsed.min())} to {int(elapsed.max())} days"
            rep += "; some trajectories have no recorded nonpayment)" if not valid.all() else ")"
        return rep

    # stay security (§2.5)
    def _security(self) -> tuple[str, int]:
        g, i = self._g(), self._g().rep[0]
        j = g.rep[1]
        terms = security_terms(self.fc, self.n, g.rows[i])
        kind = str(security_kinds(self.fc, self.n, g.rows[i])[j])
        return kind, int(terms["collateral" if kind == "full" else "offer"][j])

    def collateral_required(self):
        if self.n.node not in ("stay_motion", "stay_approved"):
            return money(*self._row("collateral"))
        g = self._g()
        i, j = g.rep
        terms = security_terms(self.fc, self.n, g.rows[i])
        if not terms["live"][j]:
            return "not available" if int(terms["day"][j]) >= self.fc.days else "not applicable: the stay does not take effect"
        vals = g.rowwise(lambda r, x: security_terms(self.fc, self.n, r)["collateral"][x])
        return money(int(terms["collateral"][j]), vals)

    def bond_required(self):
        if self.n.node in ("stay_motion", "stay_approved"):
            if self._security()[0] in ("inactive", "unavailable"):
                return self.collateral_required()
            g = self._g()
            i, j = g.rep
            vals = g.rowwise(lambda r, x: security_terms(self.fc, self.n, r)["collateral"][x])
            rep = int(security_terms(self.fc, self.n, g.rows[i])["collateral"][j])
        else:
            rep, vals = self._row("collateral")
        share = int(self.fc.m["parameters"]["bond_collateral_share_bps"]["value"])
        return money(rep * 10_000 // share, vals * 10_000 // share)

    def security_offered(self):
        kind, amount = self._security()
        g = self._g()
        i, j = g.rep
        day = int(security_terms(self.fc, self.n, g.rows[i])["day"][j])
        if kind == "unavailable":
            return "security amount unavailable"
        if kind == "inactive":
            return f"no security takes effect on {dated(self.review, day)}"
        return {"full": f"full bond collateral of {usd(amount)} in cash",
                "reduced": f"reduced cash security of {usd(amount)}, its cash above its operating need for the next "
                           f"month on {when(self.review, day)}, when the court decides",
                "noncash": "security not in cash, or a waiver of security",
                "none": f"no cash security: no cash remains above operating need on {dated(self.review, day)}"}[kind]

    def stay_security_required(self):
        from app.analysis.events import bond_collateral_cents, judgment_bps
        rep, vals = self._row("owed")
        required = bond_collateral_cents(np.r_[rep, vals], judgment_bps(self.fc.m, self.d, self.fc.sens),
                                        self.fc.m, self.fc.setup, self.fc.sens)
        return f"bond collateral of {money(int(required[0]), required[1:])} on the decision date"

    def motion_date(self):
        return self.decision_date()

    def enforcement_dated(self):
        g = self._g()
        i, j = g.rep
        marks = g.rows[i].get("marks") or {}
        out = []
        for name, text in (("executing", "{claimant} initiated enforcement on {d}"),
                           ("levied", "a levy on the company's cash on {d}")):
            if name in marks and int(marks[name][j]) <= self.day:
                out.append(text.format(claimant=self.d.counterparty, d=when(self.review, int(marks[name][j]))))
        return "; ".join(out) or "none: no enforcement has been initiated"

    # settlement (D3, C2)
    def settlement_amount(self):
        return money(*self._row("settle_offer"))

    def installments(self):
        from app.analysis.events import settlement_terms
        mode, count = settlement_terms(self.fc.m, self.fc.sens)
        if mode != "installments":
            return "one payment of the full amount on the settlement date"
        return str(count)

    def settlement_date(self):
        if self.g is not None and "settlement_pricing" in self.g.rows[self.g.rep[0]]["sit"]:
            g = self._g()
            dates = g.rowwise(lambda r, x: r["sit"]["settlement_pricing"]["day"][x])
            i, j = g.rep
            return dated(self.review, int(g.rows[i]["sit"]["settlement_pricing"]["day"][j]), dates)
        p = self.fc.m["parameters"]["settlement_date_in_interval"]
        if self.fc.sens.get("settlement_date_in_interval"):
            return "the end of the current stage of the dispute"
        rep, grp = self._row("day")
        k = int(p["value"])
        return dated(self.review, rep + k, grp + k)

    def settlement_pricing_cash(self):
        return self._pricing_money("cash")

    def settlement_pricing_operating_need(self):
        return self._pricing_money("operating_need")

    def _pricing_money(self, name):
        g = self._g()
        vals = g.rowwise(lambda r, x: r["sit"]["settlement_pricing"][name][x])
        i, j = g.rep
        return money(int(g.rows[i]["sit"]["settlement_pricing"][name][j]), vals)

    # the notes (§2.3, §3)
    def notes_principal(self):
        return usd(self.fin.principal_cents) if self.fin is not None else "not applicable: no notes"

    notes_balance_due = notes_principal

    def days_unpaid_and_unstayed(self):
        entry, _ = self._sit("entry")
        if entry >= BIG:
            return "not applicable: no judgment has been entered"
        stayed = self._sit("stayed_from")[0]
        start = entry + 30  # Fed. R. Civ. P. 62(a): execution is stayed for the first 30 days after entry
        if stayed <= self.day:
            return "none: the judgment is stayed on approved security"
        return f"{max(self.day - start, 0)} days since the automatic stay ended on {when(self.review, start)}"

    def default_available_on(self):
        return dated(self.review, *self._sit("default_available"), none="not available on this date")

    def declaration_deadline(self):
        return dated(self.review, *self._sit("declaration_deadline"))

    def notes_due_how(self):
        how = str(self._g().at_rep("notes_due_how", sit=True))
        return DUE_HOW.get(how, "not due")

    def notes_status(self):
        due, _ = self._sit("notes_due_day")
        if due <= self.day:
            return f"due and unpaid since {when(self.review, due)}, {self.notes_due_how()}"
        out = []
        avail, _ = self._sit("default_available")
        if avail <= self.day:
            out.append(f"the §7.01(i) judgment default is available since {when(self.review, avail)}")
        delisted, _ = self._sit("delisted")
        if delisted <= self.day:
            out.append(f"the §7.01(b) delisting default is continuing since {when(self.review, delisted)}")
        np_day, _ = self._sit("nonpayment_day")
        if np_day <= self.day:
            out.append(f"the §7.01(j)(v) general-nonpayment default is continuing since {when(self.review, np_day)}")
        return "; ".join(out) or "not due; no Event of Default is continuing"

    def petition_route(self):
        rep, grp = self._sit("route_days")
        text = ("at once: a §7.01(j) default is continuing, which §7.06 does not bar" if rep == 0 else
                f"{rep} days after the holders' written request to the trustee, made at acceleration (§7.06)")
        return text + ("" if grp.min() == grp.max() else " (on part of this situation the other of these holds)")

    def repurchase_date(self):
        return self._trigger("repurchase_due", none="none: no Fundamental Change has occurred")

    def delisted_on(self):
        return dated(self.review, *self._sit("delisted"), none="not delisted")

    # the listing (§2.4)
    def listing_status(self):
        return LISTING[str(self._g().at_rep("listing", sit=True))]

    def _listing(self) -> dict:
        from app.disputes.forecast import _listing_dates
        return _listing_dates(self.fc, self.d)

    def compliance_deadline(self):
        return self._date(self.fin.listing_deadline) + " (the end of the second compliance period)"

    listing_deadline = compliance_deadline

    def bid_price_on_review_date(self):
        return self.labels.get("bid_price_on_review_date") or "not stated"

    def reverse_split_time(self):
        x = self._listing()
        return ("a reverse split needs stockholder approval of a charter amendment (DGCL §242), called and noticed "
                f"under the bylaws and the proxy rules; to regain compliance by the deadline it takes effect by "
                f"{when(self.review, x['effective_by'])}, with the meeting called by {when(self.review, x['vote_call'])}")

    def determination_date(self):
        return when(self.review, self._listing()["determination"])

    def hearing_request_deadline(self):
        return when(self.review, self._listing()["hearing_request"])

    # equity (§2.6)
    def share_price(self):
        """'the company's share price on <date>: $X' on the decision day, with the group's range."""
        g = self._g()
        rep, grp = float(g.at_rep("share_price", sit=True)), np.asarray(g.field("share_price", sit=True), dtype=float)
        text = f"the company's share price on {when(self.review, self.day)}: ${rep / 100:,.4f}"
        lo, hi = round(float(grp.min()) / 100, 4), round(float(grp.max()) / 100, 4)
        return text + ("" if lo == hi else f" (across this situation: ${lo:,.4f} to ${hi:,.4f})")

    def atm_proceeds_to_date(self):
        return money(*self._sit("atm"))

    def share_capacity_left(self):
        rep, grp = self._sit("ledger")
        return f"{rep:,} shares" + ("" if grp.min() == grp.max() else
                                    f" (across this situation: {int(grp.min()):,} to {int(grp.max()):,})")

    def _terms(self) -> dict:
        t = self._g().at_rep("offering_terms", sit=True)
        if isinstance(t, Awaiting):
            raise Unbuilt(t.name)
        j = self._g().rep[1]  # worker A: each term per trajectory [n], at the capacity left on the day
        return {k: int(np.asarray(v)[j]) if np.ndim(v) else int(v) for k, v in t.items()}

    def offering_terms(self):
        t = self._terms()
        return (f"an underwritten public offering of common stock on the company's shelf: {t['shares']:,} shares at "
                f"${t['price_cents_x1e4'] / 1e6:,.4f}, gross proceeds {usd(t['gross'])}, issuance costs "
                f"{usd(t['costs'])}, net proceeds {usd(t['net'])}, closing {t['close_days']} days after it is "
                f"initiated" + ("; " + self.offering_lockup() if self._lockup() else ""))

    def _lockup(self) -> dict | None:
        lock = self.fc.m["parameters"].get("offering_lockup")
        return None if not lock or lock.get("atm_carved_out") else lock

    def offering_lockup(self):
        """The underwriting agreement's lock-up (case parameter offering_lockup) and the at-the-market proceeds it
        forgoes (`atm_lockup_proceeds`)."""
        lock = self._lockup()
        if lock is None:
            return "none"
        g = self._g()
        rep = self.atm_lockup_proceeds(np.array([g.at_rep("day")]), np.array([g.at_rep("share_price", sit=True)]),
                                       np.array([g.at_rep("ledger", sit=True)]))[0]
        grp = self.atm_lockup_proceeds(g.field("day"), g.field("share_price", sit=True), g.field("ledger", sit=True))
        return (f"the company agrees not to sell shares, including at-the-market sales, from pricing until the "
                f"{ordinal(int(lock['value']))} day after the closing; at its current pace and share price, "
                f"at-the-market sales over that period would bring in {money(rep, grp)}")

    def atm_lockup_proceeds(self, day, price, ledger) -> np.ndarray:
        """Per trajectory [k]: the net proceeds of the at-the-market sales the engine's schedule makes over the whole
        lock-up of an offering initiated on the decision day (events.Chain._lockup: sale days from the initiation +
        pricing_days through the 90th day after the scheduled close, not cut at the horizon), each day's shares
        (Chain._atm_schedule) at the decision day's share price, net of commission as the engine books a sale
        (rint(shares x price), less commission, rounded down), within the share ledger left that day. The whole
        window's sales settle, so the settlement rule changes when they are received, not what they bring in."""
        from app.analysis.events import Chain, trading_day

        fc, lock = self.fc, self._lockup()
        memo = fc.__dict__.get("_atm_lockup")
        if memo is None:
            ch = Chain(None, fc.setup, fc.m, fc.draws, fc.sens, fin=fc.instrument())
            _, _, q, _ = ch._atm_schedule()
            p = fc.m["parameters"]
            first = ch.ix(date.fromisoformat(p["atm_pace_bps"]["first_sale"]))
            span = fc.days + int(p["offering_price"]["close_days"]) + int(lock["value"]) + 400
            trade = np.array([trading_day(fc.review + timedelta(days=t + 1)) and t >= first for t in range(span)])
            memo = fc._atm_lockup = (q, int(p["atm_pace_bps"]["commission_bps"]), np.concatenate([[0], np.cumsum(trade)]),
                                     int(p["offering_price"]["close_days"]))
        q, comm, cum, close_days = memo
        day = np.asarray(day, dtype=np.int64)
        lo, hi = day + int(lock["pricing_days"]), day + close_days + int(lock["value"])  # sale days lo..hi
        days = cum[hi + 1] - cum[lo]
        sold = np.minimum(days, np.asarray(ledger, dtype=np.int64) // q)
        net = np.rint(q * np.asarray(price, dtype=float)).astype(np.int64) * (10_000 - comm) // 10_000
        return sold * net

    def gross_proceeds(self):
        return usd(self._terms()["gross"])

    def issuance_costs(self):
        return usd(self._terms()["costs"])

    def net_proceeds(self):
        return usd(self._terms()["net"])

    def offer_price(self):
        return f"${self._terms()['price_cents_x1e4'] / 1e6:,.4f} per share"

    def shares_offered(self):
        return f"{self._terms()['shares']:,} shares"

    def offering_unavailable_reason(self):
        listing = str(self._g().at_rep("listing", sit=True))
        i, j = self._g().rep
        pet = int(self._g().rows[i]["petition"][j])
        if listing == "delisted":
            return "unavailable: the stock is delisted"
        if 0 <= pet <= self.day:
            return "unavailable: a petition has been filed"
        if bool(self._g().at_rep("offering_pending", sit=True)):
            return "unavailable: another offering is pending"
        if int(self._g().at_rep("ledger", sit=True)) <= 0:
            return "unavailable: no share capacity is left"
        try:  # the case's initiation rule (QUESTIONS §2.6): the proceeds against the shortfall they must cover
            net, short = (int(self._g().at_rep(k, sit=True)) for k in ("offer_available", "offer_shortfall"))
        except KeyError:  # a record without the rule's figures: the offering is among the answers
            net, short = 1, 0
        if net <= 0 and short > 0:
            return (f"unavailable: an offering on the stated terms would raise {usd(self._terms()['net'])} net, short "
                    f"of the {usd(short)} by which the company's available cash at the end of the day falls below "
                    f"its operating need for the next month")
        return "not applicable: an offering is available"

    def _offerings(self):
        offs = self._g().at_rep("offerings", sit=True)
        if isinstance(offs, Awaiting):
            raise Unbuilt(offs.name)
        j = self._g().rep[1]
        return [(int(np.asarray(a)[j] if np.ndim(a) else a), int(np.asarray(c)[j] if np.ndim(c) else c),
                 bool(np.asarray(ok)[j] if np.ndim(ok) else ok)) for a, c, ok in offs]

    def initiated_on(self):
        return self.decision_date()

    def close_date(self):
        k = int(self._terms()["close_days"])
        return dated(self.review, self.day + k)

    def earlier_attempts(self):
        prior = [(a, c, ok) for a, c, ok in self._offerings() if a < self.day]
        return [f"initiated {when(self.review, a)}: " + (f"closed {when(self.review, c)}" if ok else
                                                           "did not close") for a, c, ok in prior] or "none"

    # the company's decisions
    def occasion(self):
        return next((OCCASION[t] for t in self.tags if t in OCCASION), "not stated")

    def floor_decision(self):
        return "the company did not file a petition when its available cash fell below its operating need for the " \
               "next month"

    # the post-trial questions
    def motion_deadline(self):
        entry, _ = self._sit("entry")
        return when(self.review, entry + 28) + " (28 days after entry; Fed. R. Civ. P. 50(b), 59(b))"

    def pending_motions(self):
        return f"the company's timely motions under Fed. R. Civ. P. 50(b) and 59, filed by {self.motion_deadline()}"

    def grounds_preserved(self):
        return "the grounds the company raised in its Rule 50(a) motions at trial"

    def reduced_low(self):
        return usd(self._band()[0])

    def reduced_high(self):
        return usd(self._band()[1])

    def _band(self):
        band = self.fc.reduced_band(self.d, self._entered()[1])
        if band is None:
            raise Unbuilt("this award has no lower positive judgment band")
        return band[:2]

    def verdict_amount(self):
        return self.judgment_amount()

    def remitted_amount(self):
        key = self.n.key.split("|#", 1)[0]
        terms = self.fc.remitted.get(key)
        if terms is None:
            raise Unbuilt(f"no proposed remittitur terms for {key}")
        return usd(int(terms[0]))

    # the verdict form (no cash: the form's words and the earlier answers)
    def _form(self) -> dict:
        return self.fc.verdict_context(self.n)

    def form_item(self):
        return self._form()["form_question"].split(":")[0]

    def form_question(self):
        return self._form()["form_question"]

    def answer_rule(self):
        return self._form().get("asked", "the form's own instruction")

    def earlier_answers(self):
        return self._form()["earlier_answers"] or "none: this is the form's first question"

    def _ask(self) -> dict:
        """The amount question's J1b terms (worker B's `Forecaster.verdict_asks`): its item, the threshold the item's
        amount is asked against, and the total judgment the earlier answers establish."""
        a = getattr(self.fc, "verdict_asks", {}).get(self.n.key)
        if a is None:
            raise Unbuilt(f"no J1b terms for {self.n.key} (Forecaster.verdict_classes not built)")
        return a

    def amount_established(self):
        return usd(int(self._ask()["established"]))

    def threshold(self):
        return usd(int(self._ask()["threshold"]))


# the events a question's context tags assume on the path, in the contracts' wording (QUESTIONS §1 Wording: the
# judgment as entered, no class names); the 20 Jun phrases (forecast.STATE_PHRASES) are untouched
PHRASES = {"stay_pending": "the company has moved for a stay, not yet decided",
           "levied": "{claimant} has levied on the company's cash", "unlevied": "no levy on the company's cash so far",
           "motions_pending": "the post-trial motions are pending",
           "executing": "{claimant} initiated enforcement before the post-trial ruling",
           "stay_moved": "the company has moved for a stay", "stayed": "the judgment is stayed on approved security",
           "paid": "the company has paid the judgment",
           "notes_due": "the notes are due and unpaid, and no petition has been filed",
           "delisted": "the stock has been delisted",
           "cash_exhausted": "the company did not file when its available cash fell below its operating need for "
                             "the next month",
           "delisted_suspension": "the stock was delisted on suspension, with no hearing",
           "delisted_panel": "the stock was delisted on the Hearings Panel's decision",
           "entered_not_acted": "the holders did not declare the notes due when the judgment default became "
                                "available",
           "after_none": "the company did not pay, initiate an offering or file when it last responded to the judgment",
           "after_offer": "the company initiated an underwritten offering when it last responded to the judgment",
           "after_mixed": "the company did not pay or file when it last responded to the judgment",
           "after_failed": "an earlier underwritten offering on the path did not close",
           "ruling": "the post-trial ruling changed the judgment"}
# tags that name a class, an option set or a retired state: the situation states what they stood for
UNSTATED = {"entered", "pay", "nopay", "raise", "noraise", "after_seek", "seeking", "raised", "claimant_theory",
            "final", "appealed",  # routing tags; dated appeal facts are rendered from the question's rows
            "without_principal_measure",
            # the distress chain's question identities (worker A): the situation states the date, the offering
            # available or why not, and the notes' route
            "deadline", "determination", "offer", "nooffer", "nonpayment", "cash_out"}
IDENTITY = re.compile(r"floor\d+|award.*|reduced.*|remit.*")  # D7's k-th fall; a verdict, ruling or remittitur class
# (the situation states each: the judgment as entered, as the ruling left it, the remitted amount)


def assumed_events(tags: list[str], labels: dict, claimant: str, strict: bool = True) -> list[str]:
    out = []
    for t in tags:
        if t in UNSTATED or IDENTITY.fullmatch(t) or t.startswith(("amt", "beyond")) or t.endswith("_retrial"):
            continue
        if t in labels:
            out.append(labels[t])
        elif t in PHRASES:
            out.append(PHRASES[t].format(claimant=claimant))
        elif t.startswith("judgment_"):
            out.append("the notes were declared due on the judgment default")
        elif t.startswith("delisting_"):
            out.append("the notes are due after the delisting default")
        elif strict:
            raise ValueError(f"No 14 May phrase for decision context {t!r}")
        else:
            out.append(f"UNPHRASED ({t})")
    return out

# --- the state ----------------------------------------------------------------------------------------------------

KIND_OF = {"court ruling": "court_findings", "party argument": "party_assertions",
           "statement of intended proof": "party_assertions", "proposal": "party_assertions",
           "company disclosure": "historical_evidence", "third-party record": "historical_evidence",
           "stipulation": "court_findings"}  # the parties' stipulated instructions, given to the jury by the court
CLOSED = {"verdict_finding": "the jury", "verdict_amount": "the jury", "post_trial_ruling": "the court",
          "stay_approved": "the court", "registration_early": "the court"}


def attribution(fc, fid: str) -> dict:
    """A finding's author, date and status: the source's (the case's `source_attribution`), with the author replaced
    by the finding's `speaker` where the agent recorded one (the party whose statement the passage is, where it is not
    the document's author; propose_finding on step-9-contracts)."""
    f = fc.findings[fid]
    src = f.spans[0].source_id
    a = fc.m.get("case_sources", {}).get(src)
    if a is None:
        raise ValueError(f"No attribution for source {src!r} (scenario.json source_attribution)")
    speaker = getattr(f, "speaker", None)
    return {"source_id": src, **a, **({"author": speaker, "document_author": a["author"]} if speaker else {})}


def evidence_kinds(fc, n, node_texts: dict, d) -> tuple[dict, list, tuple]:
    """The findings that fill the question's record items, attributed and sorted into the profile's kinds; the
    items no finding fills are left out."""
    items = node_texts["record_items"]
    supplies: dict[str, list[str]] = {}
    for item in items:
        for fid in fc.slots.get(n.node, {}).get(item, []):
            if fid in fc.findings:
                supplies.setdefault(fid, []).append(item)
    kinds = {"historical_evidence": [], "party_assertions": [], "court_findings": []}
    closed = CLOSED.get(n.node)
    for fid, its in supplies.items():
        a = attribution(fc, fid)
        p = fc.hydrate(fc.findings[fid])
        item = {"author": a["author"], "date": p.get("date", ""), "status": a["status"], "source": p.get("source", ""),
                "quotes": p.get("quotes", []), "context": p.get("context", ""), "supplies": its}
        if closed and not a.get("in_case_record"):
            item["before_the_decider"] = f"no: not part of the record {closed} decides on"
        kinds[KIND_OF[a["status"]]].append(item)
    filled = [x for x in items if any(x in its for its in supplies.values())]
    return kinds, filled, tuple(supplies)


def fill_text(text: str, values: dict) -> str:
    """The registry's question text with its placeholders filled (case inputs and situation values)."""
    import re

    def one(m):
        v = values.get(m.group(1))
        if v is None:
            raise ValueError(f"No value for placeholder {{{m.group(1)}}}")
        return v if isinstance(v, str) else json.dumps(v)

    return re.sub(r"{(\w+)}", one, text)


def question_text(n, text: str, values: dict) -> str:
    """The question names only actions in its recorded answer set."""
    if n.node in ("judgment_response", "financing_at_floor", "petition_cash_out"):
        choices = {"pay": "pay the judgment balance of {amount_owed} in full",
                   "initiate_offering": "initiate an underwritten offering of its common stock on the stated terms",
                   "file": "file a voluntary petition", "none": "none of these", "neither": "neither"}
        actions = [choices[b] for b in choices if b in n.branches]
        if set(n.branches) == {"file", "neither"}:
            actions[-1] = "continue without filing"
        options = ", ".join(actions[:-1]) + ", or " + actions[-1]
        if n.node == "judgment_response":
            text = "What does {company} do on {decision_date}, {occasion}: " + options + "?"
        else:
            text = text.split("does {company}", 1)[0] + "does {company} " + options + "?"
    return fill_text(text, values)


def eligible(fc, n, rows: list, masks: list) -> list:
    """QUESTIONS §4.2-4.3 eligibility inside the trajectories a node is asked on: a settlement question covers only
    trajectories whose offer is positive (D3, C2); a stay question only those where security of some type is
    available (D4, J3: full collateral, positive reduced cash security, or the non-cash scenario). Where none is
    eligible the masks stay as they are (the walk asked it; the state then shows what holds)."""
    from app.analysis.events import pval

    if n.node in ("settlement_offer", "settlement_accept"):
        out = [m & (r["settle_offer"] > 0) if r.get("settle_offer") is not None else m & False
               for r, m in zip(rows, masks, strict=True)]
    elif n.node in ("stay_motion", "stay_approved"):
        if pval(fc.m, "stay_security", fc.sens.get("stay_security", False)) == "noncash":
            return masks
        out = []
        for r, m in zip(rows, masks, strict=True):
            out.append(m & np.isin(security_kinds(fc, n, r), ("full", "reduced")))
    else:
        return masks
    return out if any(x.any() for x in out) else masks


RANGE = re.compile(r" \(across this situation: [^)]*\)")


def cite(fc, t: dict) -> list[str]:
    terms = {k: v for x in fc.m["templates"].values() for k, v in x.get("terms_from_instrument", {}).items()}
    return [fc.m["rules"][r]["citation"] if r in fc.m["rules"] else terms.get(r, r) for r in t["standard"]]


def build(fc, n, d, tags: list[str], rows: list, masks: list, strict: bool = True) -> tuple[dict, tuple, dict]:
    """A 14 May question's state (QUESTIONS §1): the case, the question as asked (its text and answers with the
    situation's values), the standard, the record items the evidence fills, the situation, and the evidence sorted
    into historical evidence, party assertions and court findings, the events assumed on the path, and the earlier
    readings. `strict` False renders an unbuilt interface value as UNBUILT instead of raising (STATES.md)."""
    from app.disputes.forecast import PENDING, fmt, registry_entry

    t = fc.texts(n.node, d)
    entry = registry_entry(n.question_id)
    labels = {**fc.m.get("case_labels", {}), **(fc.labels(d) if d.stage == PENDING else {})}
    g = Group.of(rows, eligible(fc, n, rows, masks)) if rows else None
    if rows and any('note_context' in row for row in rows):
        if not all('note_context' in row for row in rows):
            raise Unbuilt('mixed prefix and complete-path notes facts')
        if g is not None:
            contexts = set(g.field('note_context'))
            if len(contexts) != 1:
                raise Unbuilt('notes class mixes decision-time contexts')
            tags = str(g.at_rep('note_context')).split('|')
    keys = t["situation_keys"]
    if n.node == "post_trial_ruling" and "reduced" not in n.branches:
        keys = [k for k in keys if k not in ("reduced_low", "reduced_high")]
    situation = Situation(fc, n, d, g, tags, labels)
    sit = situation.fill(keys, strict)
    values = {"company": fc.borrower, "claimant": d.counterparty,  # the question names the representative's figures
              **{k: RANGE.sub("", v) if isinstance(v, str) else json.dumps(v) for k, v in sit.items()}}
    crit = entry["prompt"]["criteria"]
    names = dict(zip(["true", "false"], t["branches"], strict=True)) if entry["primitive"] == "noul" else \
        {k: k for k in crit}
    answers = {names[k]: fill_text(v, values) for k, v in crit.items() if names[k] in n.branches}
    kinds, filled, fids = evidence_kinds(fc, n, t, d)
    readings, _ = fc._readings(d, n.question_id)
    verdict = n.node in ("verdict_finding", "verdict_amount")
    # Routing tags remain part of identity. Current standing and dated actions
    # come from the question's engine snapshot, not a second account of status.
    dated_tags = {"Ientry", "I1", "I2", "I3", "I4", "post", "ripe", "motions_pending", "motions_open",
                  "stay_pending", "stay_moved", "stay_denied", "stay_approved", "stay_resolved",
                  "stayed", "paid", "settled", "notes_due", "delisted", "levied", "unlevied", "executing"}
    history_tags = [tag for tag in tags if g is None or
                    (tag not in dated_tags and not tag.startswith(("judgment_", "delisting_")))]
    assumed = [*([] if verdict else assumed_events(history_tags, labels, d.counterparty, strict)), *n.assumptions]
    if not verdict and g is not None:
        assumed.extend(situation.historical_events())
    if not verdict and ({"final", "appealed"} & set(tags)):
        assumed.extend(situation.appeal_events())
    state = {"case": {"evidence_cutoff": fmt(fc.review), "company": fc.borrower, "counterparty": d.counterparty,
                      "obligation": f"{fc.m['natures'].get(d.nature, d.nature)}, {d.order_reference}"},
             "question": {"actor": fill_text(t["actor"], values), "text": question_text(n,
                                                                    entry["prompt"]["instructions"], values),
                          "answers": answers},
             "standard": cite(fc, t), "record_items": filled, "situation": sit, **kinds,
             "assumed_events": assumed, "earlier_readings": readings}
    return state, fids, readings
