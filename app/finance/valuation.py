"""Loan-asset valuation primitives: present value and principal exposure.

    NPV = sum(asset_cash_flow[t] / (1 + required_return) ** (days_from_decision / 365))

Effective annual discount rate, ACT/365, from the decision date (day 0). A funding cost is either
in the ledger or in the hurdle, never both. Principal collections are return of capital, not
revenue.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, localcontext

from app.domain.values import dec


def present_value_cents(flows: list[tuple[int, int]], annual_rate: Decimal | str) -> Decimal:
    """flows: (day offset from decision, cents). Returns unrounded Decimal cents."""
    rate = dec(annual_rate)
    if rate < 0:
        raise ValueError("Discount rate must be nonnegative")
    with localcontext() as ctx:
        ctx.prec = 40
        return sum((Decimal(c) / ctx.power(1 + rate, Decimal(day) / Decimal(365)) for day, c in flows), Decimal(0))


def dated_flows(flows: list[tuple[date, int]], decision: date) -> list[tuple[int, int]]:
    out = []
    for d, c in flows:
        if d < decision:
            raise ValueError("Flows before the decision date belong to history, not valuation")
        out.append(((d - decision).days, c))
    return out


def principal_dollar_days(disbursements: list[tuple[int, int]], principal_receipts: list[tuple[int, int]]) -> int:
    """Integral of outstanding principal over time, in cent-days (dollar-days x 100).

    Uses principal only; mixed principal/fee payments need an explicit allocation first.
    """
    events = sorted([(d, c) for d, c in disbursements] + [(d, -c) for d, c in principal_receipts])
    total, outstanding, last = 0, 0, None
    for day, delta in events:
        if last is not None:
            total += outstanding * (day - last)
        outstanding += delta
        if outstanding < 0:
            raise ValueError("Principal receipts exceed disbursements")
        last = day
    if outstanding:
        raise ValueError("Principal not fully returned; exposure is open-ended at the horizon")
    return total
