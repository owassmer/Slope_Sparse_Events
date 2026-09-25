"""Slope bill-pay financing: the loan's payment schedule, proration and APR equivalent for supplied terms.

Slope pays the supplier directly; the business repays on net terms (one payment) or in monthly installments, by ACH.
The fee is a share of the amount financed and is prorated if the business repays early. Money is integer cents; rates
are Decimal basis points; payment dates follow the business-day calendar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.domain.values import cents_round
from app.finance.calendar import add_months, next_business_day
from app.finance.fixed_installment import ScheduledPayment

BPS = Decimal(10_000)


@dataclass(frozen=True)
class SlopeOffer:
    offer_id: str
    term_id: str
    amount_cents: int
    fee_bps: int
    days: int
    installments: int
    tier: str

    @property
    def fee_cents(self) -> int:
        return cents_round(Decimal(self.amount_cents) * Decimal(self.fee_bps) / BPS)

    @property
    def total_cents(self) -> int:
        return self.amount_cents + self.fee_cents

    def schedule(self, funding: date) -> list[ScheduledPayment]:
        """Net terms: one payment `days` after funding. Installments: equal monthly payments (last absorbs rounding).
        Each payment carries principal pro rata; the final payment conserves both totals exactly."""
        n = self.installments
        if n == 1:
            dues = [next_business_day(funding + timedelta(days=self.days))]
        else:
            dues = [next_business_day(add_months(funding, k)) for k in range(1, n + 1)]
        base = cents_round(Decimal(self.total_cents) / n)
        amounts = [base] * (n - 1) + [self.total_cents - base * (n - 1)]
        out, principal_left = [], self.amount_cents
        for i, (due, amt) in enumerate(zip(dues, amounts, strict=True), 1):
            principal = principal_left if i == n else cents_round(Decimal(amt) * self.amount_cents / self.total_cents)
            principal_left -= principal
            out.append(ScheduledPayment(month_index=i, due=due, amount_cents=amt, principal_cents=principal,
                                        fee_cents=amt - principal))
        return out

    def prorated_fee_cents(self, days_outstanding: int) -> int:
        """Early repayment: the fee is charged only for the days the funds were outstanding."""
        share = Decimal(min(max(days_outstanding, 0), self.days)) / Decimal(self.days)
        return cents_round(Decimal(self.fee_cents) * share)

    def apr_equivalent_bps(self, funding: date) -> int:
        """Effective annual rate (ACT/365) that equates the payments to the amount financed. Shown beside the fee."""
        flows = [((p.due - funding).days, p.amount_cents) for p in self.schedule(funding)]
        lo, hi = Decimal(0), Decimal(5)
        for _ in range(80):
            mid = (lo + hi) / 2
            pv = sum(Decimal(a) / (1 + mid) ** (Decimal(d) / 365) for d, a in flows)
            lo, hi = (mid, hi) if pv > self.amount_cents else (lo, mid)
        return int(((lo + hi) / 2 * BPS).to_integral_value())
