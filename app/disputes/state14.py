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


def signatures(fc, n, g: Group) -> dict:
    """QUESTIONS §1 Grouping: each member's legal status, available actions and ability to pay, on the dimensions the
    spec names (pay eligibility, stay security type, notes status, listing status), counted over the group. More than
    one signature means the group should split; the walk asks one answer per path for all its trajectories, so a split
    inside one path's trajectories needs per-trajectory answers (reported, not built here). A dimension whose accessor
    is not on the branch counts as '?'."""
    from collections import Counter

    from app.analysis.events import pval

    day, cash, owed = g.field("day").astype(np.int64), g.field("cash").astype(np.int64), g.field("owed").astype(np.int64)
    m = len(g.members)
    pay = np.where((owed > 0) & (owed <= cash), "pay", "nopay")
    sec = np.full(m, "-", dtype=object)
    if n.node in ("stay_motion", "stay_approved"):
        need = g.rowwise(lambda r, x: fc.draws.basis.need[x, np.minimum(r["day"][x], fc.draws.basis.need.shape[1] - 1)])
        coll = g.field("collateral").astype(np.int64)
        offer = g.rowwise(lambda r, x: r["stay_offer"][x] if r.get("stay_offer") is not None else np.zeros(x.size))
        noncash = pval(fc.m, "stay_security", fc.sens.get("stay_security", False)) == "noncash"
        sec = np.where((coll > 0) & (cash - need >= coll), "full", np.where(offer > 0, "reduced",
                                                                            "noncash" if noncash else "none"))

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
STATUS = {"I1": "the post-trial motions are pending", "I2": "the post-trial motions are decided, or the time for them "
          "has run; the time to appeal is running", "I3": "the time to appeal has expired",
          "I4": "the judgment is stayed on approved security"}
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
        status = next((STATUS[t] for t in self.tags if t in STATUS), None)
        ruled = self._sit("ruling")[0] <= self.day
        if status is None:
            status = STATUS["I2"] if ruled else STATUS["I1"]
        return f"{self.judgment_standing()}; {status}"

    def interval(self):
        got = next((self.labels[t] for t in self.tags if t in self.labels and t.startswith("I")), None)
        if got is None and "final" in self.tags:
            return "after the time to appeal has expired"
        if got is None:
            raise Unbuilt(f"no interval among the context tags {self.tags}")
        return got

    def claimed_amounts(self):
        return [self.fc._component(c, {}) for c in self.d.components]

    def post_trial_ruling(self):
        ruling = self._sit("ruling")[0]
        if ruling >= BIG:
            return "no post-trial motions were filed: the judgment is final as entered"
        if ruling > self.day:
            return "the post-trial motions are pending"
        return f"the court ruled on the post-trial motions on {when(self.review, ruling)}: the judgment is " \
               f"{self.judgment_standing()}"

    def appeal_deadline(self):
        return self._trigger("appeal_deadline")

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
        if not any(rep.values()):
            return "none: every obligation that has fallen due has been paid"
        first, _ = self._sit("first_unpaid")
        out = {k.replace("_", " "): usd(v) for k, v in rep.items() if v}
        out["unpaid since"] = when(self.review, first) if first < BIG else "not applicable"
        return out

    def unpaid_obligation(self):
        return self.arrears()

    def days_of_continuous_arrears(self):
        first, grp = self._sit("first_unpaid")
        if first >= BIG or first > self.day:
            return "none: no obligation is unpaid"
        days = self._g().field("day").astype(np.int64)
        return f"{self.day - first} days" + ("" if (days - grp).min() == (days - grp).max() else
                                             f" (across this situation: {int((days - grp).min())} to "
                                             f"{int((days - grp).max())} days)")

    # stay security (§2.5)
    def _security(self) -> tuple[str, int]:
        g, i = self._g(), self._g().rep[0]
        j = g.rep[1]
        offer = int((g.rows[i].get("stay_offer") if g.rows[i].get("stay_offer") is not None
                     else np.zeros(len(g.rows[i]["day"]), dtype=np.int64))[j])
        coll = int(g.rows[i]["collateral"][j])
        cash, need = int(g.rows[i]["cash"][j]), self._need()[0]
        if coll > 0 and cash - need >= coll:
            return "full", coll
        if offer > 0:
            return "reduced", offer
        from app.analysis.events import pval
        return ("noncash", 0) if pval(self.fc.m, "stay_security", self.fc.sens.get("stay_security", False)) == \
            "noncash" else ("none", 0)

    def collateral_required(self):
        return money(*self._row("collateral"))

    def bond_required(self):
        share = int(self.fc.m["parameters"]["bond_collateral_share_bps"]["value"])
        rep, grp = self._row("collateral")
        return money(rep * 10_000 // share, grp * 10_000 // share)

    def security_offered(self):
        kind, amount = self._security()
        return {"full": f"full bond collateral of {usd(amount)} in cash",
                "reduced": f"reduced cash security of {usd(amount)}, its cash above its operating need for the next "
                           f"month on the day the court decides",
                "noncash": "security not in cash, or a waiver of security",
                "none": "none: the company has no cash above its operating need for the next month"}[kind]

    def stay_security_required(self):
        return f"bond collateral of {self.collateral_required()}; offered: {self.security_offered()}"

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
        p = self.fc.m["parameters"]["settlement_date_in_interval"]
        if self.fc.sens.get("settlement_date_in_interval"):
            return "the end of the current stage of the dispute"
        rep, grp = self._row("day")
        k = int(p["value"])
        return dated(self.review, rep + k, grp + k)

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
        lag = int(self.fc.m["parameters"]["holder_notice_lag_days"]["value"])
        rep, grp = self._sit("default_available")
        return dated(self.review, rep + lag if rep < BIG else BIG, np.where(grp < BIG, grp + lag, BIG))

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
                f"initiated")

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
        v = self._g().at_rep("band_range", sit=True)
        return int(v[0]), int(v[1])

    def verdict_amount(self):
        return self.judgment_amount()

    def remitted_amount(self):
        return money(*self._sit("remitted"))

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
           "appealed": "the company has appealed", "final": "the time to appeal has expired",
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
            "without_principal_measure",
            # the distress chain's question identities (worker A): the situation states the date, the offering
            # available or why not, and the notes' route
            "deadline", "determination", "offer", "nooffer", "nonpayment", "cash_out"}
IDENTITY = re.compile(r"floor\d+|award.*")  # D7's k-th fall below the need; a verdict class (the situation states it)


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
           "company disclosure": "historical_evidence", "third-party record": "historical_evidence"}
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
        need = fc.draws.basis.need
        out = []
        for r, m in zip(rows, masks, strict=True):
            t = np.minimum(r["day"], need.shape[1] - 1)
            nd = need[np.arange(len(t)), t]
            offer = r["stay_offer"] if r.get("stay_offer") is not None else 0
            out.append(m & (((r["collateral"] > 0) & (r["cash"] - nd >= r["collateral"])) | (offer > 0)))
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
    sit = Situation(fc, n, d, g, tags, labels).fill(t["situation_keys"], strict)
    values = {"company": fc.borrower, "claimant": d.counterparty,  # the question names the representative's figures
              **{k: RANGE.sub("", v) if isinstance(v, str) else json.dumps(v) for k, v in sit.items()}}
    crit = entry["prompt"]["criteria"]
    names = dict(zip(["true", "false"], t["branches"], strict=True)) if entry["primitive"] == "noul" else \
        {k: k for k in crit}
    answers = {names[k]: fill_text(v, values) for k, v in crit.items() if names[k] in n.branches}
    kinds, filled, fids = evidence_kinds(fc, n, t, d)
    readings, _ = fc._readings(d, n.question_id)
    verdict = n.node in ("verdict_finding", "verdict_amount")
    assumed = [*([] if verdict else assumed_events(tags, labels, d.counterparty, strict)), *n.assumptions]
    state = {"case": {"evidence_cutoff": fmt(fc.review), "company": fc.borrower, "counterparty": d.counterparty,
                      "obligation": f"{fc.m['natures'].get(d.nature, d.nature)}, {d.order_reference}"},
             "question": {"actor": fill_text(t["actor"], values), "text": fill_text(entry["prompt"]["instructions"],
                                                                                    values),
                          "answers": answers},
             "standard": cite(fc, t), "record_items": filled, "situation": sit, **kinds,
             "assumed_events": assumed, "earlier_readings": readings}
    return state, fids, readings
