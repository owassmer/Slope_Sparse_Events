"""Actual merchant-loan contract adapter: the May 1, 2024 WebBank / Synergy CHC agreement.

Contract rules (source: synergy_merchant_agreement_20240501, cover, Section 1 definitions,
Sections 4.1.1, 4.1.2 and 4.2.1):
- Daily Payment = 25% of the day's gross Shopify Account Credits. Refunds, returns and
  cancellations do not reduce it. A payment due on a non-Business Day transfers the next one.
- Minimum Payment = 30% of the Total Payment Amount in months 1-6 from the Effective Date, and
  an additional 30% in months 7-12. Each window is measured on its own payments (4.2.1(b));
  first-window excess does not satisfy the second window.
- Total Payment Amount is due before the end of the 18-month Term. No collection beyond the
  outstanding total; payoff ends all further obligations.
- Months run from the Effective Date (actual funding), which is not assumed to be the May 1
  agreement date.

Not inferred: principal/fee allocation, APR, lien priority, a fee rebate on early payoff, or
daily credits. Account Credits are their own input type; consolidated revenue is rejected.
Declared conventions: daily payments round half up to the cent; a window's Minimum Payment
top-up is due on the last business day inside that window ("within" the six-month period); the
Term balance is due on the last business day before the Term ends.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.domain.values import EvidenceValue, UnknownInput, cents_round
from app.finance.calendar import add_months, next_business_day, previous_business_day

CHECKPOINT_FRACTIONS = {6: Decimal("0.30"), 12: Decimal("0.60"), 18: Decimal("1")}


class MerchantTerms(BaseModel):
    model_config = ConfigDict(frozen=True)

    loan_id: str = "synergy_webbank_shopify_2024-05-01"
    advance_cents: int = 37_000_000
    fixed_cost_cents: int = 4_810_000
    total_payment_cents: int = 41_810_000
    daily_payment_fraction: Decimal = Decimal("0.25")
    minimum_payment_fraction: Decimal = Decimal("0.30")
    window_months: int = 6
    windows: int = 2
    term_months: int = 18
    source_fact_ids: tuple[str, ...] = (
        "synergy_webbank_advance", "synergy_webbank_total_repayment", "synergy_webbank_cost_of_funds",
        "synergy_webbank_receipts_fraction", "synergy_webbank_term_months",
        "synergy_webbank_6m_min_fraction", "synergy_webbank_second_window_min_fraction")

    def model_post_init(self, _ctx) -> None:
        if self.advance_cents + self.fixed_cost_cents != self.total_payment_cents:
            raise ValueError("Advance plus fixed cost must equal the Total Payment Amount")
        if not Decimal(0) < self.daily_payment_fraction <= 1:
            raise ValueError("Daily payment fraction must be in (0, 1]")

    @property
    def minimum_payment_cents(self) -> int:
        return cents_round(self.total_payment_cents * self.minimum_payment_fraction)

    # --- contract thresholds (reproduce the kit's reference calculator) ---------------------

    def cumulative_floor_cents(self, month: int) -> int:
        if month not in CHECKPOINT_FRACTIONS:
            raise ValueError("Checkpoints are months 6, 12 and 18")
        return cents_round(self.total_payment_cents * CHECKPOINT_FRACTIONS[month])

    def qualifying_credit_threshold_cents(self, month: int) -> int:
        """Account Credits needed if the floor is met entirely through Daily Payments."""
        return cents_round(self.cumulative_floor_cents(month) / self.daily_payment_fraction)

    def cumulative_shortfall_only_cents(self, month: int, daily_received: int, manual_received: int) -> int:
        """Necessary cumulative check only. NOT sufficient at month 12 (see window_top_up_cents)."""
        if min(daily_received, manual_received) < 0 or daily_received + manual_received > self.total_payment_cents:
            raise ValueError("Payments must be nonnegative and within the contractual total")
        return max(0, self.cumulative_floor_cents(month) - daily_received - manual_received)

    def window_top_up_cents(self, window: int, daily_in_window: int, manual_credit_in_window: int,
                            total_received_to_date: int) -> int:
        """Section 4.2.1(b) top-up for one six-month window, capped at the remaining total."""
        if window not in (1, 2):
            raise ValueError("Windows are 1 (months 1-6) and 2 (months 7-12)")
        if min(daily_in_window, manual_credit_in_window, total_received_to_date) < 0:
            raise ValueError("Amounts must be nonnegative")
        if daily_in_window + manual_credit_in_window > total_received_to_date or \
                total_received_to_date > self.total_payment_cents:
            raise ValueError("Window credits must fit within receipts and the contractual total")
        shortfall = max(0, self.minimum_payment_cents - daily_in_window - manual_credit_in_window)
        return min(self.total_payment_cents - total_received_to_date, shortfall)

    # --- calendar ---------------------------------------------------------------------------

    def window_bounds(self, funding: date, window: int) -> tuple[date, date]:
        """[start, end) of a six-month window from the Effective Date."""
        return (add_months(funding, self.window_months * (window - 1)),
                add_months(funding, self.window_months * window))

    def term_end(self, funding: date) -> date:
        return add_months(funding, self.term_months)

    def window_of(self, funding: date, d: date) -> int | None:
        for w in range(1, self.windows + 1):
            start, end = self.window_bounds(funding, w)
            if start <= d < end:
                return w
        return None


class AccountCredits(BaseModel):
    """Gross Shopify Account Credits for the store/account the loan sweeps. Not revenue."""

    model_config = ConfigDict(frozen=True)

    store_scope: str
    covers: tuple[date, date]  # every day in this range is described; absent days inside it are zero
    daily_gross_cents: dict[date, int]
    provenance_note: str

    def model_post_init(self, _ctx) -> None:
        if any(not self.covers[0] <= d <= self.covers[1] for d in self.daily_gross_cents):
            raise ValueError("Account Credits fall outside their declared coverage")


class ConsolidatedRevenue(BaseModel):
    """Company-wide revenue. Exists so it can be rejected where Account Credits are required."""

    model_config = ConfigDict(frozen=True)

    daily_cents: dict[date, int]


class MerchantLoanState(BaseModel):
    """Seasoned-loan state at the end of `as_of`. Milestone clocks run from the funding date,
    never from the analysis date. Every field is evidence or an explicit assumption."""

    model_config = ConfigDict(frozen=True)

    as_of: date
    funding_date: EvidenceValue
    received_to_date_cents: EvidenceValue
    window_daily_cents: dict[int, EvidenceValue]
    window_manual_credit_cents: dict[int, EvidenceValue]
    unmet_prior_obligation_cents: EvidenceValue


class MerchantRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    on: date
    kind: str  # daily_payment | manual_payment | window_top_up | prior_unmet_obligation | term_balance
    amount_cents: int
    window: int | None
    received_to_date_cents: int
    outstanding_cents: int


class MerchantProjection(BaseModel):
    loan_id: str
    funding_date: date
    rows: list[MerchantRow]
    window_results: dict[int, dict[str, int]]
    outstanding_at_horizon_cents: int
    paid_off_on: date | None
    conventions: list[str]


def project(terms: MerchantTerms, state: MerchantLoanState, credits: AccountCredits,
            manual_payments: dict[date, int] | None = None, *, horizon: date) -> MerchantProjection:
    """Contractual payments owed from the day after `state.as_of` through `horizon`.

    These are contractual obligations under the supplied credits. Whether the borrower can pay a
    top-up or term balance is a capacity question for the cash ledger, not decided here.
    """
    if isinstance(credits, ConsolidatedRevenue) or not isinstance(credits, AccountCredits):
        raise TypeError("Daily Payments apply to contract-defined Shopify Account Credits, not revenue")
    funding = state.funding_date.require("merchant effective funding date")
    received = state.received_to_date_cents.require("merchant receipts to date")
    arrears = state.unmet_prior_obligation_cents.require("merchant unmet prior obligations")
    daily_w = {w: state.window_daily_cents[w].require(f"window {w} daily payments") for w in (1, 2)}
    manual_w = {w: state.window_manual_credit_cents[w].require(f"window {w} manual credit") for w in (1, 2)}
    if not funding <= state.as_of:
        raise ValueError("The seasoned state must be measured on or after the funding date")
    if received > terms.total_payment_cents:
        raise ValueError("Receipts to date exceed the Total Payment Amount")
    manual_payments = manual_payments or {}

    outstanding = terms.total_payment_cents - received
    rows: list[MerchantRow] = []
    pending: dict[date, int] = {}  # transfer date -> daily payments awaiting transfer
    start = state.as_of + timedelta(days=1)
    term_end = terms.term_end(funding)
    term_due = previous_business_day(term_end - timedelta(days=1))
    window_due = {w: previous_business_day(terms.window_bounds(funding, w)[1] - timedelta(days=1)) for w in (1, 2)}
    top_up_due = {due: w for w, due in window_due.items() if due > state.as_of}
    windows_done = {w for w, due in window_due.items() if due <= state.as_of}
    for w in windows_done:  # a closed window's shortfall cannot vanish: it must be declared as arrears
        if arrears == 0 and terms.window_top_up_cents(w, daily_w[w], manual_w[w], received) > 0:
            raise ValueError(f"Window {w} closed below its Minimum Payment; declare the unmet amount in "
                             "unmet_prior_obligation_cents")
    needed_through = min(horizon, term_due)
    if needed_through >= start and (credits.covers[0] > start or credits.covers[1] < needed_through):
        raise UnknownInput("Account Credits", f"credits must cover {start}..{needed_through}; days outside "
                           f"{credits.covers[0]}..{credits.covers[1]} are unknown, not zero")

    def pay(on: date, kind: str, amount: int, window: int | None) -> None:
        nonlocal outstanding, received
        amount = min(amount, outstanding)
        if amount <= 0:
            return
        outstanding -= amount
        received += amount
        rows.append(MerchantRow(on=on, kind=kind, amount_cents=amount, window=window,
                                received_to_date_cents=received, outstanding_cents=outstanding))

    if arrears:
        pay(next_business_day(start), "prior_unmet_obligation", arrears, None)
    if term_due <= state.as_of and outstanding:  # Term already ended: the whole balance is past due
        pay(next_business_day(start), "term_balance_past_due", outstanding, None)

    d = start
    while d <= horizon and outstanding > 0:
        gross = credits.daily_gross_cents.get(d, 0)
        if gross < 0:
            raise ValueError("Gross Account Credits cannot be negative (refunds do not reduce them)")
        if gross:
            t = next_business_day(d)
            pending[t] = pending.get(t, 0) + cents_round(gross * terms.daily_payment_fraction)
        w = terms.window_of(funding, d)
        if d in pending:
            before = outstanding
            pay(d, "daily_payment", pending.pop(d), w)
            if w:
                daily_w[w] += before - outstanding
        if d in manual_payments:
            before = outstanding
            pay(d, "manual_payment", manual_payments[d], w)
            if w:
                manual_w[w] += before - outstanding
        if d in top_up_due:
            k = top_up_due[d]
            owed = terms.window_top_up_cents(k, daily_w[k], manual_w[k], received)
            pay(d, "window_top_up", owed, k)
            manual_w[k] += owed  # the make-up payment cures its own window, not the next one
            windows_done.add(k)
        if d == term_due:
            pay(d, "term_balance", outstanding, None)
        d += timedelta(days=1)

    results = {w: {"required_cents": terms.minimum_payment_cents, "daily_cents": daily_w[w],
                   "manual_and_top_up_cents": manual_w[w], "evaluated": int(w in windows_done)}
               for w in (1, 2)}
    paid_off = rows[-1].on if rows and outstanding == 0 else None
    return MerchantProjection(
        loan_id=terms.loan_id, funding_date=funding, rows=rows, window_results=results,
        outstanding_at_horizon_cents=outstanding, paid_off_on=paid_off,
        conventions=[
            "Daily payment = 25% of gross Account Credits, rounded half up to the cent.",
            "Credits on a non-Business Day transfer on the next Business Day; the transfer date sets the window.",
            "Window top-up due on the last Business Day inside the window; it cures that window only.",
            "Term balance due on the last Business Day before the Term ends.",
            f"Effective funding date {funding.isoformat()} ({state.funding_date.provenance.basis}).",
        ])


def require_known_state(state: MerchantLoanState) -> list[str]:
    """Names of the state inputs that are still unknown (empty when a projection can run)."""
    missing = []
    for name, ev in [("funding_date", state.funding_date), ("received_to_date", state.received_to_date_cents),
                     ("unmet_prior_obligation", state.unmet_prior_obligation_cents),
                     *[(f"window_{w}_daily", v) for w, v in state.window_daily_cents.items()],
                     *[(f"window_{w}_manual", v) for w, v in state.window_manual_credit_cents.items()]]:
        try:
            ev.require(name)
        except UnknownInput:
            missing.append(name)
    return missing
