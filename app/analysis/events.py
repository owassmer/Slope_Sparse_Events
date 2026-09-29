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
from app.finance.calendar import BANK_HOLIDAYS, add_months, is_business_day, next_business_day

TOLLING = {"rule_50b", "rule_52b", "rule_59a", "rule_59e", "injunction"}  # FRAP 4(a)(4)(A); the injunction ruling tolls
MONEY_MOTIONS = {"rule_50b", "rule_52b", "rule_59a", "rule_59e"}


# The kinds of event cash the daily processor orders (QUESTIONS_20240514 §2.2; engine.run_many under "daily"):
# receipts (financing proceeds, a credit), a levy, the scheduled obligations (each with the day it was incurred), and
# reductions of operating outflow (legal spend that stops when the dispute ends: an outflow that stops, not a receipt).
OBLIGATIONS = ("settlement", "notes_interest", "judgment")
KINDS = ("inflow", "levy", "reduction", *OBLIGATIONS)
# The equity proceeds by channel (QUESTIONS_20240514 §2.6), per draw: what each channel booked into `inflow` by the
# horizon (EventCash.proceeds; core.Reduction means of the same names).
PROCEEDS = ("atm_proceeds", "offering_proceeds")
INCURRED_BEFORE = -(10**6)  # incurred before the review date and before the line opened (the notes' indenture, 2022)


@dataclass
class EventCash:
    """Per draw and day: borrower cash (+ receipt, - payment), encumbrance changes (+ lock, - release) and credit
    capacity changes (+ commit, - release). Shape [draws, horizon days], integer cents. `petition` [draws] is the
    horizon day index of a bankruptcy petition on that trajectory, or -1 for none. `kinds` splits `cash` by KINDS (they
    sum to it exactly; None: unclassified), and `incurred` gives each obligation kind's incurred day per draw (BIG:
    none), which orders the day's scheduled obligations. `proceeds` gives, per draw, the net cents each equity channel
    booked (PROCEEDS; None: no equity model)."""
    cash: np.ndarray
    lock: np.ndarray
    capacity: np.ndarray
    petition: np.ndarray
    kinds: dict[str, np.ndarray] | None = None
    incurred: dict[str, np.ndarray] | None = None
    proceeds: dict[str, np.ndarray] | None = None

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
        proceeds = None
        if self.proceeds is not None or other.proceeds is not None:
            z = np.zeros(len(petition), dtype=np.int64)
            proceeds = {k: (self.proceeds or {}).get(k, z) + (other.proceeds or {}).get(k, z) for k in PROCEEDS}
        return EventCash(self.cash + other.cash, self.lock + other.lock, self.capacity + other.capacity, petition,
                         kinds, incurred, proceeds)


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
    if isinstance(sensitivity, str) or (isinstance(sensitivity, (int, float)) and not isinstance(sensitivity, bool)):
        return sensitivity  # a named sensitivity (a string, or one figure of a list of sensitivities)
    return p["sensitivity"] if sensitivity and "sensitivity" in p else p["value"]


def rate_1961_bps(model: dict, judgment: date) -> int:
    """§1961: the weekly 1-year CMT for the calendar week preceding the judgment (the latest week ending before it)."""
    table = model["rules"]["usc28_1961"]["rate_bps_by_week_ending"]
    weeks = sorted(w for w in table if date.fromisoformat(w) < judgment)
    if not weeks or (judgment - date.fromisoformat(weeks[-1])).days > 7:
        raise ValueError(f"No §1961 rate for the week before {judgment}; add it to the dispute model (sourced)")
    return int(table[weeks[-1]])


# Nasdaq trading days (QUESTIONS_20240514 §2.6): weekdays other than the exchange's holidays. The Federal Reserve's
# holidays are the exchange's except Columbus Day and Veterans Day (the exchange is open) and Good Friday (it is not).
EXCHANGE_OPEN_ON_BANK_HOLIDAY = frozenset(date.fromisoformat(x) for x in ("2024-10-14", "2024-11-11", "2025-10-13",
                                                                        "2025-11-11", "2026-10-12", "2026-11-11"))
EXCHANGE_CLOSED = frozenset(date.fromisoformat(x) for x in ("2024-03-29", "2025-04-18", "2026-04-03"))


def trading_day(d: date) -> bool:
    return (d.weekday() < 5 and d not in EXCHANGE_CLOSED
            and (d not in BANK_HOLIDAYS or d in EXCHANGE_OPEN_ON_BANK_HOLIDAY))


def settlement_date(d: date, n: int) -> date:
    """T+n: the n-th settlement (business) day after the trade date."""
    out, k = d, 0
    while k < n:
        out += timedelta(days=1)
        k += is_business_day(out)
    return out


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
    if branch.startswith("award:"):  # a J1b class (QUESTIONS §4.1): the amount it books
        return int(branch.split(":")[1]), "band"
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
# the equity model's distress loop (QUESTIONS_20240514 §4.4): the state-triggered steps, each booked on its own day in
# date order (`_upto_dated`): D7 at the k-th fall below the need after a recovery (ctx k), D8 at the first unpaid
# obligation, N1 after an initiation (ctx: the initiating occasion), and §3.3 general nonpayment (D9 and H3)
DISTRESS = ("cash_floor", "cash_out", "offering", "nonpayment")
DISTRESS_NODES = {"cash_floor": "financing_at_floor", "cash_out": "petition_cash_out"}
DISTRESS_BOOKINGS = {"initiate_offering": "offer", "file": "petition", "neither": "none", "none": "none"}
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
    groups: dict = field(default_factory=dict)  # a grouped step's index -> its option group per draw (-1: not asked)


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
        self._av: dict[str, int] = {}  # each event-cash array's version (`_touch` names it; absent: 0)
        self._hd: dict[str, tuple] = {}  # array name -> (its version, its content digest)
        self._keys: dict[str, tuple] = {}  # "net" | "daily" -> (version, the engine run's cache key)
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
        # QUESTIONS_20240514 §2.6: the case declares a share ledger, so the company's equity channels (at-the-market
        # sales, underwritten offerings) and the distress loop (D7, N1, D8, §3.3) run; else the 4.0.0 floor
        self.equity = "value" in model["parameters"].get("share_ledger", {})
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
        self.band: str | None = None  # the total-judgment band of the award (J1b), 'lo-hi' ('top' above the line)
        self.band_range: tuple | None = None  # (low, high) of that band; high None above the top line
        self.remitted_amount = np.zeros(self.n, dtype=np.int64)  # the surviving amount of a reduced judgment (J2, C3)
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
        self.raise_offer = np.zeros(self.n, dtype=np.int64)  # the net proceeds of an offering available at the step
        # equity (QUESTIONS §2.6): the at-the-market proceeds booked [draws, days] (None: none), each offering's terms
        # and outcome, the offerings as (initiated, close, closed [draws]); the listing's dated states; how the notes
        # fell due; the distress decisions' days (floor k -> day, per draw)
        self._atm: np.ndarray | None = None
        self._offers: list[dict] = []
        # a version of what the at-the-market sales read (the petition, delisting, the offerings' held shares),
        # bumped where any of them changes: `_atm_rebook` and `_offer_stack` are memoized on it
        self._eq_v = 0
        # the share price (QUESTIONS §2.6, case parameter share_price): the 14 May close before the verdict, then the
        # structural model at the judgment amount owed; `_price_v` is bumped whenever what it reads changes
        from app.analysis.share_price import model_for
        self.merton = model_for(model["parameters"]) if self.equity else None
        self._price_v = 0
        self._price_key = None
        # the share price in cents per trajectory and day [draws, days] (None: no equity model), replaced whenever
        # `_price_v` moves
        self.share_price = (np.full((self.n, self.N), float(model["parameters"]["atm_pace_bps"]["price_cents"]))
                            if self.merton is not None else None)
        self.settlement_parts: list[tuple[np.ndarray, np.ndarray]] = []  # (day, part) per installment, any date
        self.offerings: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        self._n1: dict[str, bool] = {}
        self.hearing_requested = np.full(self.n, BIG, dtype=np.int64)
        self.suspended = np.full(self.n, BIG, dtype=np.int64)
        self.notes_due_how = np.full(self.n, "", dtype="<U11")
        self._how = "declared"
        self.floor_days: dict[int, np.ndarray] = {}
        self._at = np.full(self.n, BIG, dtype=np.int64)  # the current step's decision day (the accessors' default)
        self.coupons: list[tuple[int, int, np.ndarray]] = []  # (payment day, cash, still paid per draw)
        self.rec: tuple[list, list, list, list] = ([], [], [], [])  # per step: decision day, cash, owed, collateral
        # the state-triggered decisions (FLOOR_NODES) walked but not yet booked on every trajectory: [step index, node,
        # branch, booked mask]; each books on its own day (`upto`), whatever the walk order
        self.waiting: list = []
        self.wctx: dict = {}  # a waiting step's index -> its context
        self.grec: dict = {}  # a grouped step's index -> Chain.option_group per draw, measured before its booking
        self._grp = None  # the last walked step's option groups
        self.late: dict = {}  # their step index -> petition day, triggers and equity available at the decision
        self.reads = np.full(self.n, -1, dtype=np.int64)  # the current step's latest cash-read day (`seen_at`)
        # daily processing (QUESTIONS §2.5): each walked stay (step index -> its terms and what it booked), re-sized on
        # the approval day's balance after every later booking (`restay`); the day the dispute ends, per draw
        self.stays: dict = {}
        self.release_at = np.full(self.n, BIG, dtype=np.int64)
        self._stay_cv = -1  # the event cash's version at the last `restay`
        self._restaying = False  # inside `restay` (its views do not re-size)

    def per_draw(self, x, dtype=np.int64) -> np.ndarray:
        """x as an array [draws] (a scalar filled, an array of that shape as it is): no broadcast view per call."""
        a = np.asarray(x, dtype=dtype)
        if a.shape == (self.n,):
            return a
        return np.full(self.n, a, dtype=dtype) if a.ndim == 0 else np.broadcast_to(a, (self.n,))

    def mark(self, name: str, day, where=None) -> None:
        day = self.per_draw(day)
        ok = (day >= 0) & (day < self.N) & (True if where is None else where)
        if name == "notes_due":  # how they fell due (declared, automatic under §7.02 on a (j) default, repurchase)
            self.notes_due_how = np.where(ok & (day < self.marks[name]), self._how, self.notes_due_how)
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

    def _touch(self, *names: str) -> None:
        """The event cash changed: the memoized available cash and engine runs are stale. `names`: the arrays written
        (`_arrays` names); none: every array."""
        self._cv += 1
        for k in names or [k for k, _ in self._arrays()]:
            self._av[k] = self._cv

    def _arrays(self):
        """The event cash's arrays by name: cash, lock, capacity, petition, `k:<kind>`, `i:<obligation>` (incurred)."""
        ev = self.ev
        yield from (("cash", ev.cash), ("lock", ev.lock), ("capacity", ev.capacity), ("petition", ev.petition))
        yield from ((f"k:{k}", ev.kinds[k]) for k in KINDS)
        yield from ((f"i:{k}", ev.incurred[k]) for k in OBLIGATIONS)

    def _name(self, arr: np.ndarray) -> tuple[str, ...]:
        """The name of an event-cash array (by identity); () if it is not one (then `_touch` bumps every array)."""
        return next(((k,) for k, a in self._arrays() if a is arr), ())

    def _digest(self, name: str, a: np.ndarray) -> bytes:
        """The array's content digest (its nonzero positions and values), rehashed only when its version moved."""
        import hashlib

        v, hit = self._av.get(name, 0), self._hd.get(name)
        if hit is not None and hit[0] == v:
            return hit[1]
        flat = np.ascontiguousarray(a).ravel()
        i = np.flatnonzero(flat)
        d = hashlib.blake2b(np.int64(i.size).tobytes() + i.tobytes() + flat[i].tobytes(), digest_size=20).digest()
        self._hd[name] = (v, d)
        return d

    def _run_key(self, which: str, names: tuple[str, ...], head: bytes = b"") -> bytes:
        """The engine run's cache key: a function of the named arrays' content only (equal states share one run),
        memoized on the event cash's version."""
        import hashlib

        memo = self._keys.get(which)
        if memo is not None and memo[0] == self._cv:
            return memo[1]
        arrays = dict(self._arrays())
        h = hashlib.blake2b(which.encode() + head, digest_size=20)
        for k in names:
            h.update(self._digest(k, arrays[k]))
        self._keys[which] = (self._cv, h.digest())
        return self._keys[which][1]

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

    NET_KEY = ("cash", "lock", "capacity", "petition")  # what the net engine reads
    # the daily processor's: the kinds sum to the cash, so the cash adds nothing
    DAILY_KEY = ("lock", "capacity", "petition", *(f"k:{k}" for k in KINDS), *(f"i:{k}" for k in OBLIGATIONS))

    RUNS = 64  # engine runs kept, per event state: [draws, days] each; the current path's prefixes are reused

    def line_net(self) -> np.ndarray:
        from app.analysis.engine import run

        ev, runs = self.ev, self.basis.runs
        key = self._run_key("net", self.NET_KEY)
        if key in runs:  # least recently used first: a hit moves to the end
            runs[key] = runs.pop(key)
        else:
            # the engine adds the existing line's history cash (Setup.exposure) to its opening itself
            opening = self.basis.opening - self.s.exposure.cash_cents
            tr = run(self.basis.line, opening, EventCash(ev.cash, ev.lock, ev.capacity, ev.petition))
            if len(runs) >= self.RUNS:  # the tree is walked depth-first: recent prefixes are the ones reused
                runs.pop(next(iter(runs)))
            runs[key] = np.cumsum(tr.fundings - tr.collections, axis=1)
        return runs[key]

    def processed(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Under daily processing: the engine's end-of-day available cash [draws, days], first unpaid day and §3.3
        day [draws] (BIG: none) on this event cash, from the run cached per event state (as `line_net`)."""
        from app.analysis.engine import run

        ev, runs = self.ev, self.basis.runs
        terms = self.nonpayment_terms()
        key = self._run_key("daily", self.DAILY_KEY, np.array(terms, dtype=np.int64).tobytes())
        if key in runs:
            runs[key] = runs.pop(key)
        else:
            opening = self.basis.opening - self.s.exposure.cash_cents
            tr = run(self.basis.line, opening, EventCash(ev.cash, ev.lock, ev.capacity, ev.petition, ev.kinds,
                                                         ev.incurred), terms)
            if len(runs) >= self.RUNS:
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

    def processing_balance(self, day: np.ndarray, at: tuple | None = None) -> np.ndarray:
        """Under daily processing: the balance a levy served on the day attaches (§2.2): the day before's end balance
        plus the day's receipts, less the day's encumbrance change and any levy already booked that day. `at`: the
        (cum, event inflow, lock, levy) arrays of an earlier state (`balance_state`) instead of the current one."""
        cum, inflow, lock, levy = self.balance_state() if at is None else at
        t = np.clip(day, 0, self.N - 1)
        prev = np.where(t > 0, cum[self.rows, np.maximum(t - 1, 0)], self.basis.opening)
        return prev + self.basis.inflow[self.rows, t] + inflow[self.rows, t] - lock[self.rows, t] + levy[self.rows, t]

    def balance_state(self, copy: bool = False) -> tuple:
        """What `processing_balance` reads: the cumulative cash, the event inflows, the locks and the levies."""
        k = self.ev.kinds
        out = (self.cum(), k["inflow"], self.ev.lock, k["levy"])
        return tuple(a.copy() for a in out) if copy else out

    def decision_cash(self, day: np.ndarray, at: tuple | None = None) -> np.ndarray:
        """A decision question's cash fact (QUESTIONS §2.2; orchestrator, 29 Sep 2026): under daily processing, the
        balance at processing on the decision day before the decision's own booking (the day's receipts posted, its
        obligations not yet processed); under net processing (20 Jun, as recorded), the day's end cash."""
        if self.daily:
            return self.processing_balance(day, at)
        cum = self.cum() if at is None else at[0]
        return cum[self.rows, np.clip(day, 0, self.N - 1)]

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
        day = np.asarray(day)
        out = np.zeros(self.n, dtype=np.int64)
        for t, amt in self.takes:  # t: a day [draws] or one day (compared by broadcasting)
            out += np.where(t < day, amt, 0)
        return out


    # --- booking ---
    def book(self, arr: np.ndarray, day: np.ndarray, cents) -> None:
        day = np.asarray(day)
        cents = np.asarray(cents, dtype=np.int64)
        cents = cents if cents.shape == day.shape else np.broadcast_to(cents, day.shape)
        ok = (day >= 0) & (day < self.N) & (cents != 0)
        if ok.any():
            np.add.at(arr, (self.rows[ok], day[ok]), cents[ok])
            self._touch(*self._name(arr))

    def pay(self, day: np.ndarray, cents, kind: str, incurred=None) -> None:
        """Book event cash of one kind (KINDS) on the day per draw, into the cash and its kind; an obligation also
        records the day it was incurred (the order the daily processor clears the day's obligations in)."""
        day = np.asarray(day)
        cents = np.asarray(cents, dtype=np.int64)
        cents = cents if cents.shape == day.shape else np.broadcast_to(cents, day.shape)
        self.book(self.ev.cash, day, cents)
        self.book(self.ev.kinds[kind], day, cents)
        if incurred is not None:
            ok = (day >= 0) & (day < self.N) & (cents != 0)
            cur = self.ev.incurred[kind]
            new = np.where(ok, np.minimum(cur, np.asarray(incurred, dtype=np.int64)), cur)
            if not np.array_equal(new, cur):
                self.ev.incurred[kind] = new
                self._touch(f"i:{kind}")

    def petition(self, day: np.ndarray, where: np.ndarray | None = None, cause: str = "enforcement") -> None:
        day = np.asarray(day) + int(self.p("petition_lag_days"))
        ok = (day >= 0) & (day < self.N) & (True if where is None else where)
        cur = self.ev.petition
        win = ok & ((cur < 0) | (day < cur))
        if win.any():
            self.ev.petition = np.where(win, day, cur)
            self._touch("petition")
            self._eq_v += 1
        self.pet_cause = np.where(win, PETITION_CAUSES.index(cause), self.pet_cause).astype(np.int8)
        if win.any():
            self._atm_rebook()  # the at-the-market sales stop at a petition

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
            self._touch("cash", "k:reduction")

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
        self._atm_rebook()  # a levy changes the amount owed the share price reads

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

    def respond(self, booking: str, day: np.ndarray, cause: str = "enforcement", occasion: str = "") -> None:
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
            self._atm_rebook()  # the share price reads the amount owed
        elif booking == "petition":
            where = day < N if cause == "cash_floor" else self.live(day) & (day < N)  # the floor: as 4.0.0
            self.petition(day, where, cause=cause)
        elif booking == "seek":
            self.mark("seeking", day, self.live(day))
        elif booking == "offer":  # an underwritten offering, where one is available (N1 follows)
            self.initiate(day, occasion)
        elif booking != "none":
            raise ValueError(f"No Chain booking {booking!r} (contract branch_bookings)")

    def booking(self, node: str, branch: str) -> str:
        """The Chain booking of a branch: the contract's (branch_bookings); under the equity model, the company's
        14 May branches it does not list (initiate_offering: an offering, N1 follows; none: nothing)."""
        own = self.bookings.get(node, {})
        if branch not in own and self.equity and branch in DISTRESS_BOOKINGS:
            return DISTRESS_BOOKINGS[branch]
        return own[branch]

    def adverse_standing(self, day: np.ndarray) -> np.ndarray:
        """Whether a money judgment on an adverse verdict branch stands on the day: entered, not set aside after
        trial, and not satisfied or released (payment or settlement resolves the dispute). A settled claim is not
        an adverse judgment."""
        day = np.asarray(day)
        return (day >= self.adverse_from) & (day < self.adverse_until) & (day < self.resolved)

    # --- equity (QUESTIONS_20240514 §2.6): the share ledger, at-the-market sales and underwritten offerings ---------
    def price_owed(self, day) -> np.ndarray:
        """The judgment amount owed that the share price reads on the day [draws] (or days [draws, k]): 0 before the
        verdict or with no award; the amount as entered, from the post-trial ruling the amount it leaves, less what
        was paid or levied on or before the day; 0 once the dispute is resolved; after a settlement, its installments
        not yet due."""
        day = np.asarray(day, dtype=np.int64)
        if day.ndim == 0:
            day = np.full(self.n, day, dtype=np.int64)
        col = (lambda a: self.per_draw(a)[:, None]) if day.ndim == 2 else self.per_draw  # noqa: E731
        if not self.pending or not self.entered:
            return np.zeros(day.shape, dtype=np.int64)
        amt = np.full(day.shape, self.entered, dtype=np.int64)
        if self.cls_amount is not None:
            amt = np.where(day >= col(self.F), self.cls_amount, amt)
        for t, a in self.takes:
            amt = amt - np.where(day >= col(t), col(a), 0)
        amt = np.where(day >= col(self.resolved), 0, np.maximum(amt, 0))
        if self.settlement_parts:
            left = sum(np.where(col(t) > day, col(a), 0) for t, a in self.settlement_parts)
            amt = np.where(day >= col(self.marks["settled"]), left, amt)
        return np.where(day >= col(self.V), amt, 0).astype(np.int64)

    def _reprice(self) -> None:
        """Bump `_price_v` where what the share price reads has changed (the amount entered, the ruling, a payment
        or levy, the dispute's resolution, a settlement)."""
        if self.merton is None:
            return
        key = (self.entered, self.cls_amount, np.asarray(self.F).tobytes(), self.resolved.tobytes(), len(self.takes),
               len(self.settlement_parts), self.marks["settled"].tobytes())
        if key != self._price_key:
            self._price_key = key
            self._price_v += 1
            close = float(self.m["parameters"]["atm_pace_bps"]["price_cents"])
            owed = self.price_owed(np.broadcast_to(np.arange(self.N, dtype=np.int64), (self.n, self.N)))
            self.share_price = np.where(owed > 0, self.merton.price(owed), close)

    def share_price_on(self, day=None) -> np.ndarray:
        """The share price in cents on the day [draws] (or days [draws, k]), as floats (`share_price`): the 14 May
        close where no judgment amount is owed (before the verdict, no award, resolved), else the structural model's."""
        day = self._at if day is None else np.asarray(day, dtype=np.int64)
        if self.share_price is None:
            close = float(self.m["parameters"]["atm_pace_bps"]["price_cents"])
            return np.full(np.shape(day) if np.ndim(day) else (self.n,), close)
        self._reprice()
        t = np.clip(self.per_draw(day) if np.ndim(day) < 2 else day, 0, self.N - 1)
        return self.share_price[self.rows, t] if t.ndim == 1 else np.take_along_axis(self.share_price, t, axis=1)

    def _atm_schedule(self) -> tuple[np.ndarray, np.ndarray, int, int]:
        """The at-the-market program's sales (Scenario): each trading day from the first sale (14 May) to the horizon,
        whole shares at the day's dollar pace (the pace share of the average daily dollar volume) over the 14 May close,
        net of the agents' commission, settling T+2 before 28 May 2024 and T+1 from it. Returns each sale's day index,
        its settlement day index, the shares a sale and the net cents a sale."""
        memo = self.__dict__.get("_atm_memo")
        if memo is not None:
            return memo
        p = self.m["parameters"]["atm_pace_bps"]
        price, pace = int(p["price_cents"]), int(self.p("atm_pace_bps"))
        shares = int(p["adv_cents"]) * pace // 10_000 // price
        gross = shares * price
        net = gross * (10_000 - int(p["commission_bps"])) // 10_000
        d, t1, end = date.fromisoformat(p["first_sale"]), date.fromisoformat(p["t1_from"]), self.s.horizon
        sale, settle = [], []
        while d <= end:
            if trading_day(d):
                sale.append(self.ix(d))
                settle.append(self.ix(settlement_date(d, 2 if d < t1 else 1)))
            d += timedelta(days=1)
        self._atm_memo = (np.array(sale, dtype=np.int64), np.array(settle, dtype=np.int64), shares, net)
        return self._atm_memo

    def _offer_stack(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
        """The offerings stacked [offers, draws]: initiation, close, closed, and the shares each holds on its rows (0
        elsewhere); memoized on `_eq_v` (None: no offering)."""
        memo = self.__dict__.get("_offer_memo")
        if memo is not None and memo[0] == self._eq_v:
            return memo[1]
        st = None
        if self._offers:
            st = (np.stack([o["init"] for o in self._offers]), np.stack([o["close"] for o in self._offers]),
                  np.stack([o["closed"] for o in self._offers]),
                  np.stack([np.where(o["rows"], o["shares"], 0) for o in self._offers]).astype(np.int64))
        self._offer_memo = (self._eq_v, st)
        return st

    def _offer_shares_on(self, day: np.ndarray) -> np.ndarray:
        """Shares the offerings hold on the ledger on the day [draws] (or days [draws, k]): reserved from initiation
        to close, drawn from the close where the offering closed (an outcome not yet walked holds them to the close).
        One pass over the stacked offerings, memoized on their version and the day array."""
        day = self.per_draw(day) if np.ndim(day) < 2 else np.asarray(day, dtype=np.int64)
        st = self._offer_stack()
        if st is None:
            return np.zeros(day.shape, dtype=np.int64)
        init, close, closed, shares = st
        if day.ndim == 2:  # the sale days of `_atm_rebook` (memoized there)
            init, close, closed, shares = (a[:, :, None] for a in st)
            held = (day[None] >= init) & ((day[None] < close) | closed)
            return np.where(held, shares, 0).sum(axis=0).astype(np.int64)
        key = (self._eq_v, day.tobytes())
        memo = self.__dict__.get("_shares_memo")
        if memo is not None and memo[0] == key:
            return memo[1]
        held = (day[None] >= init) & ((day[None] < close) | closed)
        out = np.where(held, shares, 0).sum(axis=0).astype(np.int64)
        self._shares_memo = (key, out)
        return out

    def _eq_key(self) -> tuple:
        """The at-the-market booking's memo key: the version of what it reads (`_eq_v`) and of the per-trajectory share
        price (`_price_v`, bumped only where the `share_price` array changes), an input of its sales."""
        self._reprice()
        return self._eq_v, self._price_v

    def _lockup(self, sale: np.ndarray) -> np.ndarray:
        """The sale days an offering's lock-up covers [draws, sales] (case parameter offering_lockup): from its pricing
        to its close, and where it closed, through the lock-up's last day after the close (none: no offering)."""
        out = np.zeros((self.n, sale.size), dtype=bool)
        lock = self.m["parameters"].get("offering_lockup")
        st = self._offer_stack()
        if not lock or st is None or lock.get("atm_carved_out"):
            return out
        init, close, closed, _ = (a[:, :, None] for a in st)
        s = sale[None, None, :]
        held = (init < BIG) & (s >= init + int(lock["pricing_days"])) & (
            (s < close) | (closed & (s <= close + int(lock["value"]))))
        return held.any(axis=0)

    def _atm_columns(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """The sales settling inside the period, ordered by settlement day, the distinct settlement days and where
        each starts in that order (`_atm_rebook` sums the sales of a day in one pass)."""
        memo = self.__dict__.get("_atm_cols")
        if memo is None:
            _, settle, _, _ = self._atm_schedule()
            j = np.flatnonzero((settle >= 0) & (settle < self.N))
            j = j[np.argsort(settle[j], kind="stable")]
            days, start = np.unique(settle[j], return_index=True)
            memo = self._atm_cols = (j, days, start)
        return memo

    def _atm_rebook(self) -> None:
        """Book the at-the-market proceeds on the path's current state: sales stop on the day of a petition or a
        delisting, or once the ledger cannot cover a day's shares (a channel stops when it cannot cover the issuance).
        The proceeds booked before are replaced (receipts, `inflow`). Memoized on what it reads (`_eq_v`): with
        those unchanged it books nothing new."""
        if not self.equity or self.__dict__.get("_atm_v") == self._eq_key():
            return
        self._atm_v = self._eq_key()
        sale, settle, q, _ = self._atm_schedule()
        pet = np.where(self.ev.petition < 0, BIG, self.ev.petition)
        stop = np.minimum(pet, self.delisted)
        led = int(self.m["parameters"]["share_ledger"]["value"])
        other = self._offer_shares_on(np.broadcast_to(sale, (self.n, sale.size)))  # [draws, sales]
        on = (sale[None, :] < stop[:, None]) & ~self._lockup(sale)
        k = np.cumsum(on, axis=1, dtype=np.int64)  # the sales made so far, this one included
        fail = on & (k * q + other > led)  # the ledger cannot cover the day's shares: the channel stops
        ok = on & ~np.logical_or.accumulate(fail, axis=1)
        self._atm_sold = ok  # [draws, sales]
        self._atm_csold = np.cumsum(ok, axis=1, dtype=np.int64)
        j, days, start = self._atm_columns()
        new = np.zeros((self.n, self.N), dtype=np.int64)
        if j.size:  # each settlement day's sales at the sale day's share price, net of commission, per trajectory
            comm = int(self.m["parameters"]["atm_pace_bps"]["commission_bps"])
            gross = np.rint(q * self.share_price_on(np.broadcast_to(sale[j], (self.n, j.size)))).astype(np.int64)
            net = gross * (10_000 - comm) // 10_000
            new[:, days] = np.add.reduceat(np.where(ok[:, j], net, 0), start, axis=1)
        old = self._atm if self._atm is not None else np.zeros_like(new)
        delta = new - old
        self._atm = new
        self._atm_cum = np.cumsum(new, axis=1)
        if delta.any():
            self.ev.cash += delta
            self.ev.kinds["inflow"] += delta
            self._touch("cash", "k:inflow")

    def atm_to_date(self, day=None) -> np.ndarray:
        """Net at-the-market proceeds received (settled) by the day's end, in cents [draws]."""
        day = self._at if day is None else self.per_draw(day)
        if self._atm is None:
            return np.zeros(self.n, dtype=np.int64)
        return np.where(day < 0, 0, self._atm_cum[self.rows, np.clip(day, 0, self.N - 1)])

    def atm_shares_to_date(self, day=None) -> np.ndarray:
        """Shares the at-the-market program sold by the day (trade date) [draws]: the sales sold are a prefix."""
        day = self._at if day is None else self.per_draw(day)
        if self._atm is None:
            return np.zeros(self.n, dtype=np.int64)
        sale, _, q, _ = self._atm_schedule()
        i = np.searchsorted(sale, day, side="right")
        return np.where(i > 0, self._atm_csold[self.rows, np.maximum(i - 1, 0)], 0).astype(np.int64) * q

    def ledger_left(self, day=None) -> np.ndarray:
        """Shares available on the day [draws]: the ledger less the at-the-market shares sold and the offerings'
        shares held (reserved or drawn); never below zero."""
        day = self._at if day is None else self.per_draw(day)
        led = int(self.m["parameters"]["share_ledger"]["value"])
        return np.maximum(led - self.atm_shares_to_date(day) - self._offer_shares_on(day), 0)

    def offering_price_x1e4(self, day=None) -> np.ndarray:
        """The offering price in cents x 1e4 [draws]: the share price on the day (initiation) less the January
        offering's discount to its prior close, rounded half up."""
        p = self.m["parameters"]["offering_price"]
        close = np.rint(self.share_price_on(self._at if day is None else self.per_draw(day)) * 10_000).astype(np.int64)
        return (close * int(p["january_price_cents"]) * 10_000 * 2 // int(p["january_prior_close_cents_x1e4"]) + 1) // 2

    def offering_terms(self, day=None) -> dict[str, np.ndarray]:
        """An offering's terms at the capacity left on the day [draws]: the gross (the January gross, or the shares
        available times the price where capacity binds), costs in proportion, net, price, shares and close days."""
        p = self.m["parameters"]["offering_price"]
        price, gross0, net0 = self.offering_price_x1e4(day), int(p["gross_cents"]), int(p["net_cents"])
        full = (gross0 * 10_000 * 2 // price + 1) // 2
        shares = np.minimum(full, self.ledger_left(day))
        binds = shares < full
        gross = np.where(binds, shares * price // 10_000, gross0)
        net = np.where(binds, gross * net0 // gross0, net0)
        return {"gross": gross, "costs": gross - net, "net": net, "price_cents_x1e4": price,
                "shares": shares, "close_days": np.full(self.n, int(p["close_days"]))}

    def offering_pending_on(self, day) -> np.ndarray:
        """An initiated offering has not reached its close date on the day [draws]."""
        day = self.per_draw(day)
        out = np.zeros(self.n, dtype=bool)
        for o in self._offers:
            out |= o["rows"] & (day >= o["init"]) & (day < o["close"])
        return out

    @property
    def offering_pending(self) -> np.ndarray:
        return self.offering_pending_on(self._at)

    def listing_status(self, day=None) -> np.ndarray:
        """"listed", "hearing_requested" (suspension stayed until the panel decides), "suspended" or "delisted"
        (the indenture's Eligible Market condition fails) on the day [draws]."""
        day = self._at if day is None else self.per_draw(day)
        return np.where(day >= self.delisted, "delisted", np.where(
            day >= self.suspended, "suspended", np.where(day >= self.hearing_requested, "hearing_requested",
                                                         "listed")))

    def offering_available(self, day) -> np.ndarray:
        """An offering can be initiated on the day [draws]: listed, no petition filed, none pending, capacity left."""
        day = self.per_draw(day)
        pet = np.where(self.ev.petition < 0, BIG, self.ev.petition)
        return ((day >= 0) & (day < self.N) & (day < pet) & (day < self.suspended) & (day < self.delisted)
                & ~self.offering_pending_on(day) & (self.ledger_left(day) > 0))

    def offer_available(self, day) -> np.ndarray:
        """The net proceeds of the offering the company could initiate on the day, before the day's decision is
        booked [draws] (0: none available, or no equity model). D2's 'initiate an offering' is offered where it is
        positive on some trajectory (QUESTIONS §4.4 D2), as D7's and D8's are (`decide_distress`)."""
        day = self.per_draw(day)
        if not self.equity:
            return np.zeros(self.n, dtype=np.int64)
        return np.where((day < self.N) & self.offering_available(day), self.offering_terms(day)["net"], 0).astype(np.int64)

    def option_group(self, node: str, day) -> np.ndarray:
        """Per trajectory, the answers a question offers on its day, before its booking (Owen's ruling on within-path
        grouping, 29 Sep 2026): -1 where it is not asked (outside the period, after a petition; for the response, no
        amount owed or the dispute resolved); else bit 0, the balance can be paid (the response only: `respond('pay')`'s
        own test), and bit 1, an offering is available. Trajectories whose answer sets differ are asked separately."""
        day = self.per_draw(day)
        pet = np.where(self.ev.petition < 0, BIG, self.ev.petition)
        on = (day >= 0) & (day < self.N) & (day < pet)
        pay = np.zeros(self.n, dtype=bool)
        if node in RESPONSES:
            owed = self.owed_at(day)
            on &= self.live(day) & (owed > 0)
            pay = on & (self.cash_at(day) >= owed)
        offer = on & (self.offer_available(day) > 0)
        return np.where(on, pay.astype(np.int8) + 2 * offer.astype(np.int8), -1).astype(np.int8)

    def book_grouped(self, node: str, branch: str, day: np.ndarray, book) -> np.ndarray:
        """Book a step's branch on day [draws] through `book(branch, day) -> amount [draws]`, keeping the option groups
        (`_grp`) measured before it for the step's record. A grouped question's answer is booked on every trajectory:
        the path's mask (forecast.py `_Walk.mask_of`) holds the group it was asked of, and the trajectories are
        independent, so what it books outside the mask is never read."""
        self._grp = self.option_group(node, day) if node in GROUPED and (self.pending or self.equity) else None
        return book(branch, day)

    def initiate(self, day: np.ndarray, occasion: str) -> np.ndarray:
        """The company initiates an underwritten offering on the day where one is available (N1 follows): its
        shares are reserved on the ledger until the close. Returns where it was initiated."""
        day = self.per_draw(day).copy()
        rows = self.offering_available(day)
        if not rows.any():
            return rows
        terms = self.offering_terms(day)
        o = {"occasion": occasion, "init": np.where(rows, day, BIG), "rows": rows,
             "close": np.where(rows, day + terms["close_days"], BIG), "shares": np.where(rows, terms["shares"], 0),
             "net": np.where(rows, terms["net"], 0), "closed": np.zeros(self.n, dtype=bool), "booked": False}
        self._offers.append(o)
        self._eq_v += 1
        self._close(o)
        self._atm_rebook()
        return rows

    def _close(self, o: dict) -> None:
        """N1's outcome for the offering, once both the initiation and the answer are on the path: 'yes' books the
        net proceeds on the close date (where no petition precedes it) and draws the ledger; 'no' books nothing."""
        yes = self._n1.get(o["occasion"])
        if yes is None or o["booked"]:
            return
        o["booked"] = True
        pet = np.where(self.ev.petition < 0, BIG, self.ev.petition)
        closed = o["rows"] & (o["close"] < pet) & (o["close"] < self.N) if yes else np.zeros(self.n, dtype=bool)
        o["closed"] = closed
        self._eq_v += 1
        self.pay(np.where(closed, o["close"], BIG), np.where(closed, o["net"], 0), "inflow")
        self.offerings.append((o["init"].copy(), o["close"].copy(), closed.copy()))
        self.mark("raised", o["close"], closed)

    def offering_outcome(self, occasion: str, closes: bool) -> np.ndarray:
        """N1 on the path: the offering initiated at `occasion` closes, or does not. Returns its initiation day
        (BIG where none was initiated)."""
        self._n1[occasion] = closes
        out = np.full(self.n, BIG, dtype=np.int64)
        for o in self._offers:
            if o["occasion"] == occasion:
                self._close(o)
                out = np.where(o["rows"], o["init"], out)
        self._atm_rebook()
        return out

    def offering_day(self, occasion: str) -> np.ndarray:
        """The initiation day of the offering at `occasion` [draws] (BIG: none initiated yet)."""
        out = np.full(self.n, BIG, dtype=np.int64)
        for o in self._offers:
            if o["occasion"] == occasion:
                out = np.where(o["rows"], o["init"], out)
        return out

    @property
    def notes_due_day(self) -> np.ndarray:
        return self.marks["notes_due"]

    def arrears_by_class(self, day=None) -> dict[str, np.ndarray]:
        """The processor's arrears by class at the day's end [draws] (daily processing)."""
        from app.analysis.processor import ARREARS

        day = self._at if day is None else self.per_draw(day)
        arr = self._arrears()
        t = np.clip(day, 0, self.N - 1)
        return {c: np.where((day >= 0) & (day < self.N), arr[self.rows, t, i], 0) for i, c in enumerate(ARREARS)}

    def _arrears(self) -> np.ndarray:
        """The processor's arrears [draws, days, ARREARS] on this event cash: a run of its own, cached for the few
        states a question reads (the full array is too large for the per-prefix cache `processed` keeps)."""
        from app.analysis.engine import run

        if not self.daily:
            raise ValueError("arrears are the daily cash processor's (cash_processing = daily)")
        runs = self.basis.__dict__.setdefault("arrears_runs", {})
        # the event state's content, as `processed` keys it (a version is not unique across clones of one state)
        key = self._run_key("daily", self.DAILY_KEY, np.array(self.nonpayment_terms(), dtype=np.int64).tobytes())
        if key not in runs:
            ev = self.ev
            opening = self.basis.opening - self.s.exposure.cash_cents
            tr = run(self.basis.line, opening, EventCash(ev.cash, ev.lock, ev.capacity, ev.petition, ev.kinds,
                                                         ev.incurred), self.nonpayment_terms())
            if len(runs) >= 4:
                runs.pop(next(iter(runs)))
            runs[key] = tr.processed.arrears
        return runs[key]

    @property
    def first_unpaid(self) -> np.ndarray:
        return self.processed()[1].copy()

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
                self.settlement_parts.append((self.months_after(pd, i), part))
        elif mode == "monthly":
            k = np.maximum((self.N - pd + 29) // 30, 1)
            for i in range(int(k.max())):
                part = np.where(ok & (i < k), bound // k + np.where(i == k - 1, bound % k, 0), 0)
                self.pay(pd + 30 * i, -part, "settlement", incurred=pd)
                self.settlement_parts.append((pd + 30 * i, part))
            release = pd + 30 * (k - 1)
        else:
            self.pay(pd, -np.where(ok, bound, 0), "settlement", incurred=pd)
            self.settlement_parts.append((pd, np.where(ok, bound, 0)))
        self.resolve(release, ok & self.live(release))
        self.mark("settled", pd, ok)
        self._atm_rebook()  # the settlement terms replace the judgment in the share price
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
        day = self.per_draw(day)
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
            if ctx in ("I1", "ruling"):
                self.jd_acted |= cond
        self.coupon_when_due()
        if branch == "yes":
            self.petition(accel, cond, cause="notes")
        elif branch == "holders_file":  # a pending claim: the §3.2 route per trajectory ((j) default: at once)
            route = self.holder_route_days_path if self.pending else self.holder_route_days()
            self.petition(accel + route, cond, cause="notes")
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
        if ctx == "ruling":  # QUESTIONS §3.1 base (as entered): the ruling changed the judgment, default already open
            return self._default_at_ruling(f, reading)
        if ctx == "I1":
            ripe = full + self.e_ix + f.judgment_default_days
            on = reading in ("both", "entered")
            # a pending claim (§3.1): the judgment standing on the ripe day, as a ruling dated before it left it
            amount = self.standing_amount(ripe) if self.pending else self.entered
            cond = np.full(self.n, on and self.has_judgment())
        else:
            ripe = np.maximum(self.A, self.e_ix) + f.judgment_default_days
            amount, on = (self.entered if self.cls_amount is None else self.cls_amount), reading in ("both",
                                                                                                    "post_ruling")
            cond = np.full(self.n, on) & (self.A >= 0) & ~self.jd_acted
        cond = cond & (amount - f.insured_cents > f.judgment_default_threshold_cents)
        cond = cond & (self.stayed_from > ripe) & self.live(ripe) & (self.owed_at(ripe) > 0)
        return ripe, cond

    def _default_at_ruling(self, f, reading: str) -> tuple[np.ndarray, np.ndarray]:
        """QUESTIONS §3.1 base (the judgment as entered): a post-trial ruling does not restart the 60 days. Where it
        changes the judgment (a reduced amount) after the default became available on the judgment as entered, and
        the holders have not acted, their decision is asked again on the ruling day on the changed judgment, with the
        default still available: the changed amount above the threshold, unpaid, not effectively stayed, the notes not
        yet due, and no petition. No new 60 days."""
        day = np.maximum(self.F, 0).astype(np.int64)
        changed = self.pending and self.cls_amount is not None and 0 < self.cls_amount != self.entered
        if reading != "entered" or not changed:
            return np.full(self.n, BIG, dtype=np.int64), np.zeros(self.n, dtype=bool)
        ripe_i1, open_i1 = self.judgment_default("I1")
        cond = open_i1 & (ripe_i1 <= day) & ~self.jd_acted & (self.F >= 0)
        cond = cond & (self.cls_amount - f.insured_cents > f.judgment_default_threshold_cents)
        cond = cond & (self.stayed_from > day) & self.live(day) & (self.owed_at(day) > 0)
        cond = cond & (self.marks["notes_due"] > day)
        return day, cond

    def standing_amount(self, day: np.ndarray) -> np.ndarray:
        """The judgment's amount on the day, before interest and payments: as entered, or from the post-trial ruling
        on (the surviving amount; 0 once set aside). 0 before entry or with no judgment."""
        day = self.per_draw(day)
        out = np.full(self.n, self.entered, dtype=np.int64)
        if self.cls_amount is not None:
            out = np.where(day >= self.F, self.cls_amount, out)
        if self.pending:
            out = np.where(day >= self.E_ix, out, 0)
        return out if self.has_judgment() else np.zeros(self.n, dtype=np.int64)

    # --- the notes and the judgment, per trajectory (the step-9 interface; QUESTIONS §§2.1, 2.3, 3.1, 3.2) ---
    @property
    def judgment_amount_entered(self) -> np.ndarray:
        """The money judgment's amount as entered, per trajectory; 0 where none is entered inside the period."""
        if not self.has_judgment():
            return np.zeros(self.n, dtype=np.int64)
        e = self.per_draw(self.entry_ix())
        return np.where(e < self.N, self.entered, 0).astype(np.int64)

    def judgment_standing(self, day) -> np.ndarray:
        """The judgment's standing on the day (QUESTIONS §2.1), per trajectory: none, unpaid, stayed, levied_in_part,
        reduced, set_aside, paid or settled."""
        day = self.per_draw(day)
        out = np.full(self.n, "unpaid", dtype=object)
        amount = self.standing_amount(day)
        entered = (day >= np.asarray(self.entry_ix())) if self.has_judgment() else np.zeros(self.n, dtype=bool)
        out = np.where((self.cls_amount is not None and self.cls_amount != self.entered) & (day >= self.F)
                       & (amount > 0), "reduced", out)
        out = np.where(self.taken_before(day) > 0, "levied_in_part", out)
        out = np.where(self.stayed_from <= day, "stayed", out)
        out = np.where(entered & (amount == 0), "set_aside", out)
        out = np.where(self.marks["paid"] <= day, "paid", out)
        out = np.where(self.marks["settled"] <= day, "settled", out)
        return np.where(entered, out, np.where(self.marks["settled"] <= day, "settled", "none"))

    @property
    def default_available_day(self) -> np.ndarray:
        """The day the §7.01(i) judgment default becomes available under the reading held on the path (§3.1; base:
        the judgment as entered, entry + 30 days of the Rule 62(a) stay + 60 days; sensitivity: the post-trial
        ruling + 60 days, with no earlier opportunity), BIG where it does not."""
        out = np.full(self.n, BIG, dtype=np.int64)
        for ctx in ("I1", "post"):
            ripe, cond = self.judgment_default(ctx)
            out = np.minimum(out, np.where(cond, ripe, BIG))
        return np.where(out < self.N, out, BIG).astype(np.int64)

    @property
    def holder_route_days_path(self) -> np.ndarray:
        """Per trajectory, days from the notes falling due to the holders' petition (§3.2): none where a §7.01(j)
        general-nonpayment default (§3.3) is continuing when they fall due (§7.06 does not bar it), else the declared
        route (base: the §7.06 request at acceleration + 60 days; sensitivity: at acceleration)."""
        route = np.full(self.n, self.holder_route_days(), dtype=np.int64)
        if not self.daily:
            return route
        return np.where(self.nonpayment_day() <= self.marks["notes_due"], 0, route)

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
        return {"deadline": self.ix(dl), "suspension": self.ix(suspension),
                "vote_call": self.ix(effective_by - timedelta(days=20)), "effective_by": self.ix(effective_by),
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
        self._atm_rebook()
        chips = int(self.p("chips_credit_cents"))
        if chips:
            per = np.full(self.N, chips // self.N, dtype=np.int64)
            per[-1] += chips - per.sum()
            self.ev.cash += per[None, :]
            self.ev.kinds["inflow"] += per[None, :]
            self._touch("cash", "k:inflow")


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
            self.raise_offer = self.offer_available(milestone)
            self.book_grouped(node, branch, milestone, lambda b, t: (self.respond(self.booking(node, b), t,
                                                                                   occasion=ctx), 0)[1])
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
        if node in ("listing", "listing_date") and self.equity:
            return self.listing_step(node, ctx, branch)
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
                self._eq_v += 1
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
            route = self.holder_route_days_path if self.pending else self.holder_route_days()  # §3.2 per trajectory
            return np.where(due < BIG, due + (route if ctx == "holders" else 0), BIG)
        if self.equity and node in DISTRESS:  # booked on every trajectory now (`advance` books it on its own day)
            t = self.distress_day(node, ctx)
            self.raise_offer = self.book_grouped(node, branch, t, lambda b, day: self.decide_distress(node, ctx, b, day))
            return t
        if node in FLOOR_NODES:  # booked on every trajectory now (`advance` books it on its own day instead)
            t = self.tau() if node == "cash_floor" else self.cash_out()
            self.raise_offer = self.decide_floor(node, branch, t)
            return t
        if node == "verdict":
            if branch.startswith("award:"):  # J1b (QUESTIONS §4.1): the award booked and its total-judgment band
                _, total, lo, hi = branch.split(":")
                self.entered = int(total)
                self.band, self.band_range = f"{lo}-{hi}", (int(lo), None if hi == "top" else int(hi))
                self._enter()
                if hi == "top":  # the claimant's amount: the adverse branch
                    self.adverse_from = self.E_ix.copy()
                return self.V.copy()
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
            if branch.startswith("reduced:"):  # J2 reduced, the remittitur accepted (C3): the surviving amount
                self.cls_amount = int(branch.split(":")[1])
                self.remitted_amount = np.where(self.live(self.F) & (self.F < self.N), self.cls_amount, 0)
            if branch == "set_aside":  # no money judgment on the path; the dispute goes on (legal spend too)
                self.cls_amount, self.retrial = 0, True
                self.adverse_until = np.minimum(self.adverse_until, self.F)  # no adverse judgment stands after it
                self.release_lock(np.maximum(self.F, 0), self.live(self.F))
            self.mark("ruled", self.F)
            self.increase = 0
            self.EF, self.EI = self.F.copy(), self.F.copy()
            return self.F.copy()
        raise ValueError(f"Unknown chain step {node}")

    def run(self, steps, day_only: bool = False) -> Trace:
        self.instrument_cash()
        tr = Trace(self.ev)
        for node, ctx, branch in steps:
            self.advance(tr, node, ctx, branch)
        return self.finish(tr, day_only)

    def advance(self, tr: Trace, node: str, ctx: str, branch: str) -> None:
        """One step of `run`: book it and record its decision day and path facts. The cash floor and cash exhaustion
        (FLOOR_NODES) are state-triggered: walked here, they book on each trajectory on their own day (`upto`), after
        every step dated before it and before every step dated after it, whatever the walk order. A grouped branch
        ('@<group>=<answer>') books its answer (`plain`)."""
        branch = plain(branch)
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
        self._last_node = node
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
        before = self.balance_state(copy=True) if self.daily else (self.cum(),)
        self._grp = None
        day = self.step(node, ctx, branch)
        self._atm_rebook()  # a verdict, a ruling or an election changes the amount owed the share price reads
        if self._grp is not None:
            self.grec[len(self.rec[0])] = self._grp
        for lst, v in zip(self.rec, (day, self.decision_cash(day, before), self.owed_at(day),
                                     self.collateral_required.copy()),
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
        elif self.pending:  # "ripe" (QUESTIONS D2): the day the judgment default becomes available (§3.1)
            milestone = self.default_available_day
        else:  # "ripe": after seeking a sale or financing, the post-ruling judgment default's ripe date
            ripe, cond = self.judgment_default("post")
            milestone = np.where(cond, ripe, BIG)
        milestone = np.where(self.owed_at(milestone) > 0, milestone, BIG)  # nothing owed: no response arises
        return milestone

    def decide_waiting(self, i: int, node: str, branch: str, t: np.ndarray) -> np.ndarray:
        """Book waiting step i on day t (BIG: not on that trajectory); returns the equity available at a floor. A
        grouped branch books each option group's answer on its own trajectories (`book_grouped`)."""
        self._grp = None
        amt = self.book_grouped(node, branch, t, lambda b, day: self._decide_waiting(i, node, b, day))
        if self._grp is not None:
            old = self.grec.get(i, np.full(self.n, -1, dtype=np.int8))
            self.grec[i] = np.where(t < BIG, self._grp, old).astype(np.int8)
        return amt

    def _decide_waiting(self, i: int, node: str, branch: str, t: np.ndarray) -> np.ndarray:
        if self.equity and node in DISTRESS:
            return self.decide_distress(node, self.wctx[i], branch, t)
        if node in FLOOR_NODES:
            return self.decide_floor(node, branch, t)
        if node == "judgment_default":
            self.book_default(self.wctx[i], branch, t < BIG)
            return np.zeros(self.n, dtype=np.int64)
        amt = self.offer_available(t) if node in RESPONSES else np.zeros(self.n, dtype=np.int64)
        self.respond(self.booking(node, branch), t, occasion=self.wctx[i])
        return amt

    def decide_floor(self, node: str, branch: str, t: np.ndarray) -> np.ndarray:
        """The company's decision at the cash floor, or when cash runs out, booked on day t (BIG: not on that
        trajectory). Returns the equity available at the floor (0 where the decision is petition_cash_floor)."""
        q = FLOOR_NODES[node]
        amt = np.zeros(self.n, dtype=np.int64)
        self.respond(self.bookings[q][branch], t, cause="cash_floor")
        return amt

    # --- the distress loop (QUESTIONS_20240514 §4.4, one chain for both views) -----------------------------------
    def _fall_after(self, prev: np.ndarray) -> np.ndarray:
        """The first day after `prev` that available cash falls below the month's need having recovered to it (at or
        above) after `prev` [draws] (BIG: none)."""
        cum = self.cum()
        floor = np.zeros_like(cum) if self.sens.get("cash_floor") else self.basis.need
        idx = np.arange(self.N)[None, :]
        up = (cum >= floor) & (idx > prev[:, None])
        rec = np.where(up.any(axis=1), up.argmax(axis=1), BIG)
        down = (cum < floor) & (idx > rec[:, None])
        return np.where(down.any(axis=1), down.argmax(axis=1), BIG).astype(np.int64)

    def distress_day(self, node: str, ctx: str) -> np.ndarray:
        """The day a distress step's decision falls on this path's cash as booked so far [draws] (BIG: none)."""
        if node == "cash_floor":
            k = int(ctx)
            if k == 1:
                return self.tau()
            prev = self.floor_days.get(k - 1)
            return self._fall_after(prev) if prev is not None else np.full(self.n, BIG, dtype=np.int64)
        if node == "cash_out":
            return self.cash_out()
        if node == "offering":
            return self.offering_day(ctx)
        if node == "nonpayment":  # §3.3 met while the notes are not yet due
            day = self.nonpayment_day()
            return np.where((day < BIG) & (self.marks["notes_due"] > day), day, BIG)
        raise ValueError(f"No distress step {node}")

    def decide_distress(self, node: str, ctx: str, branch: str, t: np.ndarray) -> np.ndarray:
        """Book a distress step on day t (BIG: not on that trajectory). Returns the net proceeds of the offering
        available at a financing decision (0 where none is available), before its booking."""
        zero = np.zeros(self.n, dtype=np.int64)
        on = t < self.N
        if node == "offering":  # N1: the offering initiated at the occasion closes, or does not
            self.offering_outcome(ctx, branch == "yes")
            return zero
        if node == "nonpayment":  # §3.3: the notes are due automatically (§7.02); D9 and H3 (§7.06 does not bar it)
            self._how = "automatic_j"
            self.mark("notes_due", t, on)
            self._how = "declared"
            self.coupon_when_due()
            if branch == "petition":
                self.petition(t, on, cause="notes")
            elif branch == "holders_file":
                self.petition(t, on, cause="notes")
            elif branch != "due":
                raise ValueError(f"No §3.3 branch {branch!r}")
            return zero
        if node == "cash_floor":
            k = int(ctx)
            self.floor_days[k] = np.where(on, t, self.floor_days.get(k, np.full(self.n, BIG, dtype=np.int64)))
        occasion = f"floor{ctx}" if node == "cash_floor" else "cash_out"
        amt = np.where(on & self.offering_available(t), self.offering_terms(t)["net"], 0).astype(np.int64)
        self.respond(DISTRESS_BOOKINGS[branch], t, cause="cash_floor", occasion=occasion)
        return amt

    def listing_step(self, node: str, ctx: str, branch: str) -> np.ndarray:
        """The listing under the equity model (QUESTIONS §4.6): D6a, compliance regained by the deadline, decided on
        it; else the staff's determination the next day and D6b, a hearing request within 7 days, which stays
        suspension until the panel decides; without one, suspension on the rule's day, when the indenture's Eligible
        Market condition fails (delisted). Asked where no petition precedes the deadline."""
        full = np.full(self.n, 0, dtype=np.int64)
        dates = self.listing_dates()
        pet = np.where(self.ev.petition < 0, BIG, self.ev.petition)
        if node == "listing_date":  # a question's own decision day: ctx "compliance" (D6a) or "hearing_request" (D6b)
            day = dates["deadline"] if ctx == "compliance" else dates["hearing_request"]
            return np.where(full + day < pet, full + day, BIG)
        on = full + dates["deadline"] < pet
        if branch == "hearing":
            if dates["panel_decision"] < self.N:
                raise NotImplementedError("the panel decides inside the period: its decision is not modeled")
            self.hearing_requested = np.where(on, dates["hearing_request"], self.hearing_requested)
        elif branch == "suspended":
            self.suspended = np.where(on, dates["suspension"], self.suspended)
            self.delisted = np.where(on, dates["delisted_suspension"], self.delisted)
            self._eq_v += 1
            self.mark("delisted", self.delisted)
            self._atm_rebook()  # the at-the-market sales stop at delisting
        elif branch != "compliant":
            raise ValueError(f"No listing branch {branch!r}")
        return np.where(on, dates["deadline"], BIG)

    def waits(self, node: str, ctx: str) -> bool:
        """The decisions booked on their own day on each trajectory, whatever the walk order: the cash floor and cash
        exhaustion, and the company's response on the post-ruling levy day, which the walk may ask before the I3
        settlement window that precedes it on some trajectories, and the notes' judgment default (the walk asks the
        pre-ruling one before a ruling that can set the judgment aside first). A pending claim (4.1.0) only: 4.0.0
        books each step as it is walked, the floor last, as recorded."""
        if self.equity and node in DISTRESS:
            return True
        if self.ordinary:
            return node in FLOOR_NODES
        return self.pending and (node in FLOOR_NODES or node == "judgment_default" or (node in RESPONSES
                                                                                     and ctx in ("post", "ripe")))

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
            if node in RESPONSES and ctx != "ripe":  # the levy-day responses (the default's day is not a levy's)
                out |= ~done & (self.response_day(ctx) < BIG)
        return out

    def upto(self, before: np.ndarray | None, every: bool = False, levy: np.ndarray | None = None) -> None:
        """Book the waiting state-triggered decisions, in walk order, on each trajectory whose own day falls before
        `before` (every: on every trajectory left). A day's cash reads nothing booked after it, so a floor dated before
        `before` is final there; its facts (day, cash, owed, collateral, earlier petitions, triggers) are its own day's."""
        if not self.waiting or (before is None and not every):
            return False
        if self.equity:
            return self._upto_dated(before, every, levy)
        moved = False
        prior = np.ones(self.n, dtype=bool)
        for i, node, branch, done, ctx in self.waiting:
            floor = node in FLOOR_NODES
            t = (self.tau() if node == "cash_floor" else self.cash_out()) if floor else self.waiting_day(node, ctx)
            # a waiting step dated after the pending levy (levy: its day) books after it; the response on the levy
            # day comes before it
            lim = before if levy is None or (node in RESPONSES and ctx != "ripe") else np.minimum(before, levy)
            fire = (prior if floor else True) & ~done & (True if every else t < lim)
            if fire.any():
                vals = (t, self.decision_cash(t), self.owed_at(t), self.collateral_required)
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
        vf = self.__dict__.setdefault("_vfired", {})
        for w in self.waiting:  # the day each waiting step books in the view (exact subtree reuse reads it)
            booked = (v.rec[0][w[0]] < BIG) & ~w[3]
            if booked.any():
                vf[w[0]] = np.minimum(vf.get(w[0], BIG), np.where(booked, v.rec[0][w[0]], BIG))
        return v

    def _upto_dated(self, before, every: bool, levy, target: int | None = None) -> bool:
        """`upto` under the equity model: each trajectory books its waiting decisions one at a time, the earliest-
        dated first (ties in walk order), each day recomputed on the cash as booked so far, so a later-walked step
        dated before an earlier-walked one books first. every: all left, the undated last (they book nothing).
        target: a waiting step's index (`finish(day_only=True)` of a prefix ending in it): on each trajectory, every
        decision dated inside the horizon up to and including it (the order `every` books them in), then those
        dated on its own day; `before` is ignored."""
        moved = False
        tdone = next((w[3] for w in self.waiting if w[0] == target), None) if target is not None else None
        while self.waiting:
            if tdone is not None:  # the target's day is known once it is booked on the trajectory
                before = np.where(tdone, self.rec[0][target] + 1, self.N)
            best = np.full(self.n, BIG + 1, dtype=np.int64)
            pick = np.full(self.n, -1, dtype=np.int64)
            days = []
            for j, (_, node, _, done, ctx) in enumerate(self.waiting):
                t = self.distress_day(node, ctx) if node in DISTRESS else self.waiting_day(node, ctx)
                lim = before if levy is None or node in RESPONSES else np.minimum(before, levy)
                cand = ~done & (True if every else t < lim)
                better = cand & (t < best)
                best, pick = np.where(better, t, best), np.where(better, j, pick)
                days.append(t)
            if (pick < 0).all():
                break
            for j, (i, node, branch, done, _ctx) in enumerate(self.waiting):
                fire = pick == j
                if not fire.any():
                    continue
                t = days[j]
                vals = (t, self.decision_cash(t), self.owed_at(t), self.collateral_required)
                for lst, v in zip(self.rec, vals, strict=True):
                    lst[i] = np.where(fire, v, lst[i])
                late = self.late[i]
                late["petition"] = np.where(fire, self.ev.petition, late["petition"])
                late["triggers"] = {k: np.where(fire, v, late["triggers"].get(k, BIG))
                                    for k, v in self.trigger_days().items()}
                amt = self.decide_waiting(i, node, branch, np.where(fire, t, BIG))
                late["raise_offer"] = np.where(fire, amt, late["raise_offer"])
                done |= fire  # each trajectory books one step a pass (bookings are per trajectory)
                moved = True
            self.waiting = [w for w in self.waiting if not w[3].all()]
        return moved

    def next_floor(self) -> np.ndarray:
        """Per draw, the day of the next waiting floor decision (BIG: none)."""
        if self.equity:
            out = np.full(self.n, BIG, dtype=np.int64)
            for _, node, _, done, ctx in self.waiting:
                if node in DISTRESS:
                    out = np.where(~done, np.minimum(out, self.distress_day(node, ctx)), out)
            return out
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
        bound = self.per_draw(bound)
        while self.waiting:
            lv = self.pending_levy if self.pending_levy is not None else np.full(self.n, BIG, dtype=np.int64)
            moved = self.upto(bound, levy=lv)
            rows = (lv < bound) & (lv < BIG)
            if rows.any():
                self.flush_levy(rows)
                moved = True
            if not moved:
                break

    def _book_to_day(self) -> None:
        """The day-only end of a prefix (`finish(day_only=True)`): on each trajectory, the waiting decisions dated up
        to the last step's decision day, in the order the whole path books them (a prefix of that order), and nothing
        after it. The engine is causal (a day reads nothing booked after it), so every read of the prefix as of its
        decision day (cash, owed, marks and petition on or before it, its facts, its situation) equals the whole
        path's; the fingerprint of the event cash to the horizon is not computed (`_Prefix.digest` None). Where the
        last step itself waits, its day is known only once it books: every decision before it, it, then those on
        its day. Trajectories where the step falls outside the horizon book nothing more (no read looks there). A
        walked stay's snapshot is its approval day's (`c_situations`, `_snapshot_day`): booked up to that day."""
        i = len(self.rec[0]) - 1
        if any(w[0] == i for w in self.waiting):
            tw = next(w for w in self.waiting if w[0] == i)
            self._upto_dated(None, False, None, target=i)
            self._booked_to = np.where(tw[3], self.rec[0][i], self.N - 1)
            return
        t, u = self.rec[0][i], self._snapshot_day(i)
        bound = np.where(u < self.N, u + 1, np.where(t < self.N, self.N, 0))
        # notes_due_date books every waiting decision inside the horizon first (`advance`): its day reads them all
        self._booked_to = np.full(self.n, self.N - 1) if self.__dict__.get("_last_node") == "notes_due_date" \
            else bound - 1
        if self.waiting:  # a snapshot after the horizon reads its last day (`c_situation` clips)
            self._upto_dated(bound, False, None)

    def _snapshot_day(self, i: int) -> np.ndarray:
        """The day step i's question-state snapshot reads (`c_situations`): a walked stay's approval day, else the
        step's decision day."""
        return self.stays[i]["approval"] if i in self.stays else self.rec[0][i]

    def finish(self, tr: Trace, day_only: bool = False) -> Trace:
        """The end of `run`: the pending levy, the waiting floor decisions, the petition's stay of the feed's cash,
        and the path's marks. day_only: the waiting decisions only up to the last step's day (`_book_to_day`), and
        the question-state snapshot of the last step only."""
        if self.pending_levy is not None:  # the floor decisions dated up to the pending levy, and the levy
            self.until(self.pending_levy + 1)
        self.flush_levy()
        self.restay()
        triggers = self.trigger_days()  # as of the last step: a floor decision dated after it is not in them
        day_only = day_only and self.equity and bool(self.rec[0])
        if day_only:
            self._book_to_day()
        else:
            self.upto(None, every=True)
        self.restay()
        if not day_only:
            for st in self.stays.values():  # a stay not approved: its security sized on the whole path, for its facts
                if not st["approved"]:
                    self._size_stay(st, read=False)
        tr.stays = {} if day_only else {
            i: {k: st[k] for k in ("day", "cash", "owed", "collateral", "stay_offer", "petition", "triggers")}
            for i, st in self.stays.items()}
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
        if self.equity:  # each channel's receipts as booked above: the sales settled and the offerings closed before
            stop = np.where(pet < 0, BIG, pet)  # the petition (nothing after it)
            atm = np.zeros(self.n, dtype=np.int64) if self._atm is None else np.where(after, 0, self._atm).sum(axis=1)
            off = sum((np.where(o["closed"] & (o["close"] < stop), o["net"], 0) for o in self._offers),
                      np.zeros(self.n, dtype=np.int64))
            self.ev.proceeds = {"atm_proceeds": atm.astype(np.int64), "offering_proceeds": off.astype(np.int64)}
        if self.pending or self.ordinary:  # a dispute that ended (`resolve`) never re-adds its legal spend
            ended = np.arange(self.N)[None, :] >= self.resolved[:, None]
            back = np.where(after & ended, self.basis.legal, 0)
            self.ev.cash -= back
            self.ev.kinds["reduction"] -= back
            self._touch("cash", "k:reduction")
        tr.events = self.ev
        tr.cause = np.where(self.ev.petition >= 0, self.pet_cause, 0).astype(np.int8)
        tr.marks = {k: v.astype(np.int32) for k, v in self.marks.items()}
        tr.settle_offer, tr.stay_offer, tr.raise_offer = self.settle_offer, self.stay_offer, self.raise_offer
        tr.reads = self.reads
        tr.triggers = triggers
        tr.situations = ({len(tr.day) - 1: self.c_situation(self._snapshot_day(len(tr.day) - 1))} if day_only
                         and (self.pending or self.ordinary) else self.c_situations(tr))  # the question-state snapshot
        tr.groups = {i: g.copy() for i, g in self.grec.items()}
        vf = self.__dict__.get("_vfired", {})  # each waiting step's booking day, here or in a view (BIG: neither)
        tr.fired = {i: np.minimum(tr.day[i], vf.get(i, BIG)) for i in tr.late}
        # a day-only trace: per draw, the last day whose state it reads (what it booked through, a cash read's day)
        tr.as_of = np.maximum(self._booked_to, self.reads) if day_only else None
        return tr

    SHARED = frozenset({"d", "s", "m", "dr", "basis", "sens", "fin", "rows", "bookings", "merton"})  # read-only inputs, never copied

    def clone(self) -> Chain:
        """An independent copy of the walk's state (every array it books into), sharing its read-only inputs."""
        new = Chain.__new__(Chain)
        # the memoized cash is shared, not copied: it is never written in place, and each copy replaces its own
        new.__dict__.update({k: v if k in Chain.SHARED or k in ("_cum", "_tau", "_out") else _copied(v) for k, v in self.__dict__.items()})
        return new

    # --- exact subtree reuse (forecast.py `Forecaster._served`) ------------------------------------------------------
    # Chain state by what a later step or a walk read makes of it (a recorded fact is always computed: forecast.py
    # `_facts`). DATED: a day per draw (an event's day; -1 or BIG: none) the engine reads only as of a day; it is
    # causal, so two chains agreeing on every event dated before day X agree on everything read as of a day before
    # X. UNSEEN: memos of other state, transients reset by every step, each step's own record, facts-only state (the
    # hearing request, how the notes fell due, the offerings' list: the snapshot's listing status and texts), and
    # state each of whose changes is booked in a dated array the same day (lock_amount, lock_day: ev.lock). Else
    # (the timeline, flags, the ruling's amounts) is compared as it is: a difference is a divergence from day 0 on
    # the draws it concerns (on every draw where it is not per draw).
    DATED = frozenset({"suspended", "resolved", "release_at", "adverse_from", "adverse_until", "early_registration",
                       "pending_levy", "delisted", "stayed_from"})
    UNSEEN = frozenset({"_cum", "_tau", "_out", "_keys", "_av", "_hd", "_cv", "_stay_cv", "_restaying", "_atm_memo",
                        "_atm_cols", "_atm_v", "_eq_v", "_offer_memo", "_shares_memo", "_grp", "settle_offer",
                        "stay_offer", "raise_offer", "reads", "rec", "grec", "late", "wctx", "_atm", "_atm_cum",
                        "_atm_sold", "_atm_nsold", "taken", "_booked_to", "_last_node", "ev", "marks", "waiting", "takes", "writs",
                        "coupons", "floor_days", "stays", "pet_cause", "collateral_required", "lock_amount",
                        "levied", "q1", "offerings", "_at", "notes_due_how", "appealed", "_offers", "lock_day",
                        "hearing_requested", "_vfired", "_price_v", "_price_key"})

    def divergence(self, other: Chain, wait: int | None = None) -> np.ndarray:
        """Per draw, a day before which this chain and `other` (the same dispute after sibling steps) book and read
        the same (BIG: they agree inside the horizon). `wait`: the index of a waiting step whose branch alone differs
        (its booking day is the divergence, read per trace by the caller). Conservative: state it cannot date
        diverges from day 0."""
        n, N = self.n, self.N
        x = np.full(n, BIG, dtype=np.int64)
        zero = np.zeros(n, dtype=np.int64)

        def day(a):
            a = np.asarray(a if a is not None else BIG, dtype=np.int64)
            return np.where(a < 0, BIG, a)

        def dated(a, b):
            nonlocal x
            da, db = day(a), day(b)
            x = np.minimum(x, np.where(da != db, np.minimum(da, db), BIG))

        def cols(a, b):  # [draws, days]: the first day they differ
            nonlocal x
            ne = a != b
            x = np.minimum(x, np.where(ne.any(axis=1), ne.argmax(axis=1), BIG))

        def plain(a, b):
            nonlocal x
            if isinstance(a, np.ndarray) and isinstance(b, np.ndarray) and a.shape == b.shape == (n,):
                x = np.where(a != b, zero, x)
            elif not _same_state(a, b):
                x = zero.copy()

        ea, eb = self.ev, other.ev
        for k in ("cash", "lock", "capacity"):
            cols(getattr(ea, k), getattr(eb, k))
        for k in set(ea.kinds) | set(eb.kinds):
            cols(ea.kinds.get(k, 0 * ea.cash), eb.kinds.get(k, 0 * eb.cash))
        dated(ea.petition, eb.petition)
        for k in set(ea.incurred) | set(eb.incurred):  # the day each obligation was incurred
            dated(ea.incurred.get(k), eb.incurred.get(k))
        plain(ea.proceeds, eb.proceeds)
        pa, pb = day(ea.petition), day(eb.petition)
        x = np.minimum(x, np.where(self.pet_cause != other.pet_cause, np.minimum(pa, pb), BIG))
        for k in set(self.marks) | set(other.marks):
            dated(self.marks.get(k), other.marks.get(k))
        if self.appealed != other.appealed:  # the appeal's flag acts from the appeal (the levy's registration day)
            x = np.minimum(x, np.where(self.AD < 0, BIG, np.maximum(self.F, 0)))
        for i in range(max(len(self._offers), len(other._offers))):  # an offering acts from its initiation
            oa = self._offers[i] if i < len(self._offers) else None
            ob = other._offers[i] if i < len(other._offers) else None
            init = np.minimum(*(day(o["init"]) if o is not None else np.full(n, BIG) for o in (oa, ob)))
            ne = np.ones(n, dtype=bool) if oa is None or ob is None else _differs(oa, ob, n)
            x = np.minimum(x, np.where(ne, init, BIG))
        for k in set(self.floor_days) | set(other.floor_days):
            dated(self.floor_days.get(k), other.floor_days.get(k))
        for la, lb in ((self.takes, other.takes), (self.writs, other.writs)):  # (day, amount) events
            for i in range(max(len(la), len(lb))):
                (da, aa), (db, ab) = (la[i] if i < len(la) else (BIG, 0)), (lb[i] if i < len(lb) else (BIG, 0))
                ne = (day(da) != day(db)) | (np.asarray(aa) != np.asarray(ab))
                x = np.minimum(x, np.where(ne, np.minimum(day(da), day(db)), BIG))
        for i in range(max(len(self.coupons), len(other.coupons))):  # (payment day, cash, paid per draw)
            ca = self.coupons[i] if i < len(self.coupons) else None
            cb = other.coupons[i] if i < len(other.coupons) else None
            if ca is None or cb is None or ca[0] != cb[0] or ca[1] != cb[1]:
                x = np.minimum(x, min(c[0] for c in (ca, cb) if c is not None))
            else:
                x = np.minimum(x, np.where(np.asarray(ca[2]) != np.asarray(cb[2]), ca[0], BIG))
        terms = ("approval", "approved", "stayed_from", "mark")  # what `restay` sizes from (the rest is its output)
        for i in set(self.stays) | set(other.stays):  # a walked stay: what differs books from its approval
            sa, sb = self.stays.get(i), other.stays.get(i)
            if sa is None or sb is None:
                x = np.minimum(x, min(day(s_["approval"]) for s_ in (sa, sb) if s_ is not None))
                continue
            ta, tb = {k: sa[k] for k in terms}, {k: sb[k] for k in terms}
            appr = np.minimum(day(sa["approval"]), day(sb["approval"]))
            x = np.minimum(x, np.where(_differs(ta, tb, n), appr, BIG))
            if ("lock" in sa) != ("lock" in sb):  # what it booked (`restay` takes it out before re-sizing)
                x = np.minimum(x, appr)
            elif "lock" in sa:  # the lock on approval, its release on `rel`
                x = np.minimum(x, np.where(sa["lock"] != sb["lock"], appr, BIG))
                x = np.minimum(x, np.where((sa["rel"] != sb["rel"]) | (sa["held"] != sb["held"]),
                                           np.minimum(day(sa["rel"]), day(sb["rel"])), BIG))
        wa = {w[0]: w for w in self.waiting}
        wb = {w[0]: w for w in other.waiting}
        for i in set(wa) | set(wb):
            a, b = wa.get(i), wb.get(i)
            if a is None or b is None or a[1] != b[1] or a[4] != b[4] or (a[2] != b[2] and i != wait):
                x = zero.copy()
            else:
                x = np.where(a[3] != b[3], zero, x)
        # the per-trajectory share price (step-9-price) is an input of every equity read: compared even if it is
        # made a shared read-only input; where it differs the chains diverge from day 0 on those draws
        self._reprice(), other._reprice()  # compared as later reads see it
        plain(self.__dict__.get("share_price"), other.__dict__.get("share_price"))
        for k in (set(self.__dict__) | set(other.__dict__)) - Chain.SHARED - Chain.UNSEEN - {"share_price"}:
            a, b = self.__dict__.get(k), other.__dict__.get(k)
            (dated if k in Chain.DATED else plain)(a, b)
        return np.where(x >= N, BIG, x).astype(np.int64)

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
    # The step-9 interface (SPLIT_common.md) is implemented by workers A and B above (merged at integration; worker C's
    # guarded stubs retired). Per-trajectory attributes are read through `c_read`, which
    # raises NotImplementedError naming any the chain does not have.
    C_INTERFACE = {"A": ("ledger_left", "atm_to_date", "offering_terms", "offering_pending", "offerings", "share_price_on",
                         "listing_status", "notes_due_day", "notes_due_how", "arrears_by_class", "first_unpaid",
                         "nonpayment_day"),
                   "B": ("judgment_amount_entered", "judgment_standing", "band", "band_range", "default_available_day",
                         "holder_route_days_path", "remitted_amount")}

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
                 "offering_terms": ("offering_terms", day), "share_price": ("share_price_on", day),
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

def _same_state(a, b) -> bool:
    """Equal chain state: arrays by value (shape, dtype, content), containers element by element."""
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        return (isinstance(a, np.ndarray) and isinstance(b, np.ndarray) and a.shape == b.shape
                and a.dtype == b.dtype and np.array_equal(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same_state(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return type(a) is type(b) and len(a) == len(b) and all(_same_state(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, EventCash) or isinstance(b, EventCash):
        return False
    return type(a) is type(b) and a == b


def _differs(a: dict, b: dict, n: int) -> np.ndarray:
    """Per draw, whether two dicts of per-draw arrays (and scalars) differ (a differing scalar: every draw)."""
    out = np.zeros(n, dtype=bool)
    for k in set(a) | set(b):
        x, y = a.get(k), b.get(k)
        if isinstance(x, np.ndarray) and isinstance(y, np.ndarray) and x.shape == y.shape == (n,):
            out |= x != y
        elif not _same_state(x, y):
            out[:] = True
    return out


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
                         _copied(v.incurred), _copied(v.proceeds))
    if type(v) is dict:
        return {k: _copied(x) for k, x in v.items()}
    if type(v) is list:
        return [_copied(x) for x in v]
    if type(v) is tuple:
        return tuple(_copied(x) for x in v)
    return copy.deepcopy(v)


def _advanced(make, steps, draws: Draws, key: tuple, inputs: tuple) -> tuple[Chain, Trace]:
    """`make()` with `steps` advanced (not finished), resumed from the deepest prefix of `steps` already walked. A
    chain's state after k steps depends only on those k steps, so with `draws.prefixes` on (the tree builder and the
    analysis walk paths in depth-first order) each call walks only the steps after the prefix it shares with the
    previous call. The cache holds one stack of states per chain: the root (after the instrument's cash) and each
    step of the last path."""
    cache = draws.prefixes
    if cache is None:
        ch = make()
        ch.instrument_cash()
        tr = Trace(ch.ev)
        for step in steps:
            ch.advance(tr, *step)
        return ch, tr
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
    return ch, tr


def _run(make, steps, draws: Draws, key: tuple, inputs: tuple, day_only: bool = False) -> Trace:
    """`make().run(steps)`, resumed from the prefix stack (`_advanced`)."""
    ch, tr = _advanced(make, steps, draws, key, inputs)
    return ch.finish(tr, day_only)


def event_chain(d: DisputeInstance | None, steps, setup: Setup, model: dict, draws: Draws, sens: dict | None = None,
                fin=None) -> Chain:
    """The chain after the steps (canonical), not finished: the dispute's (d) or the bank view's (d None, `fin`)."""
    if d is None:
        return _advanced(lambda: Chain(None, setup, model, draws, sens, fin=fin), steps, draws, (BANK,),
                         (fin, setup, model, sens))[0]
    return _advanced(lambda: Chain(d, setup, model, draws, sens), steps, draws, (d.instance_id,),
                     (d, setup, model, sens))[0]


def event_trace(d: DisputeInstance, path: DisputePath, setup: Setup, model: dict, draws: Draws,
                sens: dict | None = None, day_only: bool = False) -> Trace:
    """The path's trace; day_only: settled only to its last step's decision day (`Chain._book_to_day`)."""
    return _run(lambda: Chain(d, setup, model, draws, sens), canon(path.steps), draws, (d.instance_id,),
                (d, setup, model, sens), day_only)


GROUPED = (*RESPONSES, "cash_floor", "cash_out")  # questions asked per option group (Owen's ruling, 29 Sep 2026)


def plain(branch: str) -> str:
    """A step's answer: a grouped step's branch '@<group>=<answer>' (the path asked it of one option group,
    Chain.option_group: bit 0, the balance can be paid; bit 1, an offering is available; -1, not asked) books
    <answer>."""
    return branch.split("=", 1)[1] if branch.startswith("@") else branch


def step_group(branch: str) -> int | None:
    """The option group a grouped step's branch names (None: not grouped)."""
    return int(branch[1:].split("=", 1)[0]) if branch.startswith("@") else None


def canon(steps) -> tuple:
    """The steps as the engine books them (grouped branches as their answers): paths that differ only in the group
    they follow share one chain."""
    return tuple((n, c, plain(b)) if b.startswith("@") else (n, c, b) for n, c, b in steps)


def group_tags(node: str, code: int) -> tuple[str, ...]:
    """The context tags naming an option group (they key the group's own question)."""
    offer = "offer" if code & 2 else "nooffer"
    return (("pay" if code & 1 else "nopay"), offer) if node in RESPONSES else (offer,)


def answers_levy(node: str, ctx: str) -> bool:
    """The company's response on a levy day, asked before the pending levy is booked."""
    return node in RESPONSES and ctx in ("I1", "post")


def bank_trace(fin, steps, setup: Setup, model: dict, draws: Draws, sens: dict | None = None,
               day_only: bool = False) -> Trace:
    """The bank view's chain: the borrower's instrument `fin` (common input; None: none) and its distress steps."""
    return _run(lambda: Chain(None, setup, model, draws, sens, fin=fin), canon(steps), draws, (BANK,),
                (fin, setup, model, sens), day_only)


def event_cash(d: DisputeInstance, path: DisputePath, setup: Setup, model: dict, draws: Draws) -> EventCash:
    """The analysis entry point (app/analysis/core.py): the path's event cash on the shared draws."""
    return event_trace(d, path, setup, model, draws).events
