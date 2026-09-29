"""Event cash (dispute model 4.0.0): the dated cash, collateral and petition effects of one chain path, per operating
draw, and the path facts the residual questions are given.

Code owns every date and amount. Rulings fall on each motion's close of briefing plus a lag drawn from the judge's
measured pace (`ruling_lag_days`), one draw per motion; stays, registration and levies follow the rules' clocks; the
notes' defaults follow the instrument's terms; tau is the first day available cash falls below operating need. Every
draw is keyed (instance, node, purpose), so a probability never moves a date. Amounts: quoted components, statutory
interest from quoted law (§24-5(b) 8% simple on compensatory damages from commencement; §1961 from entry), the
declared remittitur and fee scenarios, settlement at the payer's cash above operating need, the levy at min(owed, available
cash), bond collateral only where the path can fund it. A petition ends every later cash effect on its trajectory.

Available cash before events is the opening balance plus cumulative operating flows (the line's draws and collections,
at most one limit's worth, are left out of this pre-engine figure). Stress mode draws every lag at its adverse end.
"""

from __future__ import annotations

import copy
import zlib
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from app.analysis.setup import SEED, Setup
from app.disputes.forecast import DisputePath
from app.domain.investigation import DisputeInstance
from app.finance.calendar import add_months, next_business_day

TOLLING = {"rule_50b", "rule_52b", "rule_59a", "rule_59e", "injunction"}  # FRAP 4(a)(4)(A); the injunction ruling tolls
MONEY_MOTIONS = {"rule_50b", "rule_52b", "rule_59a", "rule_59e"}


# The kinds of event cash the daily processor orders (QUESTIONS_20240514 §2.2; engine.run_many under "daily"):
# receipts (financing proceeds, a credit), a levy, the scheduled obligations (each with the day it was incurred), and
# reductions of operating outflow (legal spend that stops when the dispute ends: an outflow that stops, not a receipt).
OBLIGATIONS = ("settlement", "notes_interest", "judgment")
KINDS = ("inflow", "levy", "reduction", *OBLIGATIONS)
INCURRED_BEFORE = -(10**6)  # incurred before the review date and before the line opened (the notes' indenture, 2022)


@dataclass
class EventCash:
    """Per draw and day: borrower cash (+ receipt, - payment), encumbrance changes (+ lock, - release) and credit
    capacity changes (+ commit, - release). Shape [draws, horizon days], integer cents. `petition` [draws] is the
    horizon day index of a bankruptcy petition on that trajectory, or -1 for none. `kinds` splits `cash` by KINDS (they
    sum to it exactly; None: unclassified), and `incurred` gives each obligation kind's incurred day per draw (BIG:
    none), which orders the day's scheduled obligations."""
    cash: np.ndarray
    lock: np.ndarray
    capacity: np.ndarray
    petition: np.ndarray
    kinds: dict[str, np.ndarray] | None = None
    incurred: dict[str, np.ndarray] | None = None

    @classmethod
    def zeros(cls, draws: int, days: int, kinds: bool = False) -> EventCash:
        z = np.zeros((draws, days), dtype=np.int64)
        return cls(z.copy(), z.copy(), z.copy(), np.full(draws, -1, dtype=np.int64),
                   {k: z.copy() for k in KINDS} if kinds else None,
                   {k: np.full(draws, BIG, dtype=np.int64) for k in OBLIGATIONS} if kinds else None)

    def split(self) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]] | None:
        """The kinds and incurred days, zeros where no cash is booked at all; None where cash is unclassified."""
        if self.kinds is not None:
            return self.kinds, self.incurred
        if self.cash.any():
            return None
        n = self.cash.shape[0]
        return ({k: np.zeros_like(self.cash) for k in KINDS},
                {k: np.full(n, BIG, dtype=np.int64) for k in OBLIGATIONS})

    def __add__(self, other: EventCash) -> EventCash:
        a, b = self.petition, other.petition  # the earliest petition on the trajectory
        petition = np.where(a < 0, b, np.where(b < 0, a, np.minimum(a, b)))
        kinds = incurred = None
        if self.kinds is not None or other.kinds is not None:  # both classified (or empty): add kind by kind
            x, y = self.split(), other.split()
            if x is not None and y is not None:
                kinds = {k: x[0][k] + y[0][k] for k in KINDS}
                incurred = {k: np.minimum(x[1][k], y[1][k]) for k in OBLIGATIONS}
        return EventCash(self.cash + other.cash, self.lock + other.lock, self.capacity + other.capacity, petition,
                         kinds, incurred)


@dataclass
class Basis:
    """What the chains read from the operating simulation: available cash before events at each day's end, the
    collection rule's need, and the legal-fee outflows (negative cents). [draws, days]."""
    cash: np.ndarray
    need: np.ndarray
    legal: np.ndarray
    line: object | None = None  # the loan engine's Line (engine.prepare), for cash_facts = engine_forward_run
    opening: int = 0
    runs: dict = field(default_factory=dict)  # event-cash fingerprint -> the line's cumulative net cash [draws, days]
    inflow: np.ndarray | None = None  # the operating receipts [draws, days] (a levy attaches them, daily processing)

    @classmethod
    def of(cls, ops, need: np.ndarray, opening_cents: int, line=None) -> Basis:
        days = need.shape[1]
        legal = ops.by_category.get("legal_fees")
        return cls(cash=opening_cents + np.cumsum(ops.total[:, :days], axis=1), need=need[:, :days],
                   legal=(legal[:, :days] if legal is not None else np.zeros_like(need[:, :days])), line=line,
                   opening=opening_cents, inflow=None if ops.inflow is None else ops.inflow[:, :days])


class Draws:
    """Uniform draws keyed by (instance, node, purpose), identical across paths, probabilities and controls. `basis`
    is attached by the analysis (and by the forecaster's pre-pass) once the operating draws exist."""

    def __init__(self, draws: int, stress: bool = False, basis: Basis | None = None) -> None:
        self.n, self.stress, self.cache, self.basis = draws, stress, {}, basis
        self.prefixes: dict | None = None  # {}: walk chains from cached prefixes (events._run)

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


def pval(model: dict, key: str, sensitivity: bool | str = False):
    """The parameter's value: its base, its sensitivity (True), or the named sensitivity (a string)."""
    p = model["parameters"][key]
    if isinstance(sensitivity, str):
        return sensitivity
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


def judgment_bps(model: dict, d: DisputeInstance | None, sens: dict | None = None) -> int:
    """§1961's rate: the recorded judgment's week (usc28_1961), or for a pending claim the case's latest published
    rate at the review date (rate_1961_bps_pending)."""
    if d is None:
        return 0
    if d.stage == PENDING:
        return int(pval(model, "rate_1961_bps_pending"))
    return rate_1961_bps(model, d.judgment_date) if d.judgment_date else 0


def pending_template(model: dict) -> dict:
    return next(t for t in model["templates"].values() if t.get("stage") == PENDING)


class UnknownAmount(ValueError):
    """A branch amount that rests on a component the record leaves unquantified, where the case declares no bound for
    it: the analysis stops and names the missing input (an unknown amount is never 0)."""


def counted_components(d: DisputeInstance, model: dict, principal: bool = True) -> list:
    """The claimant's requested components a verdict branch sums (amount_rules): no restatement of another
    (duplicates), none of the defense's theory, none of a claim whose damages the court barred, none of the kinds the
    bounded claimant_enhancements term stands for (exemplary, enhanced and statutory additions, fees, costs,
    interest); principal False also leaves out the claimant's principal measure."""
    barred = {c.claim_id for c in d.claims if c.damages_barred}
    plus = set(model["parameters"].get("claimant_enhancements", {}).get("kinds", ()))
    return [c for c in d.components if c.status == "requested" and not c.duplicates and c.claim not in barred
            and c.theory != "defense" and c.kind not in plus and (principal or not c.principal)]


def claim_components(d: DisputeInstance, model: dict, principal: bool = True) -> int | None:
    """The sum of the counted components; None (unknown) where any of them has no quoted amount."""
    cs = counted_components(d, model, principal)
    if any(c.amount_cents is None for c in cs):
        return None
    return int(sum(c.amount_cents for c in cs))


def verdict_basis(d: DisputeInstance, model: dict, branch: str, sens: dict | None = None) -> tuple[int, str]:
    """A verdict branch's judgment amount (template verdict_branches) and what it rests on: 'record' (the quoted
    components), 'bound' (the case's declared bound, where a counted component's amount is unknown), 'declared' (a
    declared amount, e.g. the sensitivity), or 'none' (the branch enters no judgment). Raises UnknownAmount where a
    counted component is unknown and the case declares no bound."""
    sens = sens or {}
    spec = pending_template(model)["verdict_branches"][branch]
    if not spec["judgment"]:
        return 0, "none"
    rule = spec["amount"]
    v = "components" if rule == "components" else pval(model, rule, sens.get(rule, False))
    if v in ("components", "components_without_principal"):
        amount, how = claim_components(d, model, principal=v == "components"), "record"
        if amount is None:
            bound = model["parameters"].get(rule, {}).get("bound")
            if bound is None:
                missing = [c.component_id for c in counted_components(d, model, v == "components")
                           if c.amount_cents is None]
                raise UnknownAmount(f"The '{branch}' verdict amount is unknown: components {missing} have no quoted "
                                    f"amount, and the case declares no bound for it (scenario.json parameters). "
                                    f"Record the amounts from the passages that state them, or declare the bound.")
            amount, how = int(bound), "bound"
    else:
        amount, how = int(v), "declared"
    if spec.get("plus"):
        amount += int(pval(model, spec["plus"], sens.get(spec["plus"], False)))
    return amount, how


def settlement_terms(model: dict, sens: dict | None = None) -> tuple[str, int]:
    """How an agreed settlement is paid: 'installments' (the settlement amount in `installments` equal monthly
    payments from the settlement date; the case's terms), 'lump_sum' (one payment on the settlement date) or 4.0.0's
    'monthly' sensitivity (equal payments to the horizon). The case sets settlement_payment; otherwise the contract's
    settlement_scenarios."""
    sens = sens or {}
    if model["settlement_scenarios"]["base"] == "monthly" or sens.get("settlement_monthly"):
        return "monthly", 1
    p = model["parameters"].get("settlement_payment", {})
    mode = pval(model, "settlement_payment", sens.get("settlement_payment", False)) if p else "lump_sum"
    return mode, int(p.get("installments") or 1) if mode == "installments" else 1


def verdict_amount(d: DisputeInstance, model: dict, branch: str, sens: dict | None = None) -> int:
    """A verdict branch's judgment amount (verdict_basis): 0 where the branch enters no judgment."""
    return verdict_basis(d, model, branch, sens)[0]


def prejudgment_interest_cents(d: DisputeInstance, compensatory: int, model: dict) -> int:
    """N.C. Gen. Stat. §24-5(b) at the §24-1 legal rate: simple interest on the compensatory amount from commencement
    to the judgment's entry (§1961 governs after entry). None on exemplary damages."""
    if not compensatory or d.commenced is None or d.judgment_date is None:
        return 0
    bps = model["rules"][model["rules"]["nc_24_5_b"]["rate_rule"]]["value"]
    return int(round(compensatory * bps / 10_000 * (d.judgment_date - d.commenced).days / 365))


def known(c, what: str) -> int:
    """A component's quoted amount where the path needs it; an unknown amount stops the analysis (never 0)."""
    if c.amount_cents is None:
        raise UnknownAmount(f"{what}: component {c.component_id} has no quoted amount, and the path needs it")
    return int(c.amount_cents)


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
            comp = known(comp_c, "the judgment as entered")
        elif dmg == "remit" and outcome.get("remittitur") == "accept":
            scen = model["remittitur_scenarios"]["base"]
            remitted = (model["remittitur_scenarios"]["scenarios"].get("remitted", {}).get("amount_cents")
                        or comp_c.remittitur_cents)
            comp = remitted if scen == "remitted" and remitted else known(comp_c, "the remitted judgment")
    exemplary = known(ex_c, "exemplary damages") if (ex_c is not None and comp > 0) else 0  # exemplary damages fall with a new trial on compensatory damages
    patent = known(pat_c, "patent damages") if (pat_c is not None and outcome.get("patent") != "granted") else 0
    out = {"compensatory": comp, "exemplary": exemplary, "patent": patent, "trebling": 0, "fees": 0,
           "prejudgment_interest": 0}
    if comp > 0 and outcome.get("trebling") == "granted":
        trebled = comp * model["rules"]["nc_75_16"]["value"]
        if trebled >= comp + exemplary:  # election (Kuykendall): the larger recovery; trebling drops exemplary
            out.update(trebling=trebled - comp, exemplary=0)
    if comp > 0 and fee_c is not None and outcome.get("fees") == "granted":
        out["fees"] = known(fee_c, "fees")  # the requested amount, if awarded
    if comp > 0 and outcome.get("interest") == "granted":
        out["prejudgment_interest"] = prejudgment_interest_cents(d, comp, model)  # on the untrebled amount
    return out


MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"), 1)}
PER_YEAR = {"semi-annually": 2, "semiannually": 2, "quarterly": 4, "annually": 1}


def coupon_terms(fin, quotes: list[str], review: date, horizon: date):
    """The instrument's coupon from its accepted findings' quoted terms: principal x annual rate / payments a year,
    each figure read from the quote text (the principal must match the instrument's), and the interest dates inside
    the analysis period. Where the quotes do not state them, the instrument is returned unchanged (its coupon stays
    unknown)."""
    import re
    from decimal import Decimal

    text = " ".join(quotes)
    principal = re.search(r"\$(\d+(?:\.\d+)?) million aggregate principal amount", text)
    rate = (re.search(r"interest at a rate of (\d+(?:\.\d+)?)% per year", text)
            or re.search(r"(\d+(?:\.\d+)?)% Convertible", text))
    freq = next((n for w, n in PER_YEAR.items() if f"payable {w}" in text), None)
    days = re.search(r"on (\w+) (\d{1,2}) and (\w+) (\d{1,2}) of each year", text)
    if not (principal and rate and freq and days) or fin.principal_cents is None:
        return fin
    if int(Decimal(principal.group(1)) * 100_000_000) != fin.principal_cents:
        return fin
    cents = Decimal(fin.principal_cents) * Decimal(rate.group(1)) / 100 / freq
    if cents != cents.to_integral_value():
        return fin
    md = [(MONTHS[days.group(1).lower()], int(days.group(2))), (MONTHS[days.group(3).lower()], int(days.group(4)))]
    dates = sorted(date(y, mo, dd) for y in range(review.year, horizon.year + 1) for mo, dd in md
                   if review < date(y, mo, dd) <= horizon)
    return fin.model_copy(update={"coupon_cents": fin.coupon_cents if fin.coupon_cents is not None else int(cents),
                                  "interest_dates": tuple(fin.interest_dates) or tuple(dates)})


def interest_1961(principal: int, increase: int, since_entry: np.ndarray, since_amended: np.ndarray, bps: int
                  ) -> np.ndarray:
    """§1961 simple within the horizon (it compounds annually): the surviving original amount from entry, increases
    from the amended judgment (Kaiser; Dunn; Eaves)."""
    return np.rint(principal * bps / 10_000 * np.maximum(since_entry, 0) / 365
                   + increase * bps / 10_000 * np.maximum(since_amended, 0) / 365).astype(np.int64)


# --- the path's timeline and effects -------------------------------------------------------------------------------

BIG = 10**6  # a day index meaning "not in this path / never"
PENDING = "liability_pending"  # the stage of a claim at trial (template pending_money_claim, dispute model 4.1.0)
RESPONSES = ("debtor_response", "judgment_response")  # the debtor's response steps (4.0.0; 4.1.0)
FLOOR_NODES = {"cash_floor": "petition_cash_floor", "cash_out": "petition_cash_out"}  # step -> its question node
BANK = "bank"  # the bank view's chain: the common borrower inputs and the company's distress decisions, no dispute
# the dated contract and rule triggers given to the questions (day index; BIG: none), by source: those the dispute
# sets (`trigger_days` under `self.d`: the appeal deadline and the judgment default's ripe dates, which exist only
# because of a modeled judgment) and those of the borrower's instrument (under `self.fin`: the ordinary obligation)
DISPUTE_TRIGGERS = ("judgment_default_entered", "judgment_default_ruling", "appeal_deadline")
INSTRUMENT_TRIGGERS = ("coupon", "listing_deadline", "repurchase_due", "holders_petition_earliest")
TRIGGERS = DISPUTE_TRIGGERS + INSTRUMENT_TRIGGERS


PETITION_CAUSES = ("none", "enforcement", "notes", "cash_floor")
# The conditions a decision can be asked in, each dated per trajectory (BIG: not on this trajectory)
MARKS = ("executing", "stay_moved", "stayed", "ruled", "settled", "paid", "levied", "appealed", "seeking", "notes_due",
         "delisted", "raised")  # the rule that booked a trajectory's petition


@dataclass
class Trace:
    """One path's event cash plus, per step, the decision day and the path facts code computes at it."""
    events: EventCash
    day: list[np.ndarray] = field(default_factory=list)  # per step: decision day index per draw (>= days: none)
    cash: list[np.ndarray] = field(default_factory=list)  # per step: available cash at the decision day
    owed: list[np.ndarray] = field(default_factory=list)  # per step: amount owed at the decision day
    collateral: list[np.ndarray] = field(default_factory=list)  # per step: the bond collateral the law requires
    cause: np.ndarray | None = None  # per draw: which rule booked the earliest petition (PETITION_CAUSES index)
    marks: dict = field(default_factory=dict)  # MARKS name -> the day it holds from, per draw (BIG: never)
    settle_offer: np.ndarray | None = None  # the last step's settlement amount on its payment date (0: none)
    stay_offer: np.ndarray | None = None  # the last step's cash above the 30-day need on the stay-approval day (0: none)
    raise_offer: np.ndarray | None = None  # the last step's equity available at the cash floor (0: none)
    late: dict = field(default_factory=dict)  # a floor step's index -> its petition day, triggers, equity available
    reads: np.ndarray | None = None  # the last step's latest day whose cash it read (a payment, approval or levy day)
    triggers: dict = field(default_factory=dict)  # TRIGGERS name -> day index per draw (BIG: none)
    # daily processing: a stay step's index -> the security sized on the whole path (Chain.restay): its approval day,
    # the cash, amount owed, collateral and reduced security that day, and the path's petition day
    stays: dict = field(default_factory=dict)
    situations: dict = field(default_factory=dict)  # worker C: step index -> Chain.c_situation


class Chain:
    """Walks one path's steps in order, booking dated effects per trajectory (see the module docstring)."""

    def __init__(self, d: DisputeInstance | None, setup: Setup, model: dict, draws: Draws, sens: dict | None = None,
                 fin=None) -> None:
        """`d` None: the bank view's chain, with the borrower's instrument `fin` (its coupon) and no dispute."""
        self.d, self.s, self.m, self.dr = d, setup, model, draws
        self.sens = sens or {}  # parameter -> use its sensitivity value
        self.n, self.N = draws.n, (setup.horizon - setup.review).days
        self.basis = draws.basis
        self.ev = EventCash.zeros(self.n, self.N, kinds=True)
        self._cv = 0  # the event cash's version: every write to it bumps it (`_touch`); `cum` is memoized on it
        self._cum: tuple | None = None  # (version, available cash [draws, days]); never written in place
        self.pet_cause = np.zeros(self.n, dtype=np.int8)  # PETITION_CAUSES index of the earliest petition
        self.rows = np.arange(self.n)
        self.iid = d.instance_id if d is not None else BANK
        self.fin = next((f for f in d.financing if f.status != "superseded"), None) if d is not None else fin
        self.pending = d is not None and d.stage == PENDING  # a claim at trial: no judgment until the verdict step
        # the ordinary view of a case under spec §16.1 (case input ordinary_view): the same forecast with the event,
        # including its legal costs, given no cash effect; its floor decisions book on their own day, as the event's
        self.ordinary = d is None and model["parameters"].get("ordinary_view", {}).get("value") == "same_forecast"
        self.bps = judgment_bps(model, d, self.sens)
        self.entered = 0 if self.pending else entered_cents(d) if d is not None else 0
        self.bookings = model.get("branch_bookings", {}).get("nodes", {})
        self.engine = self.p("cash_facts") == "engine_forward_run"
        self.daily = setup.cash_processing == "daily"  # the Chain's cash is the daily processor's (QUESTIONS §2.2)
        if self.daily and not self.engine:
            raise ValueError("daily cash processing reads the loan engine's cash: cash_facts = engine_forward_run")
        # the case sets raise_capacity: the floor decision is financing_at_floor (4.1.0), else petition_cash_floor
        self.raising = "value" in model["parameters"].get("raise_capacity", {})
        self.adverse_from = np.full(self.n, BIG, dtype=np.int64)  # entry of a judgment on an adverse verdict branch
        self.adverse_until = np.full(self.n, BIG, dtype=np.int64)  # the ruling that set that judgment aside
        if self.engine and (self.basis is None or self.basis.line is None):
            raise ValueError("cash_facts = engine_forward_run needs the loan engine's line on the basis (Basis.of line=)")
        if self.pending:
            self._timeline_pending()
        elif d is not None:
            self._timeline()
        else:
            self.ruling, never = {}, np.full(self.n, BIG)
            self.F, self.A, self.AD, self.fee_day, self.EF, self.EI = (never.copy() for _ in range(6))
            self.E0, self.e_ix = 0, -1
        self.cls_amount = None  # the path amount after the ruling (None: the judgment as entered)
        self.increase = 0
        self.retrial = False  # the ruling orders a new trial: the dispute goes on after any payment
        self.q1 = False
        self.appealed = False
        self.early_registration = np.full(self.n, BIG)
        self.pending_levy: np.ndarray | None = None  # the pre-ruling levy day (early-registration order + levy lag), not yet booked
        self.stayed_from = np.full(self.n, BIG)
        self.resolved = np.full(self.n, BIG)
        self.delisted = np.full(self.n, BIG)
        self.levied = np.zeros(self.n, dtype=bool)
        self.taken = np.zeros(self.n, dtype=np.int64)  # levied or paid toward the judgment
        self.takes: list[tuple[np.ndarray, np.ndarray]] = []  # (day, amount) of each levy or payment (`taken_before`)
        self.writs: list[tuple[np.ndarray, np.ndarray]] = []  # (day, amount taken) per writ, per trajectory
        self.cls_fees = 0
        self.lock_amount = np.zeros(self.n, dtype=np.int64)
        self.lock_day = np.full(self.n, BIG)  # the approval day of the stay whose security is locked
        self.collateral_required = np.zeros(self.n, dtype=np.int64)
        self.marks = {k: np.full(self.n, BIG) for k in MARKS}
        self.jd_acted = np.zeros(self.n, dtype=bool)  # the holders acted on the judgment default as entered
        self.settle_offer = np.zeros(self.n, dtype=np.int64)
        self.stay_offer = np.zeros(self.n, dtype=np.int64)
        self.raise_offer = np.zeros(self.n, dtype=np.int64)
        self.coupons: list[tuple[int, int, np.ndarray]] = []  # (payment day, cash, still paid per draw)
        self.rec: tuple[list, list, list, list] = ([], [], [], [])  # per step: decision day, cash, owed, collateral
        # the state-triggered decisions (FLOOR_NODES) walked but not yet booked on every trajectory: [step index, node,
        # branch, booked mask]; each books on its own day (`upto`), whatever the walk order
        self.waiting: list = []
        self.wctx: dict = {}  # a waiting step's index -> its context
        self.late: dict = {}  # their step index -> petition day, triggers and equity available at the decision
        self.reads = np.full(self.n, -1, dtype=np.int64)  # the current step's latest cash-read day (`seen_at`)
        # daily processing (QUESTIONS §2.5): each walked stay (step index -> its terms and what it booked), re-sized on
        # the approval day's balance after every later booking (`restay`); the day the dispute ends, per draw
        self.stays: dict = {}
        self.release_at = np.full(self.n, BIG, dtype=np.int64)
        self._stay_cv = -1  # the event cash's version at the last `restay`
        self._restaying = False  # inside `restay` (its views do not re-size)

    def mark(self, name: str, day, where=None) -> None:
        day = np.broadcast_to(np.asarray(day, dtype=np.int64), (self.n,))
        ok = (day >= 0) & (day < self.N) & (True if where is None else where)
        self.marks[name] = np.where(ok, np.minimum(self.marks[name], day), self.marks[name])

    def p(self, key: str):
        return pval(self.m, key, self.sens.get(key, False))

    def ix(self, when: date) -> int:
        return (when - self.s.review).days - 1

    def _timeline_pending(self) -> None:
        """A pending claim: the verdict day V drawn per trajectory over the case's window (key (instance, verdict,
        date)); entry, execution, the motions, the ruling and the appeal deadline are set by the verdict and
        post_trial_motions steps from the modeled V. Nothing exists before the verdict (BIG)."""
        w = self.m["parameters"]["verdict_window"]
        days = [w["sensitivity"]] if self.sens.get("verdict_window") else w["days"]
        idx = np.array([self.ix(date.fromisoformat(x)) for x in days], dtype=np.int64)
        u = self.dr.u(self.iid, "verdict", "date", adverse_high=False)
        self.V = idx[np.minimum((u * len(idx)).astype(np.int64), len(idx) - 1)]
        self.ruling, never = {}, np.full(self.n, BIG, dtype=np.int64)
        self.F, self.A, self.AD, self.fee_day, self.EF, self.EI = (never.copy() for _ in range(6))
        self.E_ix, self.E0, self.e_ix = never.copy(), never.copy(), never.copy()

    def has_judgment(self) -> bool:
        return self.d is not None and (self.d.judgment_date is not None or (self.pending and self.entered > 0))

    def entry_ix(self):
        """The judgment's entry day index: per trajectory for a pending claim, else the recorded date's."""
        return self.E_ix if self.pending else self.ix(self.d.judgment_date)

    def _touch(self) -> None:
        """The event cash changed: the memoized available cash is stale."""
        self._cv += 1

    def cum(self) -> np.ndarray:
        """Available cash at each day's end [draws, days]: the opening balance, operating flows and event cash less
        encumbrance; under cash_facts = engine_forward_run also the line's draws less its collections, from the loan
        engine run forward on this event cash (causal: a day's cash reads nothing booked after it). Memoized on the
        event cash's version: the walk reads the same state many times between bookings. Callers never write to it."""
        if self._cum is not None and self._cum[0] == self._cv:
            return self._cum[1]
        if self.daily:  # the processor's end-of-day available cash: one definition for the Chain and the engine
            c = self.processed()[0]
            self._cum = (self._cv, c)
            return c
        c = self.basis.cash + np.cumsum(self.ev.cash - self.ev.lock, axis=1)
        c = c + self.line_net() if self.engine else c
        self._cum = (self._cv, c)
        return c

    def line_net(self) -> np.ndarray:
        import hashlib

        from app.analysis.engine import run

        ev, h = self.ev, hashlib.blake2b(digest_size=20)
        for a in (ev.cash, ev.lock):
            flat = np.ascontiguousarray(a).ravel()
            i = np.flatnonzero(flat)
            h.update(np.int64(i.size).tobytes() + i.tobytes() + flat[i].tobytes())
        h.update(np.ascontiguousarray(ev.petition).tobytes())
        runs, key = self.basis.runs, h.digest()
        if key in runs:  # least recently used first: a hit moves to the end
            runs[key] = runs.pop(key)
        else:
            # the engine adds the existing line's history cash (Setup.exposure) to its opening itself
            opening = self.basis.opening - self.s.exposure.cash_cents
            tr = run(self.basis.line, opening, EventCash(ev.cash, ev.lock, ev.capacity, ev.petition))
            if len(runs) >= 256:  # the tree is walked depth-first: recent prefixes are the ones reused
                runs.pop(next(iter(runs)))
            runs[key] = np.cumsum(tr.fundings - tr.collections, axis=1)
        return runs[key]

    def processed(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Under daily processing: the engine's end-of-day available cash [draws, days], first unpaid day and §3.3
        day [draws] (BIG: none) on this event cash, from the run cached per event state (as `line_net`)."""
        import hashlib

        from app.analysis.engine import run

        ev, h = self.ev, hashlib.blake2b(digest_size=20)
        terms = self.nonpayment_terms()
        h.update(b"daily" + np.array(terms, dtype=np.int64).tobytes())
        for a in (ev.lock, *(ev.kinds[k] for k in KINDS)):  # the kinds sum to the cash: it adds nothing
            if not a.any():
                h.update(b"0")
                continue
            flat = np.ascontiguousarray(a).ravel()
            i = np.flatnonzero(flat)
            h.update(np.int64(i.size).tobytes() + i.tobytes() + flat[i].tobytes())
        for k in OBLIGATIONS:
            h.update(np.ascontiguousarray(ev.incurred[k]).tobytes())
        h.update(np.ascontiguousarray(ev.petition).tobytes())
        runs, key = self.basis.runs, h.digest()
        if key in runs:
            runs[key] = runs.pop(key)
        else:
            opening = self.basis.opening - self.s.exposure.cash_cents
            tr = run(self.basis.line, opening, EventCash(ev.cash, ev.lock, ev.capacity, ev.petition, ev.kinds,
                                                         ev.incurred), terms)
            if len(runs) >= 256:
                runs.pop(next(iter(runs)))
            runs[key] = (tr.cash, tr.processed.first_unpaid, tr.processed.nonpayment)
        return runs[key]

    def nonpayment_terms(self) -> tuple[int, int]:
        """§7.01(j)(v) general nonpayment (QUESTIONS §3.3): the window in days and the unpaid share in bps, as the
        contract declares them (a sensitivity is named by its value)."""
        out = []
        for key in ("nonpayment_window_days", "nonpayment_unpaid_share_bps"):
            p, pick = self.m["parameters"][key], self.sens.get(key, False)
            v = p["value"] if pick is False else p["sensitivity"] if pick is True else pick
            if isinstance(v, list):
                raise ValueError(f"{key}: name the sensitivity by its value, one of {v}")
            out.append(int(v))
        return out[0], out[1]

    def processing_balance(self, day: np.ndarray) -> np.ndarray:
        """Under daily processing: the balance a levy served on the day attaches (§2.2): the day before's end balance
        plus the day's receipts, less the day's encumbrance change and any levy already booked that day."""
        cum, t = self.cum(), np.clip(day, 0, self.N - 1)
        prev = np.where(t > 0, cum[self.rows, np.maximum(t - 1, 0)], self.basis.opening)
        k = self.ev.kinds
        return (prev + self.basis.inflow[self.rows, t] + k["inflow"][self.rows, t] - self.ev.lock[self.rows, t]
                + k["levy"][self.rows, t])

    def nonpayment_day(self) -> np.ndarray:
        """The first day §7.01(j)(v) general nonpayment is met (QUESTIONS §3.3), or BIG. Daily processing only."""
        if not self.daily:
            raise ValueError("general nonpayment is tested on the daily cash processor (cash_processing = daily)")
        return self.processed()[2].copy()

    def _timeline(self) -> None:
        d = self.d
        common = self.m["parameters"]["ruling_lag_days"].get("mode") == "common"
        rule = {}
        close = max((mo.briefing_close for mo in d.motions if mo.briefing_close), default=None)
        for mo in d.motions:  # a motion with no briefing date of its own follows the shared schedule (D.I. 605)
            lag = self.dr.lag(self.m, self.iid, "common" if common else mo.motion_id)
            rule[mo.motion_id] = self.ix(mo.briefing_close or close) + lag
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
        self.EF = self.F.copy()  # the surviving amount is enforceable from the ruling
        self.EI = self.F.copy()  # an increase enforceable: its own Rule 62(a) stay, F + 30 (set by the ruling)

    # --- state at a day ---
    def cash_at(self, day: np.ndarray) -> np.ndarray:
        cum = self.cum()
        t = np.clip(day, 0, self.N - 1)
        return cum[self.rows, t]

    def owed_at(self, day: np.ndarray, enforceable: bool = False) -> np.ndarray:
        """The amount owed at the day; enforceable: an increase not yet out of its own Rule 62(a) stay is left out."""
        day = np.asarray(day)
        if self.d is None:
            return np.zeros(self.n, dtype=np.int64)
        since_entry = day - self.entry_ix() if self.has_judgment() else np.zeros(self.n)
        before = self.entered + interest_1961(self.entered, 0, since_entry, 0, self.bps)
        if self.cls_amount is None:
            out = before
        else:
            base = min(self.cls_amount, self.entered)
            after = self.cls_amount + interest_1961(base, self.cls_amount - base, since_entry, day - self.F, self.bps)
            after = after - np.where(day < self.fee_day, self.cls_fees, 0)  # fees are owed once quantified
            if enforceable and self.increase:
                after = np.where(day < self.EI, np.minimum(after, base + interest_1961(base, 0, since_entry, 0,
                                                                                       self.bps)), after)
            out = np.where(day >= self.F, after, before)
        if self.pending:  # nothing is owed before the modeled entry
            out = np.where(day < self.E_ix, 0, out)
        return np.where(day >= self.resolved, 0, np.maximum(out - self.taken_before(day), 0))

    def taken_before(self, day) -> np.ndarray:
        """Levied or paid toward the judgment before the day. A pending claim (4.1.0) dates each amount, so a decision
        walked before a later-dated levy reads the amount it owes that day; 4.0.0 counts every amount booked so far,
        as recorded."""
        if not self.pending:
            return self.taken
        day = np.broadcast_to(np.asarray(day), (self.n,))
        out = np.zeros(self.n, dtype=np.int64)
        for t, amt in self.takes:
            out += np.where(np.broadcast_to(t, (self.n,)) < day, amt, 0)
        return out


    # --- booking ---
    def book(self, arr: np.ndarray, day: np.ndarray, cents) -> None:
        day = np.asarray(day)
        cents = np.broadcast_to(np.asarray(cents, dtype=np.int64), day.shape)
        ok = (day >= 0) & (day < self.N) & (cents != 0)
        if ok.any():
            np.add.at(arr, (self.rows[ok], day[ok]), cents[ok])
            self._touch()

    def pay(self, day: np.ndarray, cents, kind: str, incurred=None) -> None:
        """Book event cash of one kind (KINDS) on the day per draw, into the cash and its kind; an obligation also
        records the day it was incurred (the order the daily processor clears the day's obligations in)."""
        day = np.asarray(day)
        cents = np.broadcast_to(np.asarray(cents, dtype=np.int64), day.shape)
        self.book(self.ev.cash, day, cents)
        self.book(self.ev.kinds[kind], day, cents)
        if incurred is not None:
            ok = (day >= 0) & (day < self.N) & (cents != 0)
            cur = self.ev.incurred[kind]
            self.ev.incurred[kind] = np.where(ok, np.minimum(cur, np.asarray(incurred, dtype=np.int64)), cur)

    def petition(self, day: np.ndarray, where: np.ndarray | None = None, cause: str = "enforcement") -> None:
        day = np.asarray(day) + int(self.p("petition_lag_days"))
        ok = (day >= 0) & (day < self.N) & (True if where is None else where)
        cur = self.ev.petition
        win = ok & ((cur < 0) | (day < cur))
        if win.any():
            self.ev.petition = np.where(win, day, cur)
            self._touch()
        self.pet_cause = np.where(win, PETITION_CAUSES.index(cause), self.pet_cause).astype(np.int8)

    def live(self, day: np.ndarray) -> np.ndarray:
        """No petition before the day and the judgment not resolved by it."""
        pet = np.where(self.ev.petition < 0, BIG, self.ev.petition)
        return (np.asarray(day) < pet) & (np.asarray(day) < self.resolved)

    def resolve(self, day: np.ndarray, where: np.ndarray) -> None:
        """The dispute ends (payment, settlement or vacatur; a petition zeroes the feed's cash after it): legal spend
        in the feed stops from that day."""
        day = np.where(where & (day < self.N), day, BIG)
        self.release_lock(day, where)
        old, self.resolved = self.resolved, np.minimum(self.resolved, day)
        t = np.arange(self.N)
        stop = (t[None, :] >= self.resolved[:, None]) & (t[None, :] < old[:, None])
        if stop.any():
            back = np.where(stop, self.basis.legal, 0)  # legal outflows are negative: adding them back
            self.ev.cash -= back
            self.ev.kinds["reduction"] -= back  # an outflow that stops, not a receipt
            self._touch()

    def levy(self, day: np.ndarray, lagged: bool = False) -> None:
        """A writ on the enforceable amount; where it comes before an increase is enforceable, a second
        writ on the increase once it is. lagged: the day already includes levy_lag_days."""
        day = np.asarray(day) + (0 if lagged else int(self.p("levy_lag_days")))
        self._take(day)
        if self.increase:
            self.restay()  # a first writ dated before an approval is in the security's balance
            self._take(np.where(day < self.EI, self.EI, BIG))

    def _take(self, day: np.ndarray) -> None:
        v = self.seen_at(day)  # a floor decision dated before the levy is in the cash it reaches
        ok = v.live(day) & (day < self.stayed_from) & (day < self.N)
        reach = v.processing_balance(day) if self.daily else v.cash_at(day)  # daily: the balance at processing
        take = np.where(ok, np.minimum(self.owed_at(day, enforceable=True), np.maximum(reach, 0)), 0)
        satisfied = (take > 0) & (take >= self.owed_at(day)) & (not self.retrial)  # the whole judgment, that day
        self.pay(day, -take, "levy")
        self.mark("levied", day, take > 0)
        self.taken += take
        self.takes.append((np.asarray(day).copy(), take))
        self.levied |= take > 0
        if self.pending:  # the dispute ends as a payment ends it (`resolve`): one rule for every booking that reads it
            self.resolve(np.asarray(day), satisfied)
            self.mark("paid", day, satisfied)
        self.writs.append((day, take))

    def flush_levy(self, rows: np.ndarray | None = None) -> None:
        """Book the pending pre-ruling levy. It waits one step so the debtor's response on the levy day acts
        first: a petition or a payment that day pre-empts it (`live`)."""
        if self.pending_levy is not None:
            day = self.pending_levy
            rows = np.ones(self.n, dtype=bool) if rows is None else rows
            rest = np.where(rows, BIG, day)
            self.pending_levy = None if (rest >= BIG).all() else rest
            self.levy(np.where(rows, day, BIG), lagged=True)

    def claimed(self) -> int:
        """Before a verdict: the most any verdict branch enters (the claimed amount), the settlement offer's cap."""
        return max(verdict_amount(self.d, self.m, b, self.sens) for b in pending_template(self.m)["verdict_branches"])

    def _enter(self) -> None:
        """The judgment's entry E per trajectory (judgment_entry): the next business day after V (base), or with the
        post-verdict ruling (V + the motion deadline + briefing + a lag draw); execution from E + 31 (Rule 62(a))."""
        if self.p("judgment_entry") == "next_business_day":
            nxt = {int(v): self.ix(business_days_after(self.s.review + timedelta(days=int(v) + 1), 1))
                   for v in np.unique(self.V)}
            self.E_ix = np.array([nxt[int(v)] for v in self.V], dtype=np.int64)
        else:
            self.E_ix = self.V + int(self.m["rules"]["frcp_50b_59_deadline"]["value"]) + int(
                self.p("briefing_days_new_motion")) + self.dr.lag(self.m, self.iid, "entry")
        self.e_ix = self.E_ix + int(self.m["rules"]["frcp_62a"]["value"]) + 1
        self.E0 = np.maximum(self.e_ix, 0)

    def respond(self, booking: str, day: np.ndarray, cause: str = "enforcement") -> None:
        """One branch's booking (contract branch_bookings) on the decision day: pay the amount owed, a petition, a
        mark that the company is seeking a transaction, or nothing. A new booking needs its code here."""
        N = self.N
        if booking == "pay":
            ok = self.live(day) & (day < N) & (self.cash_at(day) >= self.owed_at(day))
            amt = np.where(ok, self.owed_at(day), 0)
            self.pay(day, -amt, "judgment", incurred=self.entry_ix())
            self.taken += amt
            self.takes.append((np.asarray(day).copy(), amt))
            self.resolve(day, ok & (not self.retrial))  # under a new trial the dispute goes on
            self.release_lock(day, ok)  # paid in full: nothing left to secure
            self.mark("paid", day, ok)
        elif booking == "petition":
            where = day < N if cause == "cash_floor" else self.live(day) & (day < N)  # the floor: as 4.0.0
            self.petition(day, where, cause=cause)
        elif booking == "seek":
            self.mark("seeking", day, self.live(day))
        elif booking == "raise":
            self.raise_equity(day)
        elif booking != "none":
            raise ValueError(f"No Chain booking {booking!r} (contract branch_bookings)")

    def adverse_standing(self, day: np.ndarray) -> np.ndarray:
        """Whether a money judgment on an adverse verdict branch stands on the day: entered, not set aside after
        trial, and not satisfied or released (payment or settlement resolves the dispute). A settled claim is not
        an adverse judgment."""
        day = np.asarray(day)
        return (day >= self.adverse_from) & (day < self.adverse_until) & (day < self.resolved)

    def raise_available(self, day: np.ndarray) -> np.ndarray:
        """The equity the company can raise on the day (case inputs): raise_capacity_after_adverse_judgment while
        an adverse money judgment stands on the path (adverse_standing), raise_capacity everywhere else; 0 where the
        case sets none, after a petition or outside the period. The dispute's own resolution does not matter."""
        if not self.raising:
            return np.zeros(self.n, dtype=np.int64)
        day = np.asarray(day)
        amt = np.where(self.adverse_standing(day), int(self.p("raise_capacity_after_adverse_judgment")),
                       int(self.p("raise_capacity")))
        pet = np.where(self.ev.petition < 0, BIG, self.ev.petition)
        return np.where((day < pet) & (day >= 0) & (day < self.N), amt, 0).astype(np.int64)

    def raise_equity(self, day: np.ndarray) -> None:
        """The raise booking: the amount available, in equal daily amounts over raise_days from the decision day (the
        remainder on the first day); days past the period fall outside it."""
        amt = self.raise_available(day)
        n = int(self.p("raise_days"))
        each = amt // n
        for k in range(n):
            self.pay(np.asarray(day) + k, each + (amt - each * n if k == 0 else 0), "inflow")
        self.mark("raised", day, amt > 0)

    def settle(self, start: np.ndarray, end: np.ndarray, agreed: bool = True, cap: int | None = None
               ) -> tuple[np.ndarray, np.ndarray]:
        """Settlement: available cash less 30-day need, floored at 0 and capped at the amount owed (`settle_offer`),
        the settlement date interval start + 30 days (sensitivity: the interval's end). A settlement exists only where
        that amount is positive; it bounds what can be paid (spec §16.3), and the terms (settlement_terms) say how it
        is paid. Installments (the 14 May case): equal monthly payments from the settlement date, those after the
        horizon outside it, none after a petition (run); the claim is released on the settlement date. Lump sum: one
        payment, released on payment. Monthly (4.0.0 sensitivity): payments to the horizon, released on the last.
        Returns the settlement date and where a settlement exists."""
        pd = np.asarray(end) if self.sens.get("settlement_date_in_interval") else np.asarray(start) + int(
            self.m["parameters"]["settlement_date_in_interval"]["value"])
        v = self.seen_at(pd, levy=True)  # what is dated before the settlement date is in the cash it reads
        ok = v.live(pd) & (pd < self.N)
        need = self.basis.need[self.rows, np.clip(pd, 0, self.N - 1)]
        owed = v.owed_at(pd) if cap is None else np.full(self.n, cap, dtype=np.int64)
        bound = np.where(ok, np.clip(v.cash_at(pd) - need, 0, owed), 0).astype(np.int64)
        self.settle_offer = bound
        ok = ok & (bound > 0)
        if not agreed:
            return pd, ok
        release = pd
        mode, count = settlement_terms(self.m, self.sens)
        if mode == "installments":  # the case's terms: the agreement releases the claim on the settlement date
            for i in range(count):
                part = np.where(ok, bound // count + (bound % count if i == count - 1 else 0), 0)
                self.pay(self.months_after(pd, i), -part, "settlement", incurred=pd)  # after the period: outside it
        elif mode == "monthly":
            k = np.maximum((self.N - pd + 29) // 30, 1)
            for i in range(int(k.max())):
                part = np.where(ok & (i < k), bound // k + np.where(i == k - 1, bound % k, 0), 0)
                self.pay(pd + 30 * i, -part, "settlement", incurred=pd)
            release = pd + 30 * (k - 1)
        else:
            self.pay(pd, -np.where(ok, bound, 0), "settlement", incurred=pd)
        self.resolve(release, ok & self.live(release))
        self.mark("settled", pd, ok)
        return pd, ok

    def months_after(self, day: np.ndarray, k: int) -> np.ndarray:
        """The day index `k` calendar months after each day index (a day past the period stays as it is)."""
        day = np.asarray(day)
        at = {int(x): (add_months(self.s.review + timedelta(days=int(x) + 1), k) - self.s.review).days - 1
              if x < self.N else int(x) for x in np.unique(day)}
        return np.array([at[int(x)] for x in day.ravel()], dtype=np.int64).reshape(day.shape)

    def stay_security(self, motion: np.ndarray, key: str, approved: bool) -> np.ndarray:
        """Rule 62(b), effective on approval (motion + briefing + a lag draw). Where the company's cash at approval
        covers the bond collateral and its 30-day operating need, the collateral is locked. Elsewhere the company
        proposes reduced security, posted on approval: its available cash above its 30-day operating need on the
        approval day (`stay_offer`, what the court is told). If approved, that amount is locked. Where the company has
        no cash above its need that day, the stay is effective only under stay_security = noncash (security or a
        waiver not in cash; nothing locked). The lock is released when the dispute ends (`release_lock`).
        Under daily processing (QUESTIONS §2.5) the security is sized on the approval day's balance after every event
        dated before it, whatever the walk order, never above that balance and never on or after a petition:
        `_size_stay` sizes it here, and `restay` re-sizes it after every later booking."""
        approval = motion + int(self.p("briefing_days_new_motion")) + self.dr.lag(self.m, self.iid, key)
        if self.daily:  # sized on the approval day whatever the walk order (`restay`)
            if approved:
                self.mark("stay_moved", motion, self.live(motion))
            st = {"approval": approval, "approved": approved, "stayed_from": self.stayed_from.copy(),
                  "mark": self.marks["stayed"].copy(), "triggers": self.trigger_days()}
            self.stays[len(self.rec[0])] = st
            self._size_stay(st, read=True)
            return approval
        v = self.seen_at(approval, levy=True)  # what is dated before the approval is in the cash it reads
        collateral = v.bond_collateral(approval)
        cash_a = v.cash_at(approval)
        need_a = self.basis.need[self.rows, np.clip(approval, 0, self.N - 1)]
        covers = cash_a - need_a >= collateral
        offer = np.maximum(cash_a - need_a, 0)
        self.stay_offer = np.where(v.live(approval) & (approval < self.N) & ~covers, offer, 0).astype(np.int64)
        self.collateral_required = collateral
        if not approved:
            return approval
        posts = ~covers & (self.stay_offer > 0)
        lock = np.where(covers, collateral, np.where(posts, self.stay_offer, 0))
        noncash = self.p("stay_security") == "noncash"
        effective = v.live(approval) & (covers | posts | noncash)
        self.mark("stay_moved", motion, self.live(motion))
        self.mark("stayed", approval, effective)
        self.stayed_from = np.minimum(self.stayed_from, np.where(effective & (approval < self.N), approval, BIG))
        self.lock_amount = np.where(effective, lock, 0).astype(np.int64)
        self.lock_day = np.where(self.lock_amount > 0, approval, BIG)
        self.book(self.ev.lock, approval, self.lock_amount)
        return approval

    def release_lock(self, day: np.ndarray, where: np.ndarray) -> None:
        """The dispute ends on the day (vacatur, new trial, settlement or payment): the stay's security is released
        that day. Where the approval falls on or after it there is nothing left to stay, and no lock is booked."""
        day = np.broadcast_to(np.asarray(day, dtype=np.int64), (self.n,))
        if self.daily:  # the stay's lock is re-sized with its release (`restay`)
            ends = np.asarray(where, dtype=bool) & (day < self.N)
            self.release_at = np.where(ends, np.minimum(self.release_at, day), self.release_at)
            self.restay(force=True)
            return
        held = np.asarray(where, dtype=bool) & (self.lock_amount > 0) & (day < self.N)
        if not held.any():
            return
        late = held & (self.lock_day >= day)
        self.book(self.ev.lock, np.where(late, self.lock_day, day), -np.where(held, self.lock_amount, 0))
        self.marks["stayed"] = np.where(late, BIG, self.marks["stayed"])
        self.lock_amount = np.where(held, 0, self.lock_amount)
        self.lock_day = np.where(held, BIG, self.lock_day)

    def _size_stay(self, st: dict, read: bool) -> None:
        """Daily processing (QUESTIONS §2.5): size one walked stay's security on its approval day, on the balance after
        every event dated before it (the waiting decisions and the pending levy dated before it included, `seen_at`),
        with its own lock and release taken out first. Full collateral where the balance less the month's need covers
        it, else the balance above the need; never more than the balance, and nothing on or after a petition or once
        the dispute has ended. An approved stay books the lock on approval and its release when the dispute ends.
        `read`: the walked step's own read (its `stay_offer`, collateral and cash-read day)."""
        approval = st["approval"]
        if "lock" in st:  # what it booked before
            self.book(self.ev.lock, approval, -st["lock"])
            self.book(self.ev.lock, st["rel"], np.where(st["held"], st["lock"], 0))
        reads = self.reads
        v = self.seen_at(approval, levy=True)
        self.reads = self.reads if read else reads
        collateral = v.bond_collateral(approval)
        cash_a = v.cash_at(approval)
        need_a = self.basis.need[self.rows, np.clip(approval, 0, self.N - 1)]
        covers = cash_a - need_a >= collateral
        live = v.live(approval) & (approval < self.N)
        offer = np.where(live & ~covers, np.maximum(cash_a - need_a, 0), 0).astype(np.int64)
        st.update(day=approval, cash=cash_a, owed=v.owed_at(approval), collateral=collateral, stay_offer=offer,
                  petition=v.ev.petition.copy())
        if read:
            self.stay_offer, self.collateral_required = offer, collateral
        if not st["approved"]:
            return
        effective = live & (covers | (offer > 0) | (self.p("stay_security") == "noncash"))
        lock = np.where(effective & covers, collateral, np.where(effective, offer, 0)).astype(np.int64)
        self.stayed_from = np.minimum(st["stayed_from"], np.where(effective, approval, BIG))
        rel = self.release_at
        held = (lock > 0) & (rel < self.N)
        late = held & (approval >= rel)  # the dispute ended by approval: nothing left to stay
        self.marks["stayed"] = st["mark"].copy()
        self.mark("stayed", approval, effective & ~late)
        st.update(lock=lock, held=held, rel=np.where(late, approval, rel))
        self.book(self.ev.lock, approval, lock)
        self.book(self.ev.lock, st["rel"], -np.where(held, lock, 0))
        self.lock_amount = np.where(held, 0, lock)
        self.lock_day = np.where(self.lock_amount > 0, approval, BIG)

    def restay(self, force: bool = False) -> None:
        """Daily processing: re-size every approved stay walked so far (`_size_stay`) once anything has been booked since
        the last re-sizing, so a step walked later but dated before an approval (a levy, a petition, a floor decision)
        is in the balance its security is sized on. The processor is causal: one pass settles it."""
        if not self.daily or not self.stays or self._restaying or (self._stay_cv == self._cv and not force):
            return
        self._restaying = True
        try:
            for st in self.stays.values():
                if st["approved"]:
                    self._size_stay(st, read=False)
        finally:
            self._restaying = False
        self._stay_cv = self._cv

    def bond_collateral(self, approval: np.ndarray) -> np.ndarray:
        """The bond (the path judgment plus §1961 interest over the appeal) times the collateral share."""
        years = self.p("bond_forward_interest_years")
        owed = self.owed_at(approval)
        bond = owed + np.rint(owed * self.bps / 10_000 * years).astype(np.int64)
        share = (self.s.collateral_share[0] if self.s.collateral_share else
                 (self.m["parameters"]["bond_collateral_share_bps"]["lower"] if self.sens.get("bond_collateral_share_bps")
                  else self.m["parameters"]["bond_collateral_share_bps"]["value"]) / 10_000)
        return np.rint(bond * share).astype(np.int64)

    def book_default(self, ctx: str, branch: str, rows: np.ndarray) -> np.ndarray:
        """The judgment default on `rows`: it ripens; the holders give notice and accelerate (+ holder_notice_lag_days);
        the issuer files on acceleration, or the holders file once §7.06 allows (holder_petition_route), or neither.
        Returns the ripe day (BIG where it does not ripen)."""
        ripe, cond = self.judgment_default(ctx)
        cond = cond & rows
        accel = ripe + int(self.p("holder_notice_lag_days"))
        if branch in ("yes", "holders_file", "accelerated"):
            self.mark("notes_due", accel, cond)
            if ctx == "I1":
                self.jd_acted |= cond
        self.coupon_when_due()
        if branch == "yes":
            self.petition(accel, cond, cause="notes")
        elif branch == "holders_file":
            self.petition(accel + self.holder_route_days(), cond, cause="notes")
        return np.where(cond, ripe, BIG)

    def judgment_default(self, ctx: str) -> tuple[np.ndarray, np.ndarray]:
        """§7.01(i) ripe date and where it ripens (rule indenture_final_judgment; parameter judgment_default_reading).
        I1, the judgment as entered: 60 days from the end of the Rule 62(a) stay. post: 60 days from the order
        disposing of the last pending tolling motion (A), on the path amount (the entered amount where no ruling step
        set one), where the holders did not act on the judgment as entered. Either needs the amount above the
        threshold net of insurance, unpaid, not effectively stayed by the ripe date, and no earlier petition."""
        f, reading = self.fin, self.p("judgment_default_reading")
        full = np.full(self.n, 0, dtype=np.int64)
        if f is None or not f.judgment_default_days or self.d is None:
            return full + BIG, np.zeros(self.n, dtype=bool)
        if ctx == "I1":
            ripe = full + self.e_ix + f.judgment_default_days
            amount, on = self.entered, reading in ("both", "entered")
            cond = np.full(self.n, on and self.has_judgment())
        else:
            ripe = np.maximum(self.A, self.e_ix) + f.judgment_default_days
            amount, on = (self.entered if self.cls_amount is None else self.cls_amount), reading in ("both",
                                                                                                    "post_ruling")
            cond = np.full(self.n, on) & (self.A >= 0) & ~self.jd_acted
        cond = cond & (amount - f.insured_cents > f.judgment_default_threshold_cents)
        cond = cond & (self.stayed_from > ripe) & self.live(ripe) & (self.owed_at(ripe) > 0)
        return ripe, cond

    def tau(self) -> np.ndarray:
        """The first day available cash falls below operating need (sensitivity: below zero), or BIG."""
        memo = self.__dict__.get("_tau")
        if memo is not None and memo[0] == self._cv:
            return memo[1].copy()
        cum = self.cum()
        if self.daily and self.sens.get("cash_floor"):  # cash is never below zero: nil with an obligation unpaid
            out = self.processed()[1].copy()
            self._tau = (self._cv, out)
            return out.copy()
        floor = np.zeros_like(cum) if self.sens.get("cash_floor") else self.basis.need
        below = cum < floor
        out = np.where(below.any(axis=1), below.argmax(axis=1), BIG)
        self._tau = (self._cv, out)
        return out.copy()

    def cash_out(self) -> np.ndarray:
        """The first day available cash falls below zero, or BIG. Where the floor is already zero (sensitivity) the
        company decided at that day, so nothing further arises."""
        if self.sens.get("cash_floor"):
            return np.full(self.n, BIG)
        memo = self.__dict__.get("_out")
        if memo is not None and memo[0] == self._cv:
            return memo[1].copy()
        if self.daily:  # the first obligation the processor could not pay
            out = self.processed()[1].copy()
            self._out = (self._cv, out)
            return out.copy()
        cum = self.cum()
        below = cum < 0
        out = np.where(below.any(axis=1), below.argmax(axis=1), BIG)
        self._out = (self._cv, out)
        return out.copy()

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
                "panel_decision": self.ix(panel),
                "delisted_suspension": self.ix(suspension) + (10 if f25 else 0),
                "delisted_panel": self.ix(panel) + f25}

    def repurchase_day(self, delist):
        """The Fundamental Change repurchase date for a delisting day (a day index, or an array of them)."""
        if np.ndim(delist):
            days = {int(x): self.repurchase_day(int(x)) for x in np.unique(delist)}
            return np.array([days[int(x)] for x in delist], dtype=np.int64)
        when = self.s.review + timedelta(days=int(delist) + 1)
        if self.fin.repurchase_business_days is None or self.fin.repurchase_notice_business_days is None:
            raise ValueError(f"{self.fin.instrument_id}: the repurchase terms are unknown; its quoted terms must give "
                             "the notice and the repurchase window")
        notice = self.fin.repurchase_notice_business_days
        lo, hi = self.fin.repurchase_business_days
        if self.p("repurchase_date") == "earliest":
            return self.ix(business_days_after(when, lo))
        return self.ix(business_days_after(when, notice + hi))

    def holder_route_days(self) -> int:
        """Days from acceleration to the holders' involuntary petition (holder_petition_route): under §7.06 the
        holders' written request to the trustee, made at acceleration, plus 60 days; immediate in the sensitivity."""
        p = self.m["parameters"]["holder_petition_route"]
        return int(p["request_days"]) if self.p("holder_petition_route") == p["value"] else 0

    def coupon_cash_cents(self) -> int:
        """The cash part of one coupon (parameter coupon_cash_share): base, shares up to the share capacity at the
        share value (95% of the price, §16.02(c)) and the rest in cash; sensitivities all cash or all shares. A split
        ratio scales the price up and the capacity down by the same factor (none in the record: ratio 1)."""
        c, p = self.fin.coupon_cents, self.m["parameters"]["coupon_cash_share"]
        mode = self.sens.get("coupon_cash_share") or p["value"]
        if mode is True or mode == "all_cash":
            return c
        if mode == "all_shares":
            return 0
        ratio = p.get("split_ratio") or 1
        capacity = min(p["share_capacity"], p["share_limit"]) // ratio
        covered = capacity * p["share_price_cents"] * ratio * p["share_value_bps"] // 10_000
        return c - min(c, covered)

    def instrument_cash(self) -> None:
        """The notes' coupon, paid on the next business day after each interest date (coupon_cash_cents), and the
        CHIPS credit (base $0; sensitivity prorated over the horizon). The petition zeroes both after it (run); notes
        already due on the payment day pay no separate coupon (coupon_when_due)."""
        if self.fin is not None and self.fin.coupon_cents is None and self.fin.kind == "convertible_notes":
            raise ValueError(f"{self.fin.instrument_id}: the coupon is unknown; its quoted terms must give it")
        if self.fin is not None and self.fin.coupon_cents:
            cash = self.coupon_cash_cents()
            for d in self.fin.interest_dates:
                day = self.ix(next_business_day(d))
                self.pay(np.full(self.n, day), -cash, "notes_interest", incurred=INCURRED_BEFORE)
                if cash and 0 <= day < self.N:
                    self.coupons.append((day, cash, np.ones(self.n, dtype=bool)))
        if self.ordinary:  # the dispute ends on the review date at no cost: its legal spend stops (`resolve`)
            self.resolve(np.zeros(self.n, dtype=np.int64), np.ones(self.n, dtype=bool))
        chips = int(self.p("chips_credit_cents"))
        if chips:
            per = np.full(self.N, chips // self.N, dtype=np.int64)
            per[-1] += chips - per.sum()
            self.ev.cash += per[None, :]
            self.ev.kinds["inflow"] += per[None, :]
            self._touch()


    def coupon_when_due(self) -> None:
        """Notes accelerated (or their repurchase due) on or before an interest payment day: the amount due already
        carries the accrued interest, so no separate coupon is paid that day."""
        due = self.marks["notes_due"]
        for day, cash, kept in self.coupons:
            gone = kept & (due <= day)
            self.pay(np.full(self.n, day), np.where(gone, cash, 0), "notes_interest")
            kept &= ~gone

    # --- steps ---
    def step(self, node: str, ctx: str, branch: str) -> np.ndarray:
        """Book one step's effects; return its decision day per draw (BIG where it never arises)."""
        N, full = self.N, (lambda v: np.full(self.n, v, dtype=np.int64))
        if not answers_levy(node, ctx) and not self.waiting:  # with floor decisions waiting, `advance` orders it
            self.flush_levy()
        if node == "settle":
            if ctx == "I0":  # before the verdict: nothing is owed yet; the offer is capped at the claimed amount
                start, end = full(-1), self.V
            else:
                e1 = self.E_ix if self.pending else full(-1)
                # I4 after the ruling: a pending claim's stay approved before it (I1) opens no window before it
                i4 = np.maximum(self.stayed_from, self.F) if self.pending else self.stayed_from
                start = {"I1": e1, "I2": self.F, "I3": np.maximum(self.EF, self.AD), "I4": i4}[ctx]
                end = {"I1": self.F, "I2": self.AD, "I3": full(N - 1), "I4": full(N - 1)}[ctx]
            start = np.maximum(start, -1)  # an interval that began before the review date runs from it
            cap = self.claimed() if ctx == "I0" else None
            self.settle(start, end, agreed=branch == "yes", cap=cap)  # a settlement releases the stay's security
            return np.where(end < 0, BIG, np.maximum(start, 0))  # a closed interval asks nothing
        if node == "execute_pre_ruling":
            self.q1 = branch == "yes"
            if self.q1:
                self.mark("executing", self.E0)
            return full(self.E0)
        if node == "stay":
            motion = full(self.E0) if ctx == "I1" else np.maximum(self.F, 0)
            self.stay_security(motion, f"stay_{ctx}", approved=branch == "yes")
            return motion
        if node == "court_order":
            # a probe that books nothing: the day the court rules on a motion (ctx "stay_I1" / "stay_post": the stay's
            # approval, with its collateral and reduced security measured that day; "registration_I1" /
            # "registration_post": the early registration order), dated as the step that books it dates it
            kind, phase = ctx.split("_")
            if kind == "stay":
                return self.stay_security(full(self.E0) if phase == "I1" else np.maximum(self.F, 0), f"stay_{phase}",
                                          approved=False)
            motion = full(self.E0) if phase == "I1" else self.EF  # post: the creditor moves once the ruling is enforceable
            return motion + int(self.p("briefing_days_new_motion")) + self.dr.lag(self.m, self.iid, f"registration_{phase}")
        if node in RESPONSES:
            milestone = self.response_day(ctx)
            self.respond(self.bookings[node][branch], milestone)
            return milestone  # the levy is booked at the next step (or the path's end): the response comes first
        if node == "registration_early":
            motion = full(self.E0) if ctx == "I1" else self.F
            order = motion + int(self.p("briefing_days_new_motion")) + self.dr.lag(self.m, self.iid, f"registration_{ctx}")
            if branch == "yes":
                self.early_registration = np.minimum(self.early_registration, order)
                if ctx == "I1":  # booked at the next step, after the debtor's response on the levy day
                    self.pending_levy = order + int(self.p("levy_lag_days"))
                else:
                    self.levy(order)
            return motion
        if node == "judgment_default":
            return self.book_default(ctx, branch, np.ones(self.n, dtype=bool))
        if node == "ruling":
            self.retrial = branch == "retrial" or branch.endswith(":retrial")
            if branch in ("none", "retrial"):
                self.cls_amount = 0
            else:
                _, total, fees = branch.split(":")[:3]
                self.cls_amount, self.cls_fees = int(total), int(fees)
            if branch == "none":  # vacated: the dispute ends on the ruling, and legal spend stops
                self.resolve(np.maximum(self.F, 0), self.live(self.F))
            elif branch == "retrial":  # no money award survives: the stay secures nothing from the ruling
                self.release_lock(np.maximum(self.F, 0), self.live(self.F))
            self.mark("ruled", self.F)
            self.increase = max(self.cls_amount - self.entered, 0)  # cls_amount is set on every branch above
            restart = self.m["parameters"]["stay_restart_on_increase_days"]
            mode = restart["sensitivity"] if self.sens.get("stay_restart_on_increase_days") else restart["base"]
            self.EI = self.F + (int(restart["value"]) if self.increase else 0)
            self.EF = self.EI.copy() if mode == "whole_amount" else self.F.copy()  # base: the increase has its own 30-day stay
            return self.F
        if node == "appeal":
            self.appealed = branch == "yes"
            day = np.where(self.AD < 0, BIG, np.maximum(self.F, 0))  # the time to appeal has run: nothing to ask
            if self.appealed:
                self.mark("appealed", day, self.live(day))
            return day
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
                # booked at the next step, after the company's response on the levy day
                self.pending_levy = np.maximum(day, 0) + int(self.p("levy_lag_days"))
            return np.maximum(self.EF, 0)
        if node in ("listing", "listing_date"):
            # the listing chain runs where no acceleration precedes the board's vote call
            # listing_kept (ctx "kept", listing_route): one company decision on the hearing-request day
            dates = self.listing_dates()
            gate = dates["hearing_request"] if ctx == "kept" else dates["vote_call"]
            on = self.marks["notes_due"] > gate
            if node == "listing_date":  # a listing question's own decision date (ctx: the listing_dates key)
                return np.where(on, dates["hearing_request" if ctx == "kept" else ctx], BIG)
            if branch.startswith("delisted"):
                self.delisted = np.where(on, dates[branch], BIG)
                self.mark("delisted", self.delisted)
                return np.where(on, gate if ctx == "kept" else dates["determination"], BIG)
            return np.where(on, gate, BIG)
        if node == "delisting_notes":
            # delisting is an Event of Default and a Fundamental Change, except where the notes are already due
            # the notes' default and repurchase do not depend on the dispute: a pending claim's (4.1.0) opens wherever no
            # petition precedes the delisting, the dispute ended or not; 4.0.0 as recorded (only while it is live)
            pet = np.where(self.ev.petition < 0, BIG, self.ev.petition)
            alive = self.delisted < pet if self.pending or self.ordinary else self.live(self.delisted)
            open_ = alive & (self.marks["notes_due"] > self.delisted) & (self.delisted < N)
            accel = self.delisted + int(self.p("holder_notice_lag_days"))
            rep = full(BIG)
            rep[self.delisted < N] = self.repurchase_day(self.delisted[self.delisted < N])
            if branch.startswith("petition_delist") or branch == "accelerated":
                self.mark("notes_due", accel, open_)
            elif branch.startswith("petition_repurchase") or branch == "repurchase_unpaid":
                self.mark("notes_due", rep, open_)
            self.coupon_when_due()
            if branch == "petition_delist":
                self.petition(accel, open_, cause="notes")
            elif branch == "petition_delist_holders":
                self.petition(accel + self.holder_route_days(), open_, cause="notes")
            elif branch == "petition_repurchase":
                self.petition(rep, open_, cause="notes")
            elif branch == "petition_repurchase_holders":
                self.petition(rep + self.holder_route_days(), open_, cause="notes")
            return np.where(open_, self.delisted, BIG)
        if node == "notes_due_date":
            # a probe that books nothing: the day the issuer may file on notes due and unpaid (ctx "issuer"), or the
            # earliest day the holders may file (ctx "holders": holder_petition_route); and whether the post-trial
            # motions are ruled on by then
            due = self.marks["notes_due"]
            self.mark("ruled", self.F)
            return np.where(due < BIG, due + (self.holder_route_days() if ctx == "holders" else 0), BIG)
        if node in FLOOR_NODES:  # booked on every trajectory now (`advance` books it on its own day instead)
            t = self.tau() if node == "cash_floor" else self.cash_out()
            self.raise_offer = self.decide_floor(node, branch, t)
            return t
        if node == "verdict":
            spec = pending_template(self.m)["verdict_branches"][branch]
            if spec["judgment"]:
                self.entered = verdict_amount(self.d, self.m, branch, self.sens)
                self._enter()
                if spec.get("adverse"):
                    self.adverse_from = self.E_ix.copy()
            return self.V.copy()
        if node == "post_trial_motions":
            filed = self.E_ix + int(self.m["rules"]["frcp_50b_59_deadline"]["value"])
            notice = int(self.m["rules"]["frap_4a1a"]["value"])
            if branch == "yes":  # one common lag for all the motions (sensitivity: the later of two draws)
                lag = self.dr.lag(self.m, self.iid, "common")
                if self.sens.get("ruling_lag_days"):
                    lag = np.maximum(lag, self.dr.lag(self.m, self.iid, "second"))
                self.F = filed + int(self.p("briefing_days_new_motion")) + lag
            else:  # final as entered: the time to appeal runs from entry
                self.F = self.E_ix.copy()
                self.mark("ruled", filed)
            self.AD = self.F + notice
            self.A, self.EF, self.EI = self.F.copy(), self.F.copy(), self.F.copy()
            return filed
        if node == "post_trial_ruling":
            if branch == "set_aside":  # no money judgment on the path; the dispute goes on (legal spend too)
                self.cls_amount, self.retrial = 0, True
                self.adverse_until = np.minimum(self.adverse_until, self.F)  # no adverse judgment stands after it
                self.release_lock(np.maximum(self.F, 0), self.live(self.F))
            self.mark("ruled", self.F)
            self.increase = 0
            self.EF, self.EI = self.F.copy(), self.F.copy()
            return self.F.copy()
        raise ValueError(f"Unknown chain step {node}")

    def run(self, steps) -> Trace:
        self.instrument_cash()
        tr = Trace(self.ev)
        for node, ctx, branch in steps:
            self.advance(tr, node, ctx, branch)
        return self.finish(tr)

    def advance(self, tr: Trace, node: str, ctx: str, branch: str) -> None:
        """One step of `run`: book it and record its decision day and path facts. The cash floor and cash exhaustion
        (FLOOR_NODES) are state-triggered: walked here, they book on each trajectory on their own day (`upto`), after
        every step dated before it and before every step dated after it, whatever the walk order."""
        if not self.waits(node, ctx) and not self.waiting and not answers_levy(node, ctx):
            self.flush_levy()  # an earlier levy is in the cash the next decision sees
        self.settle_offer = np.zeros(self.n, dtype=np.int64)
        self.stay_offer = np.zeros(self.n, dtype=np.int64)
        self.raise_offer = np.zeros(self.n, dtype=np.int64)
        self.reads = np.full(self.n, -1, dtype=np.int64)
        if self.waits(node, ctx):  # it waits for its own day; a pending levy keeps waiting too
            i = len(self.rec[0])
            for lst, v in zip(self.rec, (np.full(self.n, BIG, dtype=np.int64), *(np.zeros(self.n, dtype=np.int64)
                                                                                  for _ in range(3))), strict=True):
                lst.append(v)
            self.late[i] = {"petition": self.ev.petition.copy(), "triggers": {},
                            "raise_offer": np.zeros(self.n, dtype=np.int64)}
            self.waiting.append([i, node, branch, np.zeros(self.n, dtype=bool), ctx])
            self.wctx[i] = ctx
            return
        if node == "notes_due_date" and self.waiting:  # a probe on the dates a waiting judgment default sets
            self.until(np.full(self.n, self.N, dtype=np.int64))
        if self.waiting:  # what is dated before this decision comes first, on each trajectory where it arises
            probe = self.clone()
            probe.waiting = []
            d = probe.step(node, ctx, branch)
            self.until(np.where(d < self.N, d, -1))
            if not answers_levy(node, ctx) and self.pending_levy is not None:  # unless a floor decision or the
                # response on the levy day, still waiting, precedes it
                self.flush_levy(~(self.next_floor() < self.pending_levy) & ~self.response_waiting())
        self.restay()  # what was booked since (a levy, a floor decision) is in a walked stay's security
        before_cash = self.cum()
        day = self.step(node, ctx, branch)
        t = np.clip(day, 0, self.N - 1)
        for lst, v in zip(self.rec, (day, before_cash[self.rows, t], self.owed_at(day), self.collateral_required.copy()),
                          strict=True):
            lst.append(v)
        self.restay()

    def response_day(self, ctx: str) -> np.ndarray:
        """The day the company responds (BIG: no response on that trajectory)."""
        N, full = self.N, (lambda v: np.full(self.n, v, dtype=np.int64))
        lv = self.pending_levy if self.pending_levy is not None else full(BIG)
        if ctx == "entry":  # a pending claim's entered judgment, on the day of entry
            milestone = np.where(self.has_judgment(), self.E_ix, BIG)
        elif ctx == "I1":  # the levy the early-registration order makes possible, where it
            # comes before stay approval and before the ruling; nowhere else does an act reach cash before it
            milestone = np.where((lv < self.stayed_from) & (lv < self.F), lv, BIG)
        elif ctx == "post":  # the day the creditor's levy after the ruling falls, before it is booked
            milestone = np.where((lv < self.stayed_from) & (lv < N), lv, BIG)
        else:  # "ripe": after seeking a sale or financing, the post-ruling judgment default's ripe date
            ripe, cond = self.judgment_default("post")
            milestone = np.where(cond, ripe, BIG)
        milestone = np.where(self.owed_at(milestone) > 0, milestone, BIG)  # nothing owed: no response arises
        return milestone

    def decide_waiting(self, i: int, node: str, branch: str, t: np.ndarray) -> np.ndarray:
        """Book waiting step i on day t (BIG: not on that trajectory); returns the equity available at a floor."""
        if node in FLOOR_NODES:
            return self.decide_floor(node, branch, t)
        if node == "judgment_default":
            self.book_default(self.wctx[i], branch, t < BIG)
        else:
            self.respond(self.bookings[node][branch], t)
        return np.zeros(self.n, dtype=np.int64)

    def decide_floor(self, node: str, branch: str, t: np.ndarray) -> np.ndarray:
        """The company's decision at the cash floor, or when cash runs out, booked on day t (BIG: not on that
        trajectory). Returns the equity available at the floor (0 where the decision is petition_cash_floor)."""
        q = "financing_at_floor" if node == "cash_floor" and self.raising else FLOOR_NODES[node]
        amt = self.raise_available(t) if q == "financing_at_floor" else np.zeros(self.n, dtype=np.int64)
        self.respond(self.bookings[q][branch], t, cause="cash_floor")
        return amt

    def waits(self, node: str, ctx: str) -> bool:
        """The decisions booked on their own day on each trajectory, whatever the walk order: the cash floor and cash
        exhaustion, and the company's response on the post-ruling levy day, which the walk may ask before the I3
        settlement window that precedes it on some trajectories, and the notes' judgment default (the walk asks the
        pre-ruling one before a ruling that can set the judgment aside first). A pending claim (4.1.0) only: 4.0.0
        books each step as it is walked, the floor last, as recorded."""
        if self.ordinary:
            return node in FLOOR_NODES
        return self.pending and (node in FLOOR_NODES or node == "judgment_default" or (node in RESPONSES
                                                                                     and ctx == "post"))

    def waiting_day(self, node: str, ctx: str) -> np.ndarray:
        """A waiting non-floor step's day as of now (BIG: it does not arise on that trajectory)."""
        if node == "judgment_default":
            ripe, cond = self.judgment_default(ctx)
            return np.where(cond, ripe, BIG)
        return self.response_day(ctx)

    def response_waiting(self) -> np.ndarray:
        """Per draw, whether a waiting levy-day response is not yet booked (the levy waits for it)."""
        out = np.zeros(self.n, dtype=bool)
        for _, node, _, done, ctx in self.waiting:
            if node in RESPONSES:
                out |= ~done & (self.response_day(ctx) < BIG)
        return out

    def upto(self, before: np.ndarray | None, every: bool = False, levy: np.ndarray | None = None) -> None:
        """Book the waiting state-triggered decisions, in walk order, on each trajectory whose own day falls before
        `before` (every: on every trajectory left). A day's cash reads nothing booked after it, so a floor dated before
        `before` is final there; its facts (day, cash, owed, collateral, earlier petitions, triggers) are its own day's."""
        if not self.waiting or (before is None and not every):
            return False
        moved = False
        prior = np.ones(self.n, dtype=bool)
        for i, node, branch, done, ctx in self.waiting:
            floor = node in FLOOR_NODES
            t = (self.tau() if node == "cash_floor" else self.cash_out()) if floor else self.waiting_day(node, ctx)
            # a waiting step dated after the pending levy (levy: its day) books after it; the response on the levy
            # day comes before it
            lim = before if levy is None or node in RESPONSES else np.minimum(before, levy)
            fire = (prior if floor else True) & ~done & (True if every else t < lim)
            if fire.any():
                ti = np.clip(t, 0, self.N - 1)
                vals = (t, self.cum()[self.rows, ti], self.owed_at(t), self.collateral_required)
                for lst, v in zip(self.rec, vals, strict=True):
                    lst[i] = np.where(fire, v, lst[i])
                late = self.late[i]
                late["petition"] = np.where(fire, self.ev.petition, late["petition"])
                late["triggers"] = {k: np.where(fire, v, late["triggers"].get(k, BIG))
                                    for k, v in self.trigger_days().items()}
                amt = self.decide_waiting(i, node, branch, np.where(fire, t, BIG))
                late["raise_offer"] = np.where(fire, amt, late["raise_offer"])
                done |= fire
                moved = True
            if floor:
                prior = done.copy()  # a later floor decision books only after the earlier one
        self.waiting = [w for w in self.waiting if not w[3].all()]
        return moved

    def seen_at(self, day: np.ndarray, levy: bool = False) -> Chain:
        """The chain as a step reading cash on `day` (after its decision day) sees it: a copy with the waiting floor
        decisions (levy: and the pending levy) dated before that day booked. The chain itself books them on their own
        day, after any step walked later and dated before them (`until`)."""
        bound = np.where(np.asarray(day) < self.N, day, -1)
        self.reads = np.maximum(self.reads, bound)
        if not self.waiting:
            return self
        v = self.clone()
        v.until(bound) if levy else v.upto(bound)
        return v

    def next_floor(self) -> np.ndarray:
        """Per draw, the day of the next waiting floor decision (BIG: none)."""
        out, prior = np.full(self.n, BIG, dtype=np.int64), np.ones(self.n, dtype=bool)
        for _, node, _, done, _ in self.waiting:
            if node not in FLOOR_NODES:
                continue
            t = self.tau() if node == "cash_floor" else self.cash_out()
            out = np.where(prior & ~done & (out == BIG), t, out)
            prior = done.copy()
        return out

    def until(self, bound: np.ndarray) -> None:
        """Book, in date order on each trajectory, the waiting decisions and the pending levy dated before `bound` (a
        day index per draw): a floor dated before the levy first, else the response on the levy day and the levy."""
        bound = np.broadcast_to(np.asarray(bound, dtype=np.int64), (self.n,))
        while self.waiting:
            lv = self.pending_levy if self.pending_levy is not None else np.full(self.n, BIG, dtype=np.int64)
            moved = self.upto(bound, levy=lv)
            rows = (lv < bound) & (lv < BIG)
            if rows.any():
                self.flush_levy(rows)
                moved = True
            if not moved:
                break

    def finish(self, tr: Trace) -> Trace:
        """The end of `run`: the pending levy, the waiting floor decisions, the petition's stay of the feed's cash,
        and the path's marks."""
        if self.pending_levy is not None:  # the floor decisions dated up to the pending levy, and the levy
            self.until(self.pending_levy + 1)
        self.flush_levy()
        self.restay()
        triggers = self.trigger_days()  # as of the last step: a floor decision dated after it is not in them
        self.upto(None, every=True)
        self.restay()
        for st in self.stays.values():  # a stay not approved: its security sized on the whole path, for its facts
            if not st["approved"]:
                self._size_stay(st, read=False)
        tr.stays = {i: {k: st[k] for k in ("day", "cash", "owed", "collateral", "stay_offer", "petition",
                                         "triggers")} for i, st in self.stays.items()}
        tr.day, tr.cash, tr.owed, tr.collateral = (list(x) for x in self.rec)
        tr.late = {i: dict(v) for i, v in self.late.items()}
        if self.late and max(self.late) == len(self.rec[0]) - 1:  # the path ends at a floor: its equity available
            self.raise_offer = self.late[max(self.late)]["raise_offer"]
        pet = self.ev.petition
        after = (pet[:, None] >= 0) & (np.arange(self.N)[None, :] >= pet[:, None])
        self.ev.cash[after] = 0  # §362: nothing is collected from or paid by the estate after the petition
        for k in KINDS:
            self.ev.kinds[k][after] = 0
        self._touch()
        if self.pending or self.ordinary:  # a dispute that ended (`resolve`) never re-adds its legal spend
            ended = np.arange(self.N)[None, :] >= self.resolved[:, None]
            back = np.where(after & ended, self.basis.legal, 0)
            self.ev.cash -= back
            self.ev.kinds["reduction"] -= back
            self._touch()
        tr.events = self.ev
        tr.cause = np.where(self.ev.petition >= 0, self.pet_cause, 0).astype(np.int8)
        tr.marks = {k: v.astype(np.int32) for k, v in self.marks.items()}
        tr.settle_offer, tr.stay_offer, tr.raise_offer = self.settle_offer, self.stay_offer, self.raise_offer
        tr.reads = self.reads
        tr.triggers = triggers
        tr.situations = self.c_situations(tr)  # worker C: the question-state snapshot
        return tr

    SHARED = frozenset({"d", "s", "m", "dr", "basis", "sens", "fin", "rows", "bookings"})  # read-only inputs, never copied

    def clone(self) -> Chain:
        """An independent copy of the walk's state (every array it books into), sharing its read-only inputs."""
        new = Chain.__new__(Chain)
        # the memoized cash is shared, not copied: it is never written in place, and each copy replaces its own
        new.__dict__.update({k: v if k in Chain.SHARED or k in ("_cum", "_tau", "_out") else _copied(v) for k, v in self.__dict__.items()})
        return new

    def trigger_days(self) -> dict[str, np.ndarray]:
        """The dated contract and rule triggers on this path (TRIGGERS), per draw, as the engine computes them at the
        end of the traced steps; BIG where none (or outside the period)."""
        never = np.full(self.n, BIG, dtype=np.int64)
        out = {k: never.copy() for k in TRIGGERS}
        inside = lambda x: np.where((x >= 0) & (x < self.N), x, BIG).astype(np.int64)  # noqa: E731
        f = self.fin
        if self.d is not None:
            out["appeal_deadline"] = inside(self.AD)
            if f is not None and f.judgment_default_days:
                for key, ctx in (("judgment_default_entered", "I1"), ("judgment_default_ruling", "post")):
                    ripe, cond = self.judgment_default(ctx)
                    out[key] = inside(np.where(cond, ripe, BIG))
        if f is not None:
            if f.coupon_cents and f.interest_dates:
                pay = min(self.ix(next_business_day(x)) for x in f.interest_dates)
                out["coupon"] = inside(np.full(self.n, pay))
            if f.listing_deadline is not None:
                out["listing_deadline"] = inside(np.full(self.n, self.ix(f.listing_deadline)))
            due = self.marks["notes_due"]
            if f.kind == "convertible_notes" and (due < BIG).any():  # §7.06: the holders' request, then 60 days
                out["holders_petition_earliest"] = inside(np.where(due < BIG, due + self.holder_route_days(), BIG))
            if f.repurchase_business_days and (self.delisted < self.N).any():
                rep = never.copy()
                rep[self.delisted < self.N] = self.repurchase_day(self.delisted[self.delisted < self.N])
                out["repurchase_due"] = inside(rep)
        return out


    # --- step 9 worker C: the question-state snapshot and the interface it reads ---------------------------------
    # The step-9 interface (SPLIT_common.md) is implemented by workers A and B. Each method below is defined here only
    # where no earlier definition in this class exists (`locals()` in the class body), so it never shadows theirs;
    # INTEGRATOR: delete each stub once its real definition is merged. Per-trajectory attributes are read through
    # `c_read`, which raises NotImplementedError naming any the chain does not have.
    C_INTERFACE = {"A": ("ledger_left", "atm_to_date", "offering_terms", "offering_pending", "offerings",
                         "listing_status", "notes_due_day", "notes_due_how", "arrears_by_class", "first_unpaid",
                         "nonpayment_day"),
                   "B": ("judgment_amount_entered", "judgment_standing", "band", "band_range", "default_available_day",
                         "holder_route_days_path", "remitted_amount")}

    if "ledger_left" not in locals():
        def ledger_left(self, day):  # STUB (worker A): shares available on the day, per trajectory
            raise NotImplementedError("Chain.ledger_left (worker A)")
    if "atm_to_date" not in locals():
        def atm_to_date(self, day):  # STUB (worker A): net at-the-market proceeds received by the day, cents
            raise NotImplementedError("Chain.atm_to_date (worker A)")
    if "offering_terms" not in locals():
        def offering_terms(self, day=None):  # STUB (worker A): gross, costs, net, price_cents_x1e4, shares,
            # close_days, each [n], at the capacity left on the day
            raise NotImplementedError("Chain.offering_terms (worker A)")
    if "listing_status" not in locals():
        def listing_status(self, day):  # STUB (worker A): listed | hearing_requested | suspended | delisted
            raise NotImplementedError("Chain.listing_status (worker A)")
    if "arrears_by_class" not in locals():
        def arrears_by_class(self, day):  # STUB (worker A): class -> [n] cents, from the processor
            raise NotImplementedError("Chain.arrears_by_class (worker A)")
    if "judgment_standing" not in locals():
        def judgment_standing(self, day):  # STUB (worker B): none | unpaid | stayed | levied_in_part | reduced | ...
            raise NotImplementedError("Chain.judgment_standing (worker B)")

    def c_read(self, name: str, *args):
        """An interface value: an attribute or property as it is, a method called with `args`."""
        if not hasattr(self, name):
            raise NotImplementedError(f"Chain.{name} (step-9 interface, not on this branch)")
        v = getattr(self, name)
        if callable(v) and not isinstance(v, np.ndarray):
            if name == "nonpayment_day":  # the base's method takes no day
                return v()
            return v(*args)
        return v

    def c_situation(self, day: np.ndarray) -> dict:
        """What a question's situation reads on its decision day (QUESTIONS §§2.1-2.6), per trajectory: each entry an
        array [n], a scalar or dict, or an Awaiting marker where the interface is not built on this branch."""
        day = np.asarray(day, dtype=np.int64)
        t = np.clip(day, 0, self.N - 1)  # the end of the decision day's processing (the question's cash is the row's)
        out: dict = {"cash_end": np.maximum(self.cash_at(t), 0), "owed": self.owed_at(day),
                     "entry": (np.broadcast_to(np.asarray(self.entry_ix(), dtype=np.int64), (self.n,)).copy()
                               if self.has_judgment() else np.full(self.n, BIG, dtype=np.int64)),
                     "ruling": np.asarray(self.F, dtype=np.int64).copy(), "delisted": self.delisted.copy(),
                     "stayed_from": np.asarray(self.stayed_from, dtype=np.int64).copy()}
        reads = {"standing": ("judgment_standing", day), "entered": ("judgment_amount_entered",),
                 "band": ("band",), "band_range": ("band_range",), "default_available": ("default_available_day",),
                 "route_days": ("holder_route_days_path",), "remitted": ("remitted_amount",),
                 "listing": ("listing_status", day), "atm": ("atm_to_date", day), "ledger": ("ledger_left", day),
                 "offering_terms": ("offering_terms", day),
                 "offering_pending": ("offering_pending_on", day) if hasattr(self, "offering_pending_on")
                 else ("offering_pending",),
                 "offerings": ("offerings",), "notes_due_day": ("notes_due_day",), "notes_due_how": ("notes_due_how",),
                 "arrears": ("arrears_by_class", day), "first_unpaid": ("first_unpaid",),
                 "nonpayment_day": ("nonpayment_day",)}
        for key, (name, *args) in reads.items():
            try:
                v = self.c_read(name, *args)
            except NotImplementedError:
                v = Awaiting(name)
            out[key] = _copied(v)
        return out

    def c_situations(self, tr) -> dict:
        """A pending claim's (or the ordinary view's) snapshot at the steps whose facts a question reads: the last
        step (a prefix's decision), each state-triggered step and each stay (their facts come from the whole path)."""
        if not (self.pending or self.ordinary) or not tr.day:
            return {}
        at = {len(tr.day) - 1: tr.day[-1], **{i: tr.day[i] for i in tr.late},
              **{i: st["day"] for i, st in tr.stays.items()}}
        return {i: self.c_situation(day) for i, day in at.items()}

class Awaiting:
    """A question-state value whose step-9 interface accessor is not built on this branch (worker C's snapshot)."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"Awaiting({self.name})"


def _copied(v):
    """A deep copy of a chain attribute: arrays, EventCash, and dicts, lists and tuples of them; scalars as they are."""
    if isinstance(v, np.ndarray):
        return v.copy()
    if isinstance(v, (int, float, str, bool, np.generic)) or v is None:
        return v
    if isinstance(v, EventCash):
        return EventCash(v.cash.copy(), v.lock.copy(), v.capacity.copy(), v.petition.copy(), _copied(v.kinds),
                         _copied(v.incurred))
    if type(v) is dict:
        return {k: _copied(x) for k, x in v.items()}
    if type(v) is list:
        return [_copied(x) for x in v]
    if type(v) is tuple:
        return tuple(_copied(x) for x in v)
    return copy.deepcopy(v)


def _run(make, steps, draws: Draws, key: tuple, inputs: tuple) -> Trace:
    """`make().run(steps)`, resumed from the deepest prefix of `steps` already walked. A chain's state after k steps
    depends only on those k steps, so with `draws.prefixes` on (the tree builder and the analysis walk paths in
    depth-first order) each call walks only the steps after the prefix it shares with the previous call. The cache
    holds one stack of states per chain: the root (after the instrument's cash) and each step of the last path."""
    cache = draws.prefixes
    if cache is None:
        return make().run(steps)
    entry = cache.get(key)
    if entry is None or len(entry[0]) != len(inputs) or any(a is not b for a, b in zip(entry[0], inputs, strict=True)):
        root = make()
        root.instrument_cash()
        entry = cache[key] = (inputs, [(None, root, None)])
    stack = entry[1]
    k = 0
    while k < len(steps) and k + 1 < len(stack) and stack[k + 1][0] == steps[k]:
        k += 1
    del stack[k + 1:]
    ch = stack[k][1].clone()  # its per-step records (Chain.rec) travel with it
    tr = Trace(ch.ev)
    for step in steps[k:]:
        ch.advance(tr, *step)
        stack.append((step, ch.clone(), None))
    return ch.finish(tr)


def event_trace(d: DisputeInstance, path: DisputePath, setup: Setup, model: dict, draws: Draws,
                sens: dict | None = None) -> Trace:
    return _run(lambda: Chain(d, setup, model, draws, sens), tuple(path.steps), draws, (d.instance_id,),
                (d, setup, model, sens))


def answers_levy(node: str, ctx: str) -> bool:
    """The company's response on a levy day, asked before the pending levy is booked."""
    return node in RESPONSES and ctx in ("I1", "post")


def bank_trace(fin, steps, setup: Setup, model: dict, draws: Draws, sens: dict | None = None) -> Trace:
    """The bank view's chain: the borrower's instrument `fin` (common input; None: none) and its distress steps."""
    return _run(lambda: Chain(None, setup, model, draws, sens, fin=fin), tuple(steps), draws, (BANK,),
                (fin, setup, model, sens))


def event_cash(d: DisputeInstance, path: DisputePath, setup: Setup, model: dict, draws: Draws) -> EventCash:
    """The analysis entry point (app/analysis/core.py): the path's event cash on the shared draws."""
    return event_trace(d, path, setup, model, draws).events
