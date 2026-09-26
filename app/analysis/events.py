"""Event cash (dispute model 4.0.0): the dated cash, collateral and petition effects of one chain path, per operating
draw, and the path facts the residual questions are given.

Code owns every date and amount. Rulings fall on each motion's close of briefing plus a lag drawn from the judge's
measured pace (`ruling_lag_days`), one draw per motion; stays, registration and levies follow the rules' clocks; the
notes' defaults follow the instrument's terms; tau is the first day available cash falls below operating need. Every
draw is keyed (instance, node, purpose), so a probability never moves a date. Amounts: quoted components, statutory
interest from quoted law (§24-5(b) 8% simple on compensatory damages from commencement; §1961 from entry), the
declared remittitur and fee scenarios (D4), settlement at the D5 feasibility bound, the levy at min(owed, available
cash), bond collateral only where the path can fund it. A petition ends every later cash effect on its trajectory.

Available cash before events is the opening balance plus cumulative operating flows (the line's draws and collections,
at most one limit's worth, are left out of this pre-engine figure). Stress mode draws every lag at its adverse end.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from app.analysis.setup import SEED, Setup
from app.disputes.forecast import DisputePath
from app.domain.investigation import DisputeInstance
from app.finance.calendar import next_business_day

TOLLING = {"rule_50b", "rule_52b", "rule_59a", "rule_59e", "injunction"}  # FRAP 4(a)(4)(A); injunction per L8(c) base
MONEY_MOTIONS = {"rule_50b", "rule_52b", "rule_59a", "rule_59e"}


@dataclass
class EventCash:
    """Per draw and day: borrower cash (+ receipt, - payment), encumbrance changes (+ lock, - release) and credit
    capacity changes (+ commit, - release). Shape [draws, horizon days], integer cents. `petition` [draws] is the
    horizon day index of a bankruptcy petition on that trajectory, or -1 for none."""
    cash: np.ndarray
    lock: np.ndarray
    capacity: np.ndarray
    petition: np.ndarray

    @classmethod
    def zeros(cls, draws: int, days: int) -> EventCash:
        z = np.zeros((draws, days), dtype=np.int64)
        return cls(z.copy(), z.copy(), z.copy(), np.full(draws, -1, dtype=np.int64))

    def __add__(self, other: EventCash) -> EventCash:
        a, b = self.petition, other.petition  # the earliest petition on the trajectory
        petition = np.where(a < 0, b, np.where(b < 0, a, np.minimum(a, b)))
        return EventCash(self.cash + other.cash, self.lock + other.lock, self.capacity + other.capacity, petition)


@dataclass
class Basis:
    """What the chains read from the operating simulation: available cash before events at each day's end, the
    collection rule's need, and the legal-fee outflows (negative cents). [draws, days]."""
    cash: np.ndarray
    need: np.ndarray
    legal: np.ndarray

    @classmethod
    def of(cls, ops, need: np.ndarray, opening_cents: int) -> Basis:
        days = need.shape[1]
        legal = ops.by_category.get("legal_fees")
        return cls(cash=opening_cents + np.cumsum(ops.total[:, :days], axis=1), need=need[:, :days],
                   legal=(legal[:, :days] if legal is not None else np.zeros_like(need[:, :days])))


class Draws:
    """Uniform draws keyed by (instance, node, purpose), identical across paths, probabilities and controls. `basis`
    is attached by the analysis (and by the forecaster's pre-pass) once the operating draws exist."""

    def __init__(self, draws: int, stress: bool = False, basis: Basis | None = None) -> None:
        self.n, self.stress, self.cache, self.basis = draws, stress, {}, basis

    def u(self, *key: str, adverse_high: bool | None = None) -> np.ndarray:
        if self.stress and adverse_high is not None:
            return np.full(self.n, 1.0 if adverse_high else 0.0)
        k = ":".join(key)
        if k not in self.cache:
            self.cache[k] = np.random.default_rng([SEED, zlib.crc32(k.encode())]).random(self.n)
        return self.cache[k]

    def lag(self, model: dict, *key: str) -> np.ndarray:
        """A ruling lag drawn from the measured sample (Data), per trajectory; in stress the fastest, so increases,
        stays' collateral and levies fall as early as the record allows."""
        sample = np.array(sorted(model["parameters"]["ruling_lag_days"]["sample"]), dtype=np.int64)
        u = self.u(*key, "ruling_lag", adverse_high=False)
        return sample[np.minimum((u * len(sample)).astype(np.int64), len(sample) - 1)]


def pval(model: dict, key: str, sensitivity: bool = False):
    p = model["parameters"][key]
    return p["sensitivity"] if sensitivity and "sensitivity" in p else p["value"]


def rate_1961_bps(model: dict, judgment: date) -> int:
    """§1961: the weekly 1-year CMT for the calendar week preceding the judgment (the latest week ending before it)."""
    table = model["rules"]["usc28_1961"]["rate_bps_by_week_ending"]
    weeks = sorted(w for w in table if date.fromisoformat(w) < judgment)
    if not weeks or (judgment - date.fromisoformat(weeks[-1])).days > 7:
        raise ValueError(f"No §1961 rate for the week before {judgment}; add it to the dispute model (sourced)")
    return int(table[weeks[-1]])


def business_days_after(d: date, n: int) -> date:
    out, k = d, 0
    while k < n:
        out += timedelta(days=1)
        if out.weekday() < 5:
            k += 1
    return out


# --- amounts (Law and arithmetic on quoted figures) ----------------------------------------------------------------

def component(d: DisputeInstance, kind: str):
    return next((c for c in d.components if c.kind == kind), None)


def entered_cents(d: DisputeInstance) -> int:
    """The judgment as entered: the awarded components, or the dispute's documented amount when none are listed."""
    awarded = [c.amount_cents for c in d.components if c.status == "awarded" and c.amount_cents is not None]
    if awarded:
        return int(sum(awarded))
    return int(d.amount.value if d.amount.value is not None else d.amount.upper)


def prejudgment_interest_cents(d: DisputeInstance, compensatory: int, model: dict) -> int:
    """N.C. Gen. Stat. §24-5(b) at the §24-1 legal rate: simple interest on the compensatory amount from commencement
    to the judgment's entry (§1961 governs after entry). None on exemplary damages."""
    if not compensatory or d.commenced is None or d.judgment_date is None:
        return 0
    bps = model["rules"][model["rules"]["nc_24_5_b"]["rate_rule"]]["value"]
    return int(round(compensatory * bps / 10_000 * (d.judgment_date - d.commenced).days / 365))


def ruling_amounts(d: DisputeInstance, outcome: dict[str, str], model: dict) -> dict[str, int]:
    """Component amounts after the post-trial ruling, by kind. `outcome` holds the merits branches taken
    (liability, damages, remittitur, patent, trebling, fees, interest); an absent key means no motion decides it."""
    comp_c, ex_c, pat_c = component(d, "compensatory"), component(d, "exemplary"), component(d, "patent")
    fee_c = component(d, "fees")
    survives = outcome.get("liability") != "granted"
    comp = 0
    if comp_c is not None and survives:
        dmg = outcome.get("damages", "stands")
        if dmg == "stands":
            comp = comp_c.amount_cents or 0
        elif dmg == "remit" and outcome.get("remittitur") == "accept":
            scen = model["remittitur_scenarios"]["base"]
            comp = (comp_c.remittitur_cents if scen == "remitted" and comp_c.remittitur_cents else comp_c.amount_cents) or 0
    exemplary = (ex_c.amount_cents or 0) if (ex_c is not None and comp > 0) else 0  # L4: falls with a new trial
    patent = (pat_c.amount_cents or 0) if (pat_c is not None and outcome.get("patent") != "granted") else 0
    out = {"compensatory": comp, "exemplary": exemplary, "patent": patent, "trebling": 0, "fees": 0,
           "prejudgment_interest": 0}
    if comp > 0 and outcome.get("trebling") == "granted":
        trebled = comp * model["rules"]["nc_75_16"]["value"]
        if trebled >= comp + exemplary:  # election (Kuykendall): the larger recovery; trebling drops exemplary
            out.update(trebling=trebled - comp, exemplary=0)
    if comp > 0 and fee_c is not None and outcome.get("fees") == "granted":
        out["fees"] = fee_c.amount_cents or 0  # D4 (a): the requested amount
    if comp > 0 and outcome.get("interest") == "granted":
        out["prejudgment_interest"] = prejudgment_interest_cents(d, comp, model)  # on the untrebled amount
    return out


def interest_1961(principal: int, increase: int, since_entry: np.ndarray, since_amended: np.ndarray, bps: int
                  ) -> np.ndarray:
    """§1961 simple within the horizon (it compounds annually): the surviving original amount from entry, increases
    from the amended judgment (Kaiser; Dunn; Eaves)."""
    return np.rint(principal * bps / 10_000 * np.maximum(since_entry, 0) / 365
                   + increase * bps / 10_000 * np.maximum(since_amended, 0) / 365).astype(np.int64)


# --- the path's timeline and effects -------------------------------------------------------------------------------

BIG = 10**6  # a day index meaning "not in this path / never"


@dataclass
class Trace:
    """One path's event cash plus, per step, the decision day and the path facts code computes at it."""
    events: EventCash
    day: list[np.ndarray] = field(default_factory=list)  # per step: decision day index per draw (>= days: none)
    cash: list[np.ndarray] = field(default_factory=list)  # per step: available cash at the decision day
    owed: list[np.ndarray] = field(default_factory=list)  # per step: amount owed at the decision day
    collateral: list[np.ndarray] = field(default_factory=list)  # per step: the bond collateral the law requires


class Chain:
    """Walks one path's steps in order, booking dated effects per trajectory (see the module docstring)."""

    def __init__(self, d: DisputeInstance, setup: Setup, model: dict, draws: Draws, sens: dict | None = None) -> None:
        self.d, self.s, self.m, self.dr = d, setup, model, draws
        self.sens = sens or {}  # parameter -> use its sensitivity value
        self.n, self.N = draws.n, (setup.horizon - setup.review).days
        self.basis = draws.basis
        self.ev = EventCash.zeros(self.n, self.N)
        self.rows = np.arange(self.n)
        self.iid = d.instance_id
        self.fin = next((f for f in d.financing if f.status != "superseded"), None)
        self.bps = rate_1961_bps(model, d.judgment_date) if d.judgment_date else 0
        self.entered = entered_cents(d)
        self._timeline()
        self.cls_amount = None  # the path amount after the ruling (None: the judgment as entered)
        self.increase = 0
        self.q1 = False
        self.appealed = False
        self.early_registration = np.full(self.n, BIG)
        self.stayed_from = np.full(self.n, BIG)
        self.resolved = np.full(self.n, BIG)
        self.delisted = np.full(self.n, BIG)
        self.levied = np.zeros(self.n, dtype=bool)
        self.taken = np.zeros(self.n, dtype=np.int64)  # levied or paid toward the judgment
        self.cls_fees = 0
        self.lock_amount = np.zeros(self.n, dtype=np.int64)
        self.collateral_required = np.zeros(self.n, dtype=np.int64)

    def p(self, key: str):
        return pval(self.m, key, self.sens.get(key, False))

    def ix(self, when: date) -> int:
        return (when - self.s.review).days - 1

    def _timeline(self) -> None:
        d = self.d
        common = self.m["parameters"]["ruling_lag_days"].get("mode") == "common"
        rule = {}
        for mo in d.motions:
            lag = self.dr.lag(self.m, self.iid, "common" if common else mo.motion_id)
            rule[mo.motion_id] = self.ix(mo.briefing_close) + lag
        self.ruling = rule
        money = [rule[mo.motion_id] for mo in d.motions if mo.kind in MONEY_MOTIONS]
        tolling = [rule[mo.motion_id] for mo in d.motions if mo.kind in TOLLING]
        fees = [rule[mo.motion_id] for mo in d.motions if mo.kind == "rule_54_fees"]
        post = d.stage == "post_trial" and bool(money)
        final = self.ix(d.judgment_date) if d.judgment_date else -1
        self.F = np.max(money, axis=0) if post else np.full(self.n, final)  # the final judgment (last change)
        self.A = np.max(tolling, axis=0) if (post and tolling) else self.F
        notice = self.m["rules"]["frap_4a1a"]["value"]
        self.AD = self.A + notice  # the appeal deadline, tolled to the last tolling order
        self.fee_day = np.max(fees, axis=0) if fees else np.full(self.n, BIG)
        stay = self.m["rules"]["frcp_62a"]["value"]
        e = max(d.judgment_date + timedelta(days=stay + 1), self.s.review) if d.judgment_date else self.s.review
        self.E0 = max(self.ix(e), 0)  # execution may issue (Rule 62(a) ended; not before the day after review)
        self.e_ix = self.ix(e)
        self.EF = self.F.copy()  # enforceable after the ruling; + 30 days on increases (L8 base), set by the ruling

    # --- state at a day ---
    def cash_at(self, day: np.ndarray) -> np.ndarray:
        cum = self.basis.cash + np.cumsum(self.ev.cash - self.ev.lock, axis=1)
        t = np.clip(day, 0, self.N - 1)
        return cum[self.rows, t]

    def owed_at(self, day: np.ndarray) -> np.ndarray:
        day = np.asarray(day)
        since_entry = day - self.ix(self.d.judgment_date) if self.d.judgment_date else np.zeros(self.n)
        before = self.entered + interest_1961(self.entered, 0, since_entry, 0, self.bps)
        if self.cls_amount is None:
            out = before
        else:
            base = min(self.cls_amount, self.entered)
            after = self.cls_amount + interest_1961(base, self.cls_amount - base, since_entry, day - self.F, self.bps)
            after = after - np.where(day < self.fee_day, self.cls_fees, 0)  # fees are owed once quantified
            out = np.where(day >= self.F, after, before)
        return np.where(day >= self.resolved, 0, np.maximum(out - self.taken, 0))


    # --- booking ---
    def book(self, arr: np.ndarray, day: np.ndarray, cents) -> None:
        day = np.asarray(day)
        cents = np.broadcast_to(np.asarray(cents, dtype=np.int64), day.shape)
        ok = (day >= 0) & (day < self.N) & (cents != 0)
        np.add.at(arr, (self.rows[ok], day[ok]), cents[ok])

    def petition(self, day: np.ndarray, where: np.ndarray | None = None) -> None:
        day = np.asarray(day) + int(self.p("petition_lag_days"))
        ok = (day >= 0) & (day < self.N) & (True if where is None else where)
        cur = self.ev.petition
        self.ev.petition = np.where(ok & ((cur < 0) | (day < cur)), day, cur)

    def live(self, day: np.ndarray) -> np.ndarray:
        """No petition before the day and the judgment not resolved by it."""
        pet = np.where(self.ev.petition < 0, BIG, self.ev.petition)
        return (np.asarray(day) < pet) & (np.asarray(day) < self.resolved)

    def resolve(self, day: np.ndarray, where: np.ndarray) -> None:
        """The dispute ends (payment or settlement): legal spend in the feed stops from that day."""
        day = np.where(where & (day < self.N), day, BIG)
        old, self.resolved = self.resolved, np.minimum(self.resolved, day)
        t = np.arange(self.N)
        stop = (t[None, :] >= self.resolved[:, None]) & (t[None, :] < old[:, None])
        self.ev.cash -= np.where(stop, self.basis.legal, 0)  # legal outflows are negative: adding them back

    def levy(self, day: np.ndarray) -> None:
        day = np.asarray(day) + int(self.p("levy_lag_days"))
        ok = self.live(day) & (day < self.stayed_from) & (day < self.N)
        take = np.where(ok, np.minimum(self.owed_at(day), np.maximum(self.cash_at(day), 0)), 0)
        self.book(self.ev.cash, day, -take)
        self.taken += take
        self.levied |= take > 0

    def settle(self, start: np.ndarray, end: np.ndarray) -> np.ndarray:
        """D5: the feasibility bound (available cash less 30-day need, floored at 0, capped at the amount owed) on
        interval start + 30 days (sensitivity: the interval's end); lump sum or monthly to the horizon."""
        pd = np.asarray(end) if self.sens.get("settlement_date_in_interval") else np.asarray(start) + int(
            self.m["parameters"]["settlement_date_in_interval"]["value"])
        ok = self.live(pd) & (pd < self.N)
        need = self.basis.need[self.rows, np.clip(pd, 0, self.N - 1)]
        bound = np.where(ok, np.clip(self.cash_at(pd) - need, 0, self.owed_at(pd)), 0)
        if self.m["settlement_scenarios"]["base"] == "monthly" or self.sens.get("settlement_monthly"):
            k = np.maximum((self.N - pd + 29) // 30, 1)
            for i in range(int(k.max())):
                part = np.where(i < k, bound // k + np.where(i == k - 1, bound % k, 0), 0)
                self.book(self.ev.cash, pd + 30 * i, -part)
        else:
            self.book(self.ev.cash, pd, -bound)
        self.resolve(pd, ok)
        return pd

    def stay(self, motion: np.ndarray, key: str) -> np.ndarray:
        """Rule 62(b): effective on approval (motion + briefing + a lag draw). The bond is the path judgment plus
        interest (L7); its collateral is locked only on trajectories whose cash covers it; elsewhere the stay rests
        on approved lesser security (no terms in the record: nothing booked)."""
        approval = motion + int(self.p("briefing_days_new_motion")) + self.dr.lag(self.m, self.iid, key)
        self.stayed_from = np.minimum(self.stayed_from, np.where(approval < self.N, approval, BIG))
        years = self.p("bond_forward_interest_years")
        owed = self.owed_at(approval)
        bond = owed + np.rint(owed * self.bps / 10_000 * years).astype(np.int64)
        share = (self.s.collateral_share[0] if self.s.collateral_share else
                 (self.m["parameters"]["bond_collateral_share_bps"]["lower"] if self.sens.get("bond_collateral_share_bps")
                  else self.m["parameters"]["bond_collateral_share_bps"]["value"]) / 10_000)
        collateral = np.rint(bond * share).astype(np.int64)
        funded = self.live(approval) & (self.cash_at(approval) >= collateral)
        self.book(self.ev.lock, approval, np.where(funded, collateral, 0))
        self.lock_amount = np.where(funded, collateral, 0)
        self.collateral_required = collateral
        return approval


    def tau(self) -> np.ndarray:
        """The first day available cash falls below operating need (sensitivity: below zero), or BIG."""
        cum = self.basis.cash + np.cumsum(self.ev.cash - self.ev.lock, axis=1)
        floor = np.zeros_like(cum) if self.sens.get("cash_floor") else self.basis.need
        below = cum < floor
        return np.where(below.any(axis=1), below.argmax(axis=1), BIG)

    def listing_dates(self) -> dict[str, int]:
        """Code timing from the instrument's compliance deadline (Nasdaq Rules 5810, 5815; DGCL §222; Rule 14a-6)."""
        dl = self.fin.listing_deadline
        effective_by = dl
        k = 1
        while k < 10:  # the bid must close at $1 for 10 consecutive business days ending on the deadline
            effective_by -= timedelta(days=1)
            k += effective_by.weekday() < 5
        det = dl + timedelta(days=1)
        panel = det + timedelta(days=int(self.p("panel_decision_days")))
        suspension = det + timedelta(days=int(self.p("suspension_after_determination_days")))
        f25 = int(self.p("form25_after_panel_days")) if self.sens.get("form25_after_panel_days") else 0
        return {"vote_call": self.ix(effective_by - timedelta(days=20)), "effective_by": self.ix(effective_by),
                "determination": self.ix(det), "hearing_request": self.ix(det + timedelta(days=7)),
                "delisted_suspension": self.ix(suspension) + (10 if f25 else 0),
                "delisted_panel": self.ix(panel) + f25}

    def repurchase_day(self, delist: int) -> int:
        when = self.s.review + timedelta(days=delist + 1)
        notice = self.fin.repurchase_notice_business_days or 0
        lo, hi = self.fin.repurchase_business_days or (0, 0)
        if self.p("repurchase_date") == "earliest":
            return self.ix(business_days_after(when, lo))
        return self.ix(business_days_after(when, notice + hi))

    def instrument_cash(self) -> None:
        """The notes' coupon (shares unless cash is elected: base shares; sensitivity cash) and the CHIPS credit (base
        $0; sensitivity prorated over the horizon)."""
        if self.fin is not None and self.fin.coupon_cents:
            share = {"shares": 0, "june_split": self.m["parameters"]["coupon_cash_share"].get("june_split_bps", 0),
                     "all_cash": 10_000}[self.sens.get("coupon_cash_share", "shares")]
            for d in self.fin.interest_dates:
                self.book(self.ev.cash, np.full(self.n, self.ix(next_business_day(d))),
                          -(self.fin.coupon_cents * share // 10_000))
        chips = int(self.p("chips_credit_cents"))
        if chips:
            per = np.full(self.N, chips // self.N, dtype=np.int64)
            per[-1] += chips - per.sum()
            self.ev.cash += per[None, :]


    # --- steps ---
    def step(self, node: str, ctx: str, branch: str) -> np.ndarray:
        """Book one step's effects; return its decision day per draw (BIG where it never arises)."""
        N, full = self.N, (lambda v: np.full(self.n, v, dtype=np.int64))
        if node == "settle":
            start = {"I1": full(-1), "I2": self.F, "I3": self.EF, "I4": self.stayed_from}[ctx]
            start = np.maximum(start, -1)  # an interval that began before the review date runs from it
            end = {"I1": self.F, "I2": self.AD, "I3": full(N - 1), "I4": full(N - 1)}[ctx]
            if branch == "yes":
                pd = self.settle(start, end)
                if ctx == "I4":  # the bond is discharged when the settlement is paid
                    self.book(self.ev.lock, pd, -np.where(self.live(pd - 1), self.lock_amount, 0))
            return np.where(end < 0, BIG, np.maximum(start, 0))  # a closed interval asks nothing
        if node == "execute_pre_ruling":
            self.q1 = branch == "yes"
            return full(self.E0)
        if node == "stay":
            motion = full(self.E0) if ctx == "I1" else np.maximum(self.F, 0)
            if branch == "yes":
                self.stay(motion, f"stay_{ctx}")
            return motion
        if node == "debtor_response":
            milestone = full(self.E0) if ctx == "I1" else np.maximum(self.EF, 0)
            if branch == "pay":
                ok = self.live(milestone) & (milestone < N) & (self.cash_at(milestone) >= self.owed_at(milestone))
                amt = np.where(ok, self.owed_at(milestone), 0)
                self.book(self.ev.cash, milestone, -amt)
                self.taken += amt
                self.resolve(milestone, ok)
            elif branch == "file":
                self.petition(milestone, self.live(milestone))
            return milestone
        if node == "registration_early":
            motion = full(self.E0) if ctx == "I1" else self.F
            order = motion + int(self.p("briefing_days_new_motion")) + self.dr.lag(self.m, self.iid, f"registration_{ctx}")
            if branch == "yes":
                self.early_registration = np.minimum(self.early_registration, order)
                self.levy(order)
            return motion
        if node == "judgment_default":
            f = self.fin
            if ctx == "I1":
                ripe = full(self.e_ix + f.judgment_default_days)
                cond = (self.F > ripe) & (self.entered - f.insured_cents > f.judgment_default_threshold_cents)
            else:
                ripe = np.maximum(self.EF, self.e_ix) + f.judgment_default_days
                amount = (self.cls_amount or 0) - f.insured_cents
                cond = (self.F >= 0) & (amount > f.judgment_default_threshold_cents)
            cond = cond & (self.stayed_from > ripe) & self.live(ripe) & (self.owed_at(ripe) > 0)
            if branch == "yes":
                self.petition(ripe + int(self.p("holder_notice_lag_days")), cond)
            return np.where(cond, ripe, BIG)
        if node == "ruling":
            if branch == "none":
                self.cls_amount = 0
            else:
                _, total, fees = branch.split(":")
                self.cls_amount, self.cls_fees = int(total), int(fees)
            increase = (self.cls_amount or 0) > self.entered
            self.EF = self.F + (int(self.p("stay_restart_on_increase_days")) if increase else 0)
            return self.F
        if node == "appeal":
            self.appealed = branch == "yes"
            return np.where(self.AD < 0, BIG, np.maximum(self.F, 0))  # the time to appeal has run: nothing to ask
        if node == "enforce":
            if branch == "levy":
                if (self.early_registration < BIG).any():
                    day = np.where(self.early_registration < BIG, np.maximum(self.EF, self.early_registration),
                                   self.AD + 1)
                else:
                    day = self.AD + 1  # registration once the time to appeal has expired (§1963)
                if self.appealed:  # still unfinal: registration only on good cause, ordered on the creditor's motion
                    order = self.EF + int(self.p("briefing_days_new_motion")) + self.dr.lag(self.m, self.iid,
                                                                                         "registration_post")
                    day = np.where(self.early_registration < BIG, np.maximum(self.EF, self.early_registration), order)
                self.levy(np.maximum(day, 0))
            return np.maximum(self.EF, 0)
        if node == "listing":
            dates = self.listing_dates()
            if branch.startswith("delisted"):
                self.delisted = full(dates[branch])
                return full(dates["determination"])
            return full(dates["vote_call"])
        if node == "delisting_notes":
            if branch == "petition_delist":
                self.petition(self.delisted + int(self.p("holder_notice_lag_days")))
            elif branch == "petition_repurchase":
                self.petition(full(self.repurchase_day(int(self.delisted[0]))))
            return self.delisted
        if node == "cash_floor":
            t = self.tau()
            if branch == "yes":
                self.petition(t, t < N)
            return t
        raise ValueError(f"Unknown chain step {node}")

    def run(self, steps) -> Trace:
        self.instrument_cash()
        tr = Trace(self.ev)
        for node, ctx, branch in steps:
            before_cash = self.basis.cash + np.cumsum(self.ev.cash - self.ev.lock, axis=1)
            day = self.step(node, ctx, branch)
            t = np.clip(day, 0, self.N - 1)
            tr.day.append(day)
            tr.cash.append(before_cash[self.rows, t])
            tr.owed.append(self.owed_at(day))
            tr.collateral.append(self.collateral_required.copy())
        pet = self.ev.petition
        after = (pet[:, None] >= 0) & (np.arange(self.N)[None, :] >= pet[:, None])
        self.ev.cash[after] = 0  # §362: nothing is collected from or paid by the estate after the petition
        tr.events = self.ev
        return tr


def event_trace(d: DisputeInstance, path: DisputePath, setup: Setup, model: dict, draws: Draws,
                sens: dict | None = None) -> Trace:
    return Chain(d, setup, model, draws, sens).run(path.steps)


def event_cash(d: DisputeInstance, path: DisputePath, setup: Setup, model: dict, draws: Draws) -> EventCash:
    """The analysis entry point (app/analysis/core.py): the path's event cash on the shared draws."""
    return event_trace(d, path, setup, model, draws).events
