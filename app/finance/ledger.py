"""Daily borrower cash ledger: dated cash available for repayment under explicit inputs.

    closing[t] = closing[t-1] + inflows[t] - outflows[t]
    headroom[t] = closing[t] - required_reserve

The ledger is an affordability test under stated assumptions, not a default model. A negative
closing balance is a financing deficit, not an implicit overdraft. A reserve breach is not a
legal default.

Guards against the spec's critical errors:
- No fake opening balance: opening cash must be a known observation dated the day before the
  declared forecast start (June 30 cash cannot open an August forecast), and its unavailable
  portion must be supplied. A flow dated on or before the observation is rejected, because the
  opening balance already reflects it.
- No duplicated obligation: each obligation ID appears in one stream only, and an aggregate
  "other debt service" stream must list every separately modelled obligation it excludes.
- No non-cash items: an accounting normalization (for example a settlement gain) never enters.
- One route per dollar: proceeds paid directly to a vendor are not also bank cash.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domain.values import Basis, EvidenceValue, UnknownInput

StreamKind = Literal[
    "operating_receipts", "other_inflow", "new_loan_disbursement", "financed_purchase_receipts",
    "operating_outflow", "other_debt_service", "settlement_payment", "existing_loan_payment",
    "new_loan_payment", "financed_purchase",
]
INFLOWS = {"operating_receipts", "other_inflow", "new_loan_disbursement", "financed_purchase_receipts"}
DEBT = {"settlement_payment", "existing_loan_payment", "new_loan_payment"}


class LedgerError(ValueError):
    pass


class DoubleCountError(LedgerError):
    pass


class CashStream(BaseModel):
    model_config = ConfigDict(frozen=True)

    stream_id: str
    kind: StreamKind
    rows: tuple[tuple[date, int], ...]  # (date, positive cents); direction comes from kind
    basis: Basis
    note: str
    obligation_id: str | None = None  # the single obligation a debt stream pays
    excludes_obligations: tuple[str, ...] = ()  # required on other_debt_service aggregates
    is_cash: bool = True
    route: Literal["bank", "direct_to_vendor"] | None = None  # new-loan proceeds and financed purchases
    loan_ref: str | None = None


class BorrowerCashBase(BaseModel):
    model_config = ConfigDict(frozen=True)

    borrower_id: str
    forecast_start: date
    opening_cash: EvidenceValue
    unavailable_opening_cash: EvidenceValue
    required_reserve: EvidenceValue
    end: date
    streams: tuple[CashStream, ...]


class LedgerRow(BaseModel):
    on: date
    inflow_cents: int
    outflow_cents: int
    by_kind: dict[str, int]
    closing_cents: int
    headroom_cents: int


class LedgerResult(BaseModel):
    status: Literal["computed", "not_computable"]
    missing_inputs: list[str] = []
    start: date | None = None
    end: date | None = None
    opening_available_cents: int | None = None
    reserve_cents: int | None = None
    rows: list[LedgerRow] = []
    min_headroom_cents: int | None = None
    min_headroom_on: date | None = None
    first_breach_on: date | None = None
    breach_dates: list[date] = []
    deficit_dates: list[date] = []
    closing_cents: int | None = None
    totals_by_kind: dict[str, int] = {}
    assumptions: list[str] = []

    def closing_on(self, d: date) -> int:
        """Closing balance at the end of d (carried forward across days without activity)."""
        if self.status != "computed" or self.start is None or d < self.start:
            raise ValueError("Ledger has no balance for that date")
        bal = self.opening_available_cents
        for r in self.rows:
            if r.on > d:
                break
            bal = r.closing_cents
        return bal


def validate(base: BorrowerCashBase) -> None:
    opened = base.opening_cash.observed_on
    if opened is None:
        raise LedgerError("Opening cash needs its observation date; the forecast starts the next day")
    if opened != base.forecast_start - timedelta(days=1):
        raise LedgerError(f"Opening cash observed on {opened} cannot open a forecast starting {base.forecast_start}: "
                          "bridge the flows in between or use an observation from the day before")
    seen: dict[str, str] = {}
    for s in base.streams:
        if not s.is_cash:
            raise LedgerError(f"{s.stream_id}: non-cash items (accounting normalizations) cannot enter the cash ledger")
        for d, cents in s.rows:
            if isinstance(cents, bool) or not isinstance(cents, int) or cents < 0:
                raise LedgerError(f"{s.stream_id}: amounts are nonnegative integer cents")
            if d <= opened:
                raise LedgerError(f"{s.stream_id}: {d} is on or before the opening-cash observation "
                                  f"({opened}); the opening balance already reflects it")
        if s.kind in DEBT:
            if not s.obligation_id:
                raise LedgerError(f"{s.stream_id}: debt streams name the obligation they pay")
            if s.obligation_id in seen:
                raise DoubleCountError(f"Obligation {s.obligation_id} appears in both {seen[s.obligation_id]} "
                                       f"and {s.stream_id}")
            seen[s.obligation_id] = s.stream_id
        if s.kind == "new_loan_disbursement" and s.route != "bank":
            raise DoubleCountError(f"{s.stream_id}: only bank-routed proceeds are borrower cash")
    for s in base.streams:
        if s.kind == "other_debt_service":
            uncovered = set(seen) - set(s.excludes_obligations)
            if uncovered:
                raise DoubleCountError(f"{s.stream_id} must state it excludes separately modelled obligations "
                                       f"{sorted(uncovered)}")
    loans = {s.loan_ref for s in base.streams if s.kind in ("new_loan_disbursement", "financed_purchase") and s.loan_ref}
    for ref in loans:
        disb = [s for s in base.streams if s.kind == "new_loan_disbursement" and s.loan_ref == ref]
        bank_purchase = [s for s in base.streams if s.kind == "financed_purchase" and s.loan_ref == ref
                         and s.route == "bank"]
        if bool(disb) != bool(bank_purchase):
            raise DoubleCountError(f"Loan {ref}: a bank disbursement and a bank-paid financed purchase go together; "
                                   "direct-to-vendor funding has neither")


def run(base: BorrowerCashBase) -> LedgerResult:
    validate(base)
    missing = []
    values = {}
    for name, ev in [("opening_cash", base.opening_cash), ("unavailable_opening_cash", base.unavailable_opening_cash),
                     ("required_reserve", base.required_reserve)]:
        try:
            values[name] = ev.require(name)
        except UnknownInput:
            missing.append(name)
    if missing:
        return LedgerResult(status="not_computable", missing_inputs=missing)
    if values["unavailable_opening_cash"] > values["opening_cash"]:
        raise LedgerError("Unavailable portion exceeds reported opening cash")

    start = base.forecast_start
    by_day: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for s in base.streams:
        for d, cents in s.rows:
            if d <= base.end:
                by_day[d][s.kind] += cents

    reserve = values["required_reserve"]
    balance = values["opening_cash"] - values["unavailable_opening_cash"]
    result = LedgerResult(status="computed", start=start, end=base.end, opening_available_cents=balance,
                          reserve_cents=reserve, min_headroom_cents=balance - reserve, min_headroom_on=start)
    totals: dict[str, int] = defaultdict(int)
    for d in sorted(by_day):
        kinds = by_day[d]
        inflow = sum(v for k, v in kinds.items() if k in INFLOWS)
        outflow = sum(v for k, v in kinds.items() if k not in INFLOWS)
        balance += inflow - outflow
        headroom = balance - reserve
        for k, v in kinds.items():
            totals[k] += v
        result.rows.append(LedgerRow(on=d, inflow_cents=inflow, outflow_cents=outflow, by_kind=dict(kinds),
                                     closing_cents=balance, headroom_cents=headroom))
        if headroom < result.min_headroom_cents:
            result.min_headroom_cents, result.min_headroom_on = headroom, d
        if headroom < 0:
            result.breach_dates.append(d)
            result.first_breach_on = result.first_breach_on or d
        if balance < 0:
            result.deficit_dates.append(d)
    result.closing_cents = balance
    result.totals_by_kind = dict(totals)
    result.assumptions = [f"{s.stream_id}: {s.basis} - {s.note}" for s in base.streams]
    return result
