"""Typed evidence values. Money is integer cents; rates are Decimal. Unknown is a state, never zero.

Mirrors the kit's `EvidenceValue` contract (status, unit, value, lower, upper, observed_on,
provenance). Code that needs a number calls `.require(name)`, which raises `UnknownInput` for an
unknown or range value instead of returning a default.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

CENT = Decimal("1")


class Status(StrEnum):
    EXACT = "exact"
    APPROXIMATE = "approximate"
    RANGE = "range"
    UNKNOWN = "unknown"


class Basis(StrEnum):
    DOCUMENTED = "documented_evidence"
    OPERATOR = "operator_assumption"
    DERIVED = "model_derived"
    UNKNOWN = "unknown"


class Unit(StrEnum):
    CENTS = "USD_cents"
    RATE = "decimal_rate"
    FRACTION = "decimal_fraction"
    DATE = "date"
    DAYS = "days"
    MONTHS = "months"


class UnknownInput(ValueError):
    """A calculation needed a value that is unknown or only bounded."""

    def __init__(self, name: str, reason: str = "") -> None:
        self.name = name
        super().__init__(f"{name} is unknown{': ' + reason if reason else ''}")


class Provenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    basis: Basis
    source_fact_ids: tuple[str, ...] = ()
    assumption_id: str | None = None
    derivation: str | None = None
    note: str | None = None


class EvidenceValue(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Status
    unit: Unit
    value: int | Decimal | date | None = None
    lower: int | Decimal | date | None = None
    upper: int | Decimal | date | None = None
    observed_on: date | None = None
    provenance: Provenance = Field(default_factory=lambda: Provenance(basis=Basis.UNKNOWN))

    @model_validator(mode="after")
    def _consistent(self) -> EvidenceValue:
        if self.status == Status.UNKNOWN:
            if self.value is not None:
                raise ValueError("An unknown value carries no number")
        elif self.status == Status.RANGE:
            if self.lower is None or self.upper is None or self.lower > self.upper:
                raise ValueError("A range needs lower <= upper")
        elif self.value is None:
            raise ValueError(f"{self.status} value needs a value")
        if self.unit == Unit.CENTS:
            for v in (self.value, self.lower, self.upper):
                if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
                    raise ValueError("Money is integer cents")
        if self.unit in (Unit.RATE, Unit.FRACTION):
            for v in (self.value, self.lower, self.upper):
                if v is not None and not isinstance(v, Decimal):
                    raise ValueError("Rates and fractions are Decimal, never float")
        if self.status != Status.UNKNOWN and self.provenance.basis == Basis.UNKNOWN:
            raise ValueError("A known value needs a basis")
        return self

    @property
    def known(self) -> bool:
        return self.status in (Status.EXACT, Status.APPROXIMATE)

    def require(self, name: str) -> Any:
        if not self.known:
            raise UnknownInput(name, self.provenance.note or f"status {self.status}")
        return self.value


def unknown(unit: Unit, note: str) -> EvidenceValue:
    return EvidenceValue(status=Status.UNKNOWN, unit=unit, provenance=Provenance(basis=Basis.UNKNOWN, note=note))


def documented(value: Any, unit: Unit, *, facts: tuple[str, ...] = (), observed_on: date | None = None,
               approximate: bool = False, note: str | None = None) -> EvidenceValue:
    return EvidenceValue(status=Status.APPROXIMATE if approximate else Status.EXACT, unit=unit, value=value,
                         observed_on=observed_on,
                         provenance=Provenance(basis=Basis.DOCUMENTED, source_fact_ids=facts, note=note))


def assumed(value: Any, unit: Unit, assumption_id: str, note: str, *, observed_on: date | None = None) -> EvidenceValue:
    return EvidenceValue(status=Status.EXACT, unit=unit, value=value, observed_on=observed_on,
                         provenance=Provenance(basis=Basis.OPERATOR, assumption_id=assumption_id, note=note))


def derived(value: Any, unit: Unit, derivation: str, *, facts: tuple[str, ...] = ()) -> EvidenceValue:
    return EvidenceValue(status=Status.EXACT, unit=unit, value=value,
                         provenance=Provenance(basis=Basis.DERIVED, source_fact_ids=facts, derivation=derivation))


def dec(value: str | int | Decimal) -> Decimal:
    if isinstance(value, bool | float):
        raise TypeError("Use Decimal, a decimal string or an integer, never float/bool")
    d = value if isinstance(value, Decimal) else Decimal(value)
    if not d.is_finite():
        raise ValueError("Must be finite")
    return d


def cents_round(amount: Decimal) -> int:
    """Round a Decimal cent amount to whole cents, half up."""
    return int(amount.quantize(CENT, rounding=ROUND_HALF_UP))


def usd(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) // 100:,}.{abs(cents) % 100:02d}"
