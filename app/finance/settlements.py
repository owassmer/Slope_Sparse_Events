"""Settlement payment calendar for existing settlement liabilities.

A settlement is an existing liability already on the balance sheet. The model assigns its
remaining annual buckets (as measured on a stated date) to dated cash outflows. It never adds the
liability again as new debt.

Bridge: buckets are measured on `measured_on` (June 30 for Synergy). Payments made between that
date and the opening-cash observation have already left the bank balance the ledger starts from,
so they reduce the first bucket exactly once. That amount is an input: unknown until supplied,
never assumed zero. It is bounded by the first bucket; prepaying later buckets needs its own
explicit input.

Timing within a bucket is unknown in the public evidence. A timing scenario (early, late, even
monthly, or an explicit schedule) places each bucket inside its window and is labelled as an
assumption. No historical installment date is invented.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domain.values import EvidenceValue, UnknownInput
from app.finance.calendar import month_ends, next_business_day, previous_business_day

Timing = Literal["early", "late", "monthly_even"]


class SettlementObligation(BaseModel):
    model_config = ConfigDict(frozen=True)

    obligation_id: str
    label: str
    measured_on: date
    remaining_by_year_cents: dict[int, int]  # contractual schedule as measured on measured_on
    source_fact_ids: tuple[str, ...] = ()

    @property
    def total_remaining_cents(self) -> int:
        return sum(self.remaining_by_year_cents.values())


class SettlementPayment(BaseModel):
    model_config = ConfigDict(frozen=True)

    obligation_id: str
    on: date
    amount_cents: int
    bucket_year: int
    timing_basis: str


def remaining_after_bridge(obligation: SettlementObligation, paid_since_measurement: EvidenceValue) -> dict[int, int]:
    """Buckets still owed after the payments made between measurement and the opening-cash date."""
    paid = paid_since_measurement.require(f"{obligation.obligation_id} payments since {obligation.measured_on}")
    first = min(obligation.remaining_by_year_cents)
    if not 0 <= paid <= obligation.remaining_by_year_cents[first]:
        raise ValueError(
            f"{obligation.obligation_id}: bridge payment {paid} must be within the {first} bucket; "
            "prepayment of later buckets needs a separate explicit input")
    out = dict(obligation.remaining_by_year_cents)
    out[first] -= paid
    return out


def calendar(obligation: SettlementObligation, paid_since_measurement: EvidenceValue, *, start: date,
             timing: Timing | dict[int, list[tuple[date, int]]], through: date) -> list[SettlementPayment]:
    """Dated payments from `start` (first forecast day) through `through`.

    `timing` is a named scenario or, per bucket year, an explicit list of (date, cents) that must
    sum to that bucket and fall inside its window.
    """
    if start <= obligation.measured_on:
        raise ValueError("Forecast must start after the measurement date")
    buckets = remaining_after_bridge(obligation, paid_since_measurement)
    out: list[SettlementPayment] = []
    for year, amount in sorted(buckets.items()):
        if amount == 0:
            continue
        lo, hi = max(start, date(year, 1, 1)), date(year, 12, 31)
        if lo > hi:
            raise ValueError(f"{obligation.obligation_id}: {year} bucket still owed but its year has passed")
        if isinstance(timing, dict):
            rows = timing.get(year)
            if rows is None:
                raise UnknownInput(f"{obligation.obligation_id} {year} payment dates", "no timing supplied")
            if sum(a for _, a in rows) != amount or any(not lo <= d <= hi for d, _ in rows):
                raise ValueError(f"Explicit {year} schedule must sum to {amount} within {lo}..{hi}")
            placed, basis = rows, "explicit_schedule"
        elif timing == "early":
            placed, basis = [(next_business_day(lo), amount)], "scenario_early_in_window"
        elif timing == "late":
            placed, basis = [(previous_business_day(hi), amount)], "scenario_late_in_window"
        else:
            ends = [previous_business_day(d) for d in month_ends(lo, hi)]
            each = amount // len(ends)
            placed = [(d, each) for d in ends[:-1]] + [(ends[-1], amount - each * (len(ends) - 1))]
            basis = "scenario_even_monthly_in_window"
        out.extend(SettlementPayment(obligation_id=obligation.obligation_id, on=d, amount_cents=a,
                                     bucket_year=year, timing_basis=basis) for d, a in placed if d <= through)
    return out
