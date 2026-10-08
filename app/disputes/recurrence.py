"""Case-declared recurrence bounds and explicit comparison scenarios."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RecurrenceBounds:
    settle: int | None = None
    cash_floor: int | None = None
    offering: int | None = None
    judgment_default: int | None = None

    def __post_init__(self):
        if any(v is not None and (not isinstance(v, int) or v < 1) for v in vars(self).values()):
            raise ValueError('bounds must be positive integers or None')

    @classmethod
    def from_model(cls, model, sens=None):
        from app.analysis.events import pval

        key = 'offering_initiations'
        if key not in model['parameters']:
            return cls()
        value = pval(model, key, (sens or {}).get(key, False))
        return cls(offering=None if value == 'unbounded' else value)

    def reached(self, node, steps):
        limit = getattr(self, node, None)
        return limit is not None and sum(n == node for n, _, _ in steps) >= limit


SCENARIOS = {
    'unbounded': RecurrenceBounds(),
    'settlement': RecurrenceBounds(settle=3),
    'floor': RecurrenceBounds(cash_floor=2),
    'offerings': RecurrenceBounds(offering=2),
    'notes': RecurrenceBounds(judgment_default=1),
    'together': RecurrenceBounds(settle=3, cash_floor=2, offering=2, judgment_default=1),
}
DECLARATIONS = {
    'unbounded': 'No recurrence bounds.',
    'settlement': 'Settlement at no more than three legal stages; thereafter no offer.',
    'floor': 'Cash-floor action on no more than two occasions; thereafter neither offering nor filing at the floor.',
    'offerings': 'At most two offerings initiated, including failed offerings; thereafter initiation is unavailable.',
    'notes': 'One judgment-default decision; thereafter holders take no further action on that default. Other defaults remain.',
    'together': 'All four recurrence bounds together.',
}
