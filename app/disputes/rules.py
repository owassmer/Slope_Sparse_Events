"""Rules as code for the post-judgment dispute model: every date window and amount on a dispute path.

The model contract (`contracts/dispute_model.json`) names each transition's rule; this module applies it. Windows are
anchored on docket dates from the record (the judgment date) or on code-owned windows where no rule sets a date (a
court's ruling); scenarios place each item early and late in its window. Amounts start from the documented amount and
apply the cited rule parameters (post-judgment interest, the supersedeas multiple, collateral share, settlement share).
Jev never sets any of these.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.config import CONTRACTS
from app.domain.investigation import BranchCash
from app.domain.values import Basis, EvidenceValue, Provenance, Status, Unit, cents_round

MODEL_PATH = CONTRACTS / "dispute_model.json"
BPS = Decimal(10_000)


def load_model() -> dict:
    return json.loads(MODEL_PATH.read_text())


@dataclass(frozen=True)
class Window:
    start: date
    end: date


def param(model: dict, key: str) -> int:
    return model["rules"][key]["value"]


def param_range(model: dict, key: str) -> tuple[int, int]:
    r = model["rules"][key]
    return r["lower"], r["upper"]


def amount_range(amount: EvidenceValue) -> tuple[int, int]:
    if amount.value is not None:
        return int(amount.value), int(amount.value)
    return int(amount.lower), int(amount.upper)


def _derived(lo: int, hi: int, derivation: str) -> EvidenceValue:
    if lo == hi:
        return EvidenceValue(status=Status.EXACT, unit=Unit.CENTS, value=lo,
                             provenance=Provenance(basis=Basis.DERIVED, derivation=derivation))
    return EvidenceValue(status=Status.RANGE, unit=Unit.CENTS, lower=lo, upper=hi,
                         provenance=Provenance(basis=Basis.DERIVED, derivation=derivation))


def _scale(amount: EvidenceValue, lo_bps: int, hi_bps: int) -> tuple[int, int]:
    lo, hi = amount_range(amount)
    return cents_round(Decimal(lo) * lo_bps / BPS), cents_round(Decimal(hi) * hi_bps / BPS)


def _with_interest(model: dict, amount: EvidenceValue, judged: Window, paid: Window) -> tuple[int, int]:
    """28 U.S.C. § 1961: simple interest from entry of judgment to payment, at the rule's rate range."""
    r_lo, r_hi = param_range(model, "post_judgment_interest_bps_per_year")
    lo, hi = amount_range(amount)
    d_lo, d_hi = max((paid.start - judged.end).days, 0), max((paid.end - judged.start).days, 0)
    return (lo + cents_round(Decimal(lo) * r_lo / BPS * d_lo / 365),
            hi + cents_round(Decimal(hi) * r_hi / BPS * d_hi / 365))


def next_window(model: dict, rule: str, to: str, at: Window, review: date) -> Window:
    """When the next stage begins. A ruling falls in the code-owned ruling window; enforcement and a bonded appeal
    begin when the automatic stay ends (Fed. R. Civ. P. 62(a))."""
    stay = timedelta(days=param(model, "automatic_stay_days"))
    if rule == "ruling":
        return Window(review + timedelta(days=1), review + timedelta(days=param(model, "ruling_window_days")))
    if rule == "bond" or to == "enforcement":
        return Window(at.start + stay, at.end + stay)
    return at


def cash_for(model: dict, rule: str, role: str, amount: EvidenceValue, at: Window, review: date, horizon: date,
             finding_ids: tuple[str, ...]) -> list[BranchCash]:
    """The dated cash one transition moves for the borrower, from the rule and the borrower's side. Items whose
    window opens after the horizon are left out; windows are clipped to the horizon."""
    stay = timedelta(days=param(model, "automatic_stay_days"))
    first = review + timedelta(days=1)
    pays = "outflow" if role == "debtor" else "inflow"
    items: list[tuple[str, str, int, int, Window, str]] = []

    def win(start: date, end: date) -> Window:
        return Window(max(start, first), min(end, horizon))

    if rule == "pay" or rule == "collect":
        days = param(model, "voluntary_payment_days_after_stay" if rule == "pay" else "enforcement_period_days")
        w = win(at.start + (stay if rule == "pay" else timedelta(0)), at.end + (stay if rule == "pay" else timedelta(0))
                + timedelta(days=days))
        lo, hi = _with_interest(model, amount, at, w)
        how = ("paid after the automatic stay (Fed. R. Civ. P. 62(a))" if rule == "pay"
               else "collected by enforcement (Fed. R. Civ. P. 69(a))")
        items.append((pays, "Judgment " + ("paid" if rule == "pay" else "collected"), lo, hi, w,
                      f"The judgment amount plus post-judgment interest (28 U.S.C. § 1961), {how}, within {days} days."))
    elif rule == "bond" and role == "debtor":
        lo, hi = bond_collateral(model, amount)
        items.append(("lock", "Appeal bond collateral", lo, hi, win(at.start, at.end + stay),
                      "Supersedeas bond at 125% of the judgment (Fed. R. Civ. P. 62(b), model multiple), with 50% to "
                      "100% taken as cash collateral, posted before the automatic stay ends; held while the appeal is "
                      "pending."))
    elif rule in ("settle", "settle_release"):
        s_lo, s_hi = param_range(model, "settlement_share_bps")
        lo, hi = _scale(amount, s_lo, s_hi)
        w = win(at.start, at.end + timedelta(days=param(model, "settlement_window_days")))
        items.append((pays, "Settlement payment", lo, hi, w,
                      "60% to 100% of the amount (model settlement range), paid within 90 days of the stage's start."))
        if rule == "settle_release" and role == "debtor":
            c_lo, c_hi = bond_collateral(model, amount)
            items.append(("inflow", "Bond collateral returned", c_lo, c_hi, Window(w.end, w.end),
                          "The bond is discharged when the judgment is settled, and the collateral is returned."))
    out = []
    for kind, label, lo, hi, w, why in items:
        if w.start > horizon or w.end < w.start:
            continue
        out.append(BranchCash(kind=kind, label=label, amount=_derived(lo, hi, why), window_start=w.start,
                              window_end=w.end, rule=why, finding_ids=finding_ids))
    return out


def bond_collateral(model: dict, amount: EvidenceValue) -> tuple[int, int]:
    m = param(model, "supersedeas_multiple_bps")
    c_lo, c_hi = param_range(model, "bond_collateral_share_bps")
    lo, hi = amount_range(amount)
    return (cents_round(Decimal(lo) * m / BPS * c_lo / BPS), cents_round(Decimal(hi) * m / BPS * c_hi / BPS))
