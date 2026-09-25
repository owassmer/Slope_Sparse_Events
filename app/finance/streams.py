"""Builders that turn engine outputs into ledger cash streams with consistent routing."""

from __future__ import annotations

from datetime import date
from typing import Literal

from app.domain.values import Basis
from app.finance.fixed_installment import FixedInstallmentOffer
from app.finance.ledger import CashStream
from app.finance.merchant import MerchantProjection
from app.finance.settlements import SettlementPayment


def settlement_stream(obligation_id: str, payments: list[SettlementPayment]) -> CashStream:
    bases = sorted({p.timing_basis for p in payments})
    return CashStream(stream_id=f"settlement:{obligation_id}", kind="settlement_payment",
                      rows=tuple((p.on, p.amount_cents) for p in payments), basis=Basis.OPERATOR,
                      note=f"Existing settlement liability scheduled once; timing {', '.join(bases) or 'n/a'}",
                      obligation_id=obligation_id)


def merchant_stream(projection: MerchantProjection) -> CashStream:
    return CashStream(stream_id=f"existing_loan:{projection.loan_id}", kind="existing_loan_payment",
                      rows=tuple((r.on, r.amount_cents) for r in projection.rows), basis=Basis.DERIVED,
                      note="Contractual merchant-loan payments from supplied Account Credits",
                      obligation_id=projection.loan_id)


def new_draw_streams(offer: FixedInstallmentOffer, *, funding_date: date, route: Literal["bank", "direct_to_vendor"],
                     purchase_cost_cents: int, purchase_date: date,
                     purchase_receipts: list[tuple[date, int]], receipts_basis: Basis = Basis.OPERATOR) -> list[CashStream]:
    """Streams for one proposed draw and the purchase it finances.

    bank: proceeds arrive in the borrower's account and the purchase is paid from it.
    direct_to_vendor: the lender pays the vendor; neither the proceeds nor the financed part of the
    purchase touches the bank. Any purchase cost above the advance is paid by the borrower either way.
    The purchase's incremental receipts belong to this action: a smaller or declined draw changes them.
    """
    ref = offer.proposal_id
    if purchase_date < funding_date:
        raise ValueError(f"{ref}: the financed purchase cannot precede funding")
    if route == "direct_to_vendor" and purchase_cost_cents < offer.advance_cents:
        raise ValueError(f"{ref}: direct-to-vendor funding of a purchase smaller than the advance needs an explicit "
                         "route for the remainder; use the bank route or reduce the advance")
    financed = min(purchase_cost_cents, offer.advance_cents)
    excess = purchase_cost_cents - financed
    streams = [CashStream(stream_id=f"new_loan:{ref}:payments", kind="new_loan_payment",
                          rows=tuple((p.due, p.amount_cents) for p in offer.schedule(funding_date)),
                          basis=Basis.OPERATOR, note="Contractual schedule of the proposed draw",
                          obligation_id=f"new_loan:{ref}", loan_ref=ref)]
    if route == "bank":
        streams += [
            CashStream(stream_id=f"new_loan:{ref}:disbursement", kind="new_loan_disbursement",
                       rows=((funding_date, offer.advance_cents),), basis=Basis.OPERATOR,
                       note="Proceeds paid to the borrower's bank account", route="bank", loan_ref=ref),
            CashStream(stream_id=f"new_loan:{ref}:purchase", kind="financed_purchase",
                       rows=((purchase_date, purchase_cost_cents),), basis=Basis.OPERATOR,
                       note="Financed purchase paid from the bank account", route="bank", loan_ref=ref),
        ]
    elif excess:
        streams.append(CashStream(stream_id=f"new_loan:{ref}:purchase_excess", kind="financed_purchase",
                                  rows=((purchase_date, excess),), basis=Basis.OPERATOR,
                                  note="Purchase cost above the advance, paid by the borrower",
                                  route="direct_to_vendor", loan_ref=ref))
    if purchase_receipts:
        streams.append(CashStream(stream_id=f"new_loan:{ref}:purchase_receipts", kind="financed_purchase_receipts",
                                  rows=tuple(purchase_receipts), basis=receipts_basis,
                                  note="Incremental customer receipts from the financed purchase", loan_ref=ref))
    return streams
