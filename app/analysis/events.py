"""Event cash: the dated cash, collateral and credit-capacity effects of one dispute path, per operating draw.

Code owns every date and amount. Dates are drawn uniformly inside the model's windows; a parent event's date is drawn
once (keyed by dispute, node and purpose) and its children's windows are set from it, so every path sharing a parent
shares its draw, and changing a probability never moves a date. Amounts: the documented figure (scaled by the exposure
control for disputes where the borrower pays), settlements at 60%-100% of it, surety collateral at 50%-100% of a 125%
bond, a cash deposit locking the amount, a letter of credit committing 125% of it as credit capacity, and post-judgment
interest unless the figure already includes it. A settlement during a secured appeal releases exactly what that path
encumbered, on the settlement date. Anything dated after the horizon is outside the period.

Stress mode places each effect adversely for the borrower (payments and encumbrances early and high, receipts late and
low) instead of drawing it.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

import numpy as np

from app.analysis.setup import SEED, Setup
from app.disputes.forecast import DisputePath
from app.disputes.rules import param, param_range
from app.domain.investigation import DisputeInstance


@dataclass
class EventCash:
    """Per draw and day: borrower cash (+ receipt, - payment), encumbrance changes (+ lock, - release) and credit
    capacity changes (+ commit, - release). Shape [draws, horizon days], integer cents."""
    cash: np.ndarray
    lock: np.ndarray
    capacity: np.ndarray

    @classmethod
    def zeros(cls, draws: int, days: int) -> EventCash:
        z = np.zeros((draws, days), dtype=np.int64)
        return cls(z.copy(), z.copy(), z.copy())

    def __add__(self, other: EventCash) -> EventCash:
        return EventCash(self.cash + other.cash, self.lock + other.lock, self.capacity + other.capacity)


class Draws:
    """Uniform draws keyed by (dispute, node, purpose), identical across paths, probabilities and controls."""

    def __init__(self, draws: int, stress: bool = False) -> None:
        self.n, self.stress, self.cache = draws, stress, {}

    def u(self, *key: str, adverse_high: bool | None = None) -> np.ndarray:
        if self.stress and adverse_high is not None:
            return np.full(self.n, 1.0 if adverse_high else 0.0)
        k = ":".join(key)
        if k not in self.cache:
            self.cache[k] = np.random.default_rng([SEED, zlib.crc32(k.encode())]).random(self.n)
        return self.cache[k]


def amount_cents(d: DisputeInstance, setup: Setup) -> int:
    a = int(d.amount.value if d.amount.value is not None else d.amount.upper)
    return int(round(a * setup.exposure_scale)) if d.borrower_role == "debtor" else a


def event_cash(d: DisputeInstance, path: DisputePath, setup: Setup, model: dict, draws: Draws) -> EventCash:
    n, days = draws.n, (setup.horizon - setup.review).days
    out = EventCash.zeros(n, days)
    rows = np.arange(n)
    debtor = d.borrower_role == "debtor"
    sign = -1 if debtor else 1
    amount = amount_cents(d, setup)
    iid = d.instance_id
    notice, stay_days = param(model, "appeal_notice_days"), param(model, "automatic_stay_days")
    vol, enf = param(model, "voluntary_payment_days_after_stay"), param(model, "enforcement_period_days")
    settle_days = param(model, "settlement_window_days")
    s_lo, s_hi = (x / 10_000 for x in param_range(model, "settlement_share_bps"))
    c_lo, c_hi = setup.collateral_share or tuple(x / 10_000 for x in param_range(model, "bond_collateral_share_bps"))
    r_lo, r_hi = (x / 10_000 for x in param_range(model, "post_judgment_interest_bps_per_year"))
    multiple = param(model, "supersedeas_multiple_bps") / 10_000

    def offset(a, b, *key):
        """A day offset from the review date (1 = the day after) drawn inside [a, b] clipped to the horizon; payments
        early in stress. A window that opens after the horizon returns a day past it, so nothing is booked there and
        nothing that depends on it (a release, a payment) is either."""
        a, b = np.maximum(np.asarray(a), 1), np.minimum(np.maximum(np.asarray(b), 1), days)
        u = draws.u(iid, *key, adverse_high=not debtor)
        inside = np.minimum(a + np.floor(u * (b - a + 1)).astype(np.int64), b)
        return np.where(a > b, np.maximum(a, days + 1), inside)  # a window opening after the horizon books nothing

    def book(arr, when, cents):
        idx = when - 1
        ok = idx < days
        np.add.at(arr, (rows[ok], idx[ok]), np.asarray(cents)[ok] if np.ndim(cents) else cents)

    def share(*key):
        return s_lo + draws.u(iid, *key, adverse_high=debtor) * (s_hi - s_lo)

    def with_interest(judged, paid, *key):
        if d.amount_includes_interest:
            return np.full(n, amount, dtype=np.int64)
        r = r_lo + draws.u(iid, *key, adverse_high=debtor) * (r_hi - r_lo)
        return np.rint(amount * (1 + r * np.maximum(paid - judged, 0) / 365)).astype(np.int64)

    if d.stage == "amount_pending":
        judged = offset(1, days, "amount_fixed", "date")
    elif d.judgment_date is not None:
        judged = np.full(n, (d.judgment_date - setup.review).days, dtype=np.int64)
    else:
        judged = np.ones(n, dtype=np.int64)
    stay = np.ones(n, dtype=np.int64)
    encumbrance, encumbered_kind = np.zeros(n, dtype=np.int64), None
    for node, _, branch in path.steps:
        if node == "settle_before_ruling" and branch == "yes":
            when = offset(1, settle_days, node, "date")
            book(out.cash, when, np.rint(sign * amount * share(node, "amount")).astype(np.int64))
        elif node == "settle_after_judgment" and branch == "yes":
            when = offset(judged, judged + notice, node, "date")
            book(out.cash, when, np.rint(sign * amount * share(node, "amount")).astype(np.int64))
        elif node == "secured_stay" and branch == "yes":
            stay = offset(judged, judged + stay_days, node, "date")
        elif node == "security_form" and debtor:
            if branch == "cash_deposit":
                encumbrance, encumbered_kind = np.full(n, amount, dtype=np.int64), "lock"
            elif branch == "surety_bond":
                c = c_lo + draws.u(iid, node, "collateral", adverse_high=True) * (c_hi - c_lo)
                encumbrance, encumbered_kind = np.rint(amount * multiple * c).astype(np.int64), "lock"
            else:
                encumbrance, encumbered_kind = np.full(n, int(round(amount * multiple)), dtype=np.int64), "capacity"
            book(out.lock if encumbered_kind == "lock" else out.capacity, stay, encumbrance)
        elif node == "settle_during_appeal" and branch == "yes":
            when = offset(stay, days, node, "date")
            book(out.cash, when, np.rint(sign * amount * share(node, "amount")).astype(np.int64))
            if encumbered_kind:  # the bond is discharged when the settlement is paid
                book(out.lock if encumbered_kind == "lock" else out.capacity, when, -encumbrance)
        elif node == "voluntary_payment" and branch == "yes":
            when = offset(judged + stay_days, judged + stay_days + vol, node, "date")
            book(out.cash, when, sign * with_interest(judged, when, node, "interest"))
        elif node == "enforcement" and branch == "yes":
            when = offset(judged + stay_days, judged + stay_days + enf, node, "date")
            book(out.cash, when, sign * with_interest(judged, when, node, "interest"))
    return out
