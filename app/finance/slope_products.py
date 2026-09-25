"""Slope bill-pay financing: the reconstructed menu, price card, tiering, limits and payment schedules.

Slope pays the supplier directly; the business repays on net terms (one payment) or in monthly installments, by ACH.
The fee is a share of the amount financed, set by risk tier and term, and is prorated if the business repays early.
Terms, prices and policy come from `cases/slope_terms.json` (reconstructed from public sources, labelled there once).
Money is integer cents; rates are Decimal basis points; payment dates follow the business-day calendar.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.config import ROOT
from app.domain.values import cents_round
from app.finance.calendar import add_months, next_business_day
from app.finance.fixed_installment import ScheduledPayment

TERMS_PATH = ROOT / "cases" / "slope_terms.json"
BPS = Decimal(10_000)


def load_terms(path: Path = TERMS_PATH) -> dict:
    return json.loads(path.read_text())


def term_spec(terms: dict, term_id: str) -> dict:
    return next(t for t in terms["product"]["terms"] if t["term_id"] == term_id)


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


def tier_for(features: dict, terms: dict) -> tuple[str, dict]:
    """First tier whose every rule the bank features meet (months of cover, inflow volatility, negative days, debt)."""
    cover = Decimal(features["available_cash_cents"]) * BPS / Decimal(max(features["monthly_outflows_cents"], 1))
    measured = {"months_cover_bps": int(cover), "inflow_volatility_bps": features["inflow_volatility_bps"],
                "negative_balance_days": features["negative_balance_days"],
                "debt_service_share_bps": features["debt_service_share_bps"]}
    for rule in terms["tiering"]["rules"]:
        if (measured["months_cover_bps"] >= rule["min_months_cover_bps"]
                and measured["inflow_volatility_bps"] <= rule["max_inflow_volatility_bps"]
                and measured["negative_balance_days"] <= rule["max_negative_balance_days"]
                and measured["debt_service_share_bps"] <= rule["max_debt_service_share_bps"]):
            return rule["tier"], measured
    return terms["tiering"]["rules"][-1]["tier"], measured


def limits(features: dict, terms: dict) -> dict[str, int]:
    """Pre-approved limit and order limit from the policy's share of monthly bank inflows."""
    pol = terms["credit_policy"]
    limit = cents_round(Decimal(features["monthly_inflows_cents"]) * pol["limit_share_of_monthly_inflows_bps"] / BPS)
    return {"limit_cents": limit, "order_limit_cents": cents_round(Decimal(limit) * pol["order_limit_share_of_limit_bps"] / BPS)}


def offer(terms: dict, term_id: str, amount_cents: int, tier: str) -> SlopeOffer:
    spec = term_spec(terms, term_id)
    if spec["days"] > terms["credit_policy"]["max_tenor_days"]:
        raise ValueError(f"{term_id} exceeds the policy's maximum tenor")
    return SlopeOffer(offer_id=f"{term_id}_{amount_cents // 100}", term_id=term_id, amount_cents=amount_cents,
                      fee_bps=terms["price_card"]["tiers"][tier][term_id], days=spec["days"],
                      installments=spec["installments"], tier=tier)


def menu(terms: dict, amount_cents: int, tier: str) -> list[SlopeOffer]:
    return [offer(terms, t["term_id"], amount_cents, tier) for t in terms["product"]["terms"]]
