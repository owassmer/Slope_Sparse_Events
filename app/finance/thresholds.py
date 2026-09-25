"""Conditional cash thresholds that stay useful without a full borrower forecast.

Remaining-period threshold (spec §5 worked threshold):

    required_net_cash = max(0, sum(bucket - paid_since_measurement) + reserve
                               - (opening_cash - unavailable_opening_cash))

`required_net_cash` is the cumulative net cash the business must generate over the period after
operating spending and other existing debt service, before new-loan service. It is a necessary
condition, not a proven shortfall, and it is not sufficient at each date (see the ledger).
Any unknown input returns an unknown result that names what is missing.
"""

from __future__ import annotations

from app.domain.values import EvidenceValue, Unit, UnknownInput, derived, unknown


def remaining_period_required_net_cash(buckets: list[tuple[str, int, EvidenceValue]], *,
                                       opening_cash: EvidenceValue, unavailable_opening_cash: EvidenceValue,
                                       reserve: EvidenceValue) -> EvidenceValue:
    """buckets: (obligation_id, remaining bucket cents at measurement, paid since measurement)."""
    missing, remaining = [], 0
    for oid, bucket, paid in buckets:
        try:
            p = paid.require(f"{oid} paid since measurement")
            if not 0 <= p <= bucket:
                raise ValueError(f"{oid}: paid amount must be within its bucket")
            remaining += bucket - p
        except UnknownInput:
            missing.append(f"{oid}_paid_since_measurement")
    vals = {}
    for name, ev in [("opening_cash", opening_cash), ("unavailable_opening_cash", unavailable_opening_cash),
                     ("reserve", reserve)]:
        try:
            vals[name] = ev.require(name)
        except UnknownInput:
            missing.append(name)
    if missing:
        return unknown(Unit.CENTS, "Required inputs unknown: " + ", ".join(missing))
    if vals["unavailable_opening_cash"] > vals["opening_cash"]:
        raise ValueError("Unavailable portion exceeds reported opening cash")
    available = vals["opening_cash"] - vals["unavailable_opening_cash"]
    result = max(0, remaining + vals["reserve"] - available)
    facts = tuple(f for ev in [opening_cash, unavailable_opening_cash, reserve, *[b[2] for b in buckets]]
                  for f in ev.provenance.source_fact_ids)
    return derived(result, Unit.CENTS,
                   f"max(0, remaining settlements {remaining} + reserve {vals['reserve']} - available opening cash "
                   f"{available})", facts=facts)


def horizon_residual(*, opening_cash: EvidenceValue, unavailable_opening_cash: EvidenceValue,
                     settlements_remaining: list[tuple[str, int, EvidenceValue]], customer_collections: EvidenceValue,
                     committed_financing: EvidenceValue, other_confirmed_inflows: EvidenceValue,
                     operating_outflows_excl_settlements_and_debt: EvidenceValue,
                     other_debt_service_excl_settlements: EvidenceValue, reserve: EvidenceValue) -> EvidenceValue:
    """Year-end cash residual before a new facility (kit reference identity). Not a maximum loan."""
    missing, vals = [], {}
    named = {"opening_cash": opening_cash, "unavailable_opening_cash": unavailable_opening_cash,
             "customer_collections": customer_collections, "committed_financing": committed_financing,
             "other_confirmed_inflows": other_confirmed_inflows,
             "operating_outflows": operating_outflows_excl_settlements_and_debt,
             "other_debt_service": other_debt_service_excl_settlements, "reserve": reserve}
    for name, ev in named.items():
        try:
            vals[name] = ev.require(name)
        except UnknownInput:
            missing.append(name)
    settlements = 0
    for oid, bucket, paid in settlements_remaining:
        try:
            p = paid.require(oid)
            if not 0 <= p <= bucket:
                raise ValueError(f"{oid}: paid amount must be within its bucket")
            settlements += bucket - p
        except UnknownInput:
            missing.append(f"{oid}_paid_since_measurement")
    if missing:
        return unknown(Unit.CENTS, "Required inputs unknown: " + ", ".join(missing))
    residual = (vals["opening_cash"] - vals["unavailable_opening_cash"] + vals["customer_collections"]
                + vals["committed_financing"] + vals["other_confirmed_inflows"] - settlements
                - vals["operating_outflows"] - vals["other_debt_service"] - vals["reserve"])
    return derived(residual, Unit.CENTS, "horizon residual identity (kit reference calculator)")
