"""Proposed Slope-style fixed-installment working-capital draw (an analytical offer, not a quote).

Pricing convention (declared, from the kit's proposal fixtures): a fixed total fee equal to a
stated fraction of the advance, repaid in equal monthly payments rounded half up to the cent,
with the final payment adjusted so payments sum exactly to advance + fee. The fee fraction is not
an APR. Payment rows start as month indices; calendar dates exist only once an operator or
scenario chooses a funding date.

Fee/principal split, used only for loan-asset economics: a declared demonstration convention
(each payment carries principal in proportion advance / total repayment; the last payment
absorbs rounding). It is labelled as a convention and never applied to the actual merchant loan.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.domain.values import cents_round
from app.finance.calendar import add_months, next_business_day

ALLOCATION_CONVENTION = "pro_rata_fee_demo_convention"


class ScheduledPayment(BaseModel):
    model_config = ConfigDict(frozen=True)

    month_index: int
    due: date | None
    amount_cents: int
    principal_cents: int
    fee_cents: int


class FixedInstallmentOffer(BaseModel):
    model_config = ConfigDict(frozen=True)

    proposal_id: str
    advance_cents: int
    term_months: int
    fee_fraction: Decimal
    basis: str = "operator_assumption"

    def model_post_init(self, _ctx) -> None:
        if self.advance_cents <= 0 or self.term_months <= 0:
            raise ValueError("Advance and term must be positive")
        if not isinstance(self.fee_fraction, Decimal) or self.fee_fraction < 0:
            raise ValueError("Fee fraction is a nonnegative Decimal")

    @property
    def fee_cents(self) -> int:
        return cents_round(self.advance_cents * self.fee_fraction)

    @property
    def total_repayment_cents(self) -> int:
        return self.advance_cents + self.fee_cents

    def payment_amounts(self) -> list[int]:
        regular = cents_round(Decimal(self.total_repayment_cents) / self.term_months)
        return [regular] * (self.term_months - 1) + [self.total_repayment_cents - regular * (self.term_months - 1)]

    def schedule(self, funding_date: date | None = None) -> list[ScheduledPayment]:
        """Contractual schedule. Without a funding date, rows carry month indices only.

        Dated convention (declared): payment k is due k calendar months after funding (day clamped
        to month end), moved to the next business day.
        """
        amounts = self.payment_amounts()
        out, principal_left = [], self.advance_cents
        for k, amount in enumerate(amounts, start=1):
            principal = principal_left if k == len(amounts) else min(
                principal_left, cents_round(Decimal(amount) * self.advance_cents / self.total_repayment_cents))
            principal_left -= principal
            due = next_business_day(add_months(funding_date, k)) if funding_date else None
            out.append(ScheduledPayment(month_index=k, due=due, amount_cents=amount,
                                        principal_cents=principal, fee_cents=amount - principal))
        assert sum(p.amount_cents for p in out) == self.total_repayment_cents
        assert sum(p.principal_cents for p in out) == self.advance_cents
        return out

    def minimum_residual_capacity_cents(self) -> int:
        """Largest single payment the borrower must be able to cover on a due date."""
        return max(self.payment_amounts())


def load_fixture_offers(path: Path) -> list[tuple[FixedInstallmentOffer, dict]]:
    """Load the kit's proposal fixtures; returns each offer with its raw fixture record."""
    data = json.loads(path.read_text())
    return [(FixedInstallmentOffer(proposal_id=o["proposal_id"], advance_cents=o["advance_cents"],
                                   term_months=o["term_months"], fee_fraction=Decimal(o["fixed_total_fee_fraction"]),
                                   basis=o["basis"]), o) for o in data["offers"]]
