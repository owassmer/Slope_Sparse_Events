"""Prefix-measurable contractual envelopes, before averaging operating draws."""
from __future__ import annotations

import numpy as np

from app.analysis.engine import PREFERENCE_DAYS, installment_amounts
from app.analysis.stress_search import exposure_bound


def continuation_bounds(line, t, ev, day):
    """Return ``{figure: (lower, upper)}``, arrays per draw; unknown upper is None.

    These are safe envelopes, not claims that either endpoint is attainable. They
    depend only on the line, trajectory records dated strictly before ``day`` and
    event petitions strictly before ``day``. Any histories with the same prefix
    therefore return identical bounds, even if their later trajectories differ.
    ``day`` is a per-draw integer in [0, horizon], or a broadcast scalar. Event
    cash here means all engine inputs (including petition, lock, capacity, and
    daily processing's classified obligations), not just the net cash array.

    engine._net_kernel and processor._daily_kernel collect only amounts due,
    never reverse collections, fund only routed invoices passing the limit test,
    and stop both activities at a petition. Reconstructing the opening schedule
    and the *individual* prefix draw records preserves installment fee rounding.
    Every remaining invoice individually exceeding its day's limit is impossible;
    funding all the others is a ceiling, not an assumed feasible joint history.
    Where contract >= principal, outstanding >= funded - collected, so the
    funding test also caps cumulative principal funded by limit + collections
    at the last funding date (opening principal is included in this test).
    Thus funded is prefix funding plus at most those invoices; due is the known
    schedule plus at most their in-horizon installments; collected is between
    prefix collections and maximum due. Unpaid is due minus collections, capped
    also by stress_search.exposure_bound's rounding-aware owed ceiling. Past due
    and frozen due partition unpaid (core.Reduction.add). Not yet due is the
    out-of-horizon schedule, or zero on petition (engine._finish). Stayed is owed
    at the single petition, at most the same exposure ceiling. A prefix petition
    fixes all these quantities, including its 90-day preference window; otherwise
    clawback cannot exceed prefix collections still within a possible petition's
    window plus all possible remaining collections. Petition probability is an
    indicator per draw, not a forecast probability.

    Outstanding is engine's ceil(total funded * remaining owed / contractual).
    It cannot exceed total funded. Where opening owed >= opening principal and
    fees are nonnegative, it also cannot exceed remaining owed. The prefix daily
    maximum/sum plus these future daily ceilings bound core._scalars' peak/mean;
    nonnegative future outstanding gives their floors. A prefix petition fixes
    outstanding for the rest of the horizon.

    Equity ceilings are deliberately None: events.Chain._atm_schedule/_atm_rebook
    require model sale pace, price, commission, share ledger and shelf capacity;
    offering_terms/ offering_bound_reached require net issue size and recurrence
    limits. None of those are carried by Line. EventCash.proceeds contains only
    horizon totals, not dated channel receipts: reading it even for a completed
    prefix could distinguish equal cash histories by an unobserved channel split.
    Neither observed totals nor net cash (which offsets payments) bounds these
    channels. Their only available universal lower bound is zero. Supplying the
    model and dated channel ledger would be necessary for finite equity ranges.
    """
    n, horizon = t.collections.shape
    days = np.broadcast_to(np.asarray(day), (n,))
    if not np.issubdtype(days.dtype, np.integer) or np.any((days < 0) | (days > horizon)):
        raise ValueError("day must be an integer in [0, horizon] per draw")
    if horizon != line.days or n != line.ops.draws:
        raise ValueError("line and trajectories must cover the same draws and horizon")
    names = ('funded', 'due', 'collected', 'unpaid', 'past_due', 'frozen_due',
             'not_yet_due', 'petition_p', 'clawback', 'stayed', 'peak_outstanding', 'avg_outstanding')
    out = {k: (np.zeros(n), np.zeros(n)) for k in names}
    ex = line.setup.exposure
    ceiling = exposure_bound(line)
    for r, start in enumerate(days):
        start = int(start)
        schedule = np.zeros(line.tail, dtype=np.int64)
        schedule[0] = ex.past_due_cents
        for date, amount in ex.installments:
            schedule[(date - line.setup.review).days - 1] += amount
        known = (t.draw_rows == r) & (t.draw_days < start)
        for date, amount in zip(t.draw_days[known], t.draw_amounts[known], strict=True):
            np.add.at(schedule, line.due_idx[date],
                      installment_amounts([amount], line.setup.fee_bps, line.setup.installments)[0])
        funded = int(t.fundings[r, :start].sum())
        collected = int(t.collections[r, :start].sum())
        due = int(schedule[:horizon].sum())
        later = int(schedule[horizon:].sum())
        owed = due + later - collected
        petition = int(ev.petition[r])
        frozen = 0 <= petition < start
        possible = schedule.copy()
        future_funding = np.zeros(horizon, dtype=np.int64)
        if not frozen:
            for date in range(start, horizon):
                for amount in line.routes[r, date]:
                    if 0 < amount <= line.limit[r, date]:
                        future_funding[date] += amount
                        np.add.at(possible, line.due_idx[date],
                                  installment_amounts([amount], line.setup.fee_bps,
                                                      line.setup.installments)[0])
        max_due = int(possible[:horizon].sum())
        max_later = int(possible[horizon:].sum())
        remaining = min(int(ceiling[r]), max_due + max_later - collected)

        def put(key, low, high, r=r):
            out[key][0][r], out[key][1][r] = low, high

        max_funded = funded + int(future_funding.sum())
        contract_covers_principal = ex.owed_cents >= ex.principal_cents and line.setup.fee_bps >= 0
        if contract_covers_principal and start < horizon and not frozen:
            max_funded = min(max_funded, max(funded, int(line.limit[r, start:].max())
                                           + max_due - ex.principal_cents))
        put('funded', funded, max_funded)
        put('due', due, max_due)
        put('collected', collected, collected if frozen else max_due)
        put('unpaid', due - collected if frozen else 0, min(max_due - collected, remaining))
        put('past_due', 0, 0 if frozen else min(max_due - collected, remaining))
        put('frozen_due', due - collected if frozen else 0, min(max_due - collected, remaining))
        put('not_yet_due', 0, 0 if frozen else min(max_later, remaining))
        put('petition_p', int(frozen), int(frozen or start < horizon))
        put('stayed', owed if frozen else 0, owed if frozen else remaining)
        if frozen:
            preference = t.collections[r, max(0, petition - PREFERENCE_DAYS):petition].sum()
            put('clawback', preference, preference)
        else:
            recent = t.collections[r, max(0, start - PREFERENCE_DAYS):start].sum()
            put('clawback', 0, recent + max_due - collected if start < horizon else 0)
        prefix = t.outstanding[r, :start]
        low_peak = int(prefix.max()) if start else 0
        low_sum = int(prefix.sum())
        future = ex.principal_cents + funded + np.cumsum(future_funding)[start:]
        future = np.minimum(future, ex.principal_cents + max_funded)
        if contract_covers_principal:
            future = np.minimum(future, remaining)
        if frozen:
            future[:] = prefix[-1]
            low_sum += int(future.sum())
            low_peak = max(low_peak, int(future.max()) if future.size else 0)
        put('peak_outstanding', low_peak, max(low_peak, int(future.max()) if future.size else 0))
        put('avg_outstanding', low_sum / horizon,
            (int(prefix.sum()) + int(future.sum())) / horizon)
        if start == horizon:
            # No future petition or collection; the entire known contract is fixed.
            put('collected', collected, collected)
            put('unpaid', due - collected, due - collected)
            put('past_due', 0 if frozen else due - collected, 0 if frozen else due - collected)
            put('frozen_due', due - collected if frozen else 0, due - collected if frozen else 0)
            put('not_yet_due', 0 if frozen else later, 0 if frozen else later)
            put('stayed', owed if frozen else 0, owed if frozen else 0)
    out.update({k: (np.zeros(n), None) for k in ('atm_proceeds', 'offering_proceeds')})
    return out
