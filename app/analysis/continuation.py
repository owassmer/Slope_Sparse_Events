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
    start = days.astype(np.int64)
    rows = np.arange(n)
    ex = line.setup.exposure
    pre = _line_tables(line, horizon)
    ceiling = pre["ceiling"]

    def at(cum, d):  # sum over days before d, from a cumulative sum with a leading zero column
        return cum[rows, d]

    zero = np.zeros((n, 1), dtype=np.int64)
    c_fund = np.concatenate([zero, np.cumsum(t.fundings[:, :horizon], axis=1, dtype=np.int64)], axis=1)
    c_coll = np.concatenate([zero, np.cumsum(t.collections[:, :horizon], axis=1, dtype=np.int64)], axis=1)
    c_out = np.concatenate([zero, np.cumsum(t.outstanding[:, :horizon], axis=1, dtype=np.int64)], axis=1)
    funded, collected = at(c_fund, start), at(c_coll, start)

    # the known schedule: the opening exposure's, and every draw funded before `day`, with its own fee rounding
    due, later = np.full(n, pre["open_in"], dtype=np.int64), np.full(n, pre["open_later"], dtype=np.int64)
    if len(t.draw_amounts):
        inst = installment_amounts(t.draw_amounts, line.setup.fee_bps, line.setup.installments)
        inside = line.due_idx[t.draw_days] < horizon
        rec_in = (inst * inside).sum(axis=1)
        known = t.draw_days < start[t.draw_rows]
        np.add.at(due, t.draw_rows[known], rec_in[known])
        np.add.at(later, t.draw_rows[known], inst.sum(axis=1)[known] - rec_in[known])
    owed = due + later - collected
    petition = np.asarray(ev.petition, dtype=np.int64)
    frozen = (petition >= 0) & (petition < start)
    live = ~frozen

    # every routed invoice from `day` on that does not alone exceed its day's limit (line tables, suffix sums)
    max_due = due + np.where(live, pre["s_due_in"][rows, start], 0)
    max_later = later + np.where(live, pre["s_due_later"][rows, start], 0)
    remaining = np.minimum(ceiling, max_due + max_later - collected)
    max_funded = funded + np.where(live, pre["s_fund"][rows, start], 0)
    covers = ex.owed_cents >= ex.principal_cents and line.setup.fee_bps >= 0
    if covers:
        cap = np.maximum(funded, pre["s_limit_max"][rows, np.minimum(start, horizon - 1)] + max_due
                         - ex.principal_cents)
        max_funded = np.where((start < horizon) & live, np.minimum(max_funded, cap), max_funded)
    open_ = start < horizon
    out = {}
    out["funded"] = (funded, max_funded)
    out["due"] = (due, max_due)
    out["collected"] = (collected, np.where(frozen, collected, max_due))
    unpaid_hi = np.minimum(max_due - collected, remaining)
    out["unpaid"] = (np.where(frozen, due - collected, 0), unpaid_hi)
    out["past_due"] = (np.zeros(n), np.where(frozen, 0, unpaid_hi))
    out["frozen_due"] = (np.where(frozen, due - collected, 0), unpaid_hi)
    out["not_yet_due"] = (np.zeros(n), np.where(frozen, 0, np.minimum(max_later, remaining)))
    out["petition_p"] = (frozen.astype(np.float64), (frozen | open_).astype(np.float64))
    out["stayed"] = (np.where(frozen, owed, 0), np.where(frozen, owed, remaining))
    pet = np.clip(petition, 0, horizon)
    preference = at(c_coll, pet) - at(c_coll, np.maximum(0, pet - PREFERENCE_DAYS))
    recent = at(c_coll, start) - at(c_coll, np.maximum(0, start - PREFERENCE_DAYS))
    out["clawback"] = (np.where(frozen, preference, 0),
                       np.where(frozen, preference, np.where(open_, recent + max_due - collected, 0)))

    # outstanding: the prefix's own days, then each later day's ceiling (or the frozen balance after a petition)
    dmax = np.maximum.accumulate(t.outstanding[:, :horizon], axis=1)
    low_peak = np.where(start > 0, dmax[rows, np.maximum(start - 1, 0)], 0)
    prefix_sum = at(c_out, start)
    last = t.outstanding[rows, np.maximum(start - 1, 0)]
    col = np.arange(horizon)[None, :]
    after = col >= start[:, None]
    c_f = pre["c_fund_incl"]  # inclusive cumulative feasible funding by day, [n, horizon]
    before_start = np.where(start > 0, c_f[rows, np.maximum(start - 1, 0)], 0)
    grow = np.where(live[:, None], c_f - before_start[:, None], 0)
    future = ex.principal_cents + funded[:, None] + grow
    future = np.minimum(future, (ex.principal_cents + max_funded)[:, None])
    if covers:
        future = np.minimum(future, remaining[:, None])
    future = np.where(frozen[:, None], last[:, None], future)
    future = np.where(after, future, 0)
    f_sum = future.sum(axis=1)
    f_max = np.where(after.any(axis=1), np.where(after, future, np.iinfo(np.int64).min).max(axis=1), 0)
    f_max = np.where(after.any(axis=1), f_max, 0)
    low_sum = prefix_sum + np.where(frozen, f_sum, 0)
    low_peak = np.where(frozen & after.any(axis=1), np.maximum(low_peak, f_max), low_peak)
    out["peak_outstanding"] = (low_peak, np.maximum(low_peak, f_max))
    out["avg_outstanding"] = (low_sum / horizon, (prefix_sum + f_sum) / horizon)

    # `day` at the horizon: no future petition or collection; the whole known contract is fixed
    done = start == horizon
    if done.any():
        def fix(key, value):
            lo, hi = out[key]
            out[key] = (np.where(done, value, lo), np.where(done, value, hi))
        fix("collected", collected)
        fix("unpaid", due - collected)
        fix("past_due", np.where(frozen, 0, due - collected))
        fix("frozen_due", np.where(frozen, due - collected, 0))
        fix("not_yet_due", np.where(frozen, 0, later))
        fix("stayed", np.where(frozen, owed, 0))
    out = {k: (np.asarray(lo, dtype=np.float64), np.asarray(hi, dtype=np.float64)) for k, (lo, hi) in out.items()}
    out.update({k: (np.zeros(n), None) for k in ('atm_proceeds', 'offering_proceeds')})
    return out


def _line_tables(line, horizon):
    """What the bounds read from the line alone, computed once per line and horizon: per draw and day, the routed
    invoices that do not alone exceed that day's limit (their principal and their installments inside and after the
    horizon), as sums over every day from d on; the limit's largest value from d on; the opening schedule's split;
    and `exposure_bound`. The per-history bounds then read these at the history's `day` instead of walking the
    invoices again."""
    cache = line.__dict__.setdefault("_continuation_tables", {})
    if horizon in cache:
        return cache[horizon]
    ex, setup = line.setup.exposure, line.setup
    schedule = np.zeros(line.tail, dtype=np.int64)
    schedule[0] = ex.past_due_cents
    for date, amount in ex.installments:
        schedule[(date - setup.review).days - 1] += amount
    routes = np.asarray(line.routes[:, :horizon], dtype=np.int64)          # [draws, days, slots]
    limit = np.asarray(line.limit[:, :horizon], dtype=np.int64)
    feasible = (routes > 0) & (routes <= limit[:, :, None])
    amounts = np.where(feasible, routes, 0)
    flat = amounts.reshape(-1)
    inst = installment_amounts(flat, setup.fee_bps, setup.installments).reshape(*amounts.shape, -1)
    inst = np.where(feasible[..., None], inst, 0)                           # an empty slot books nothing
    inside = (line.due_idx[:horizon] < horizon)[None, :, None, :]           # [1, days, 1, installments]
    due_in = (inst * inside).sum(axis=(2, 3))
    due_later = inst.sum(axis=(2, 3)) - due_in
    fund = amounts.sum(axis=2)

    def suffix(x):  # [draws, days + 1]: the sum over days d.. horizon - 1 (0 at the horizon)
        return np.concatenate([np.cumsum(x[:, ::-1], axis=1)[:, ::-1], np.zeros((len(x), 1), dtype=x.dtype)], axis=1)

    full_limit = np.asarray(line.limit, dtype=np.int64)
    tables = {"open_in": int(schedule[:horizon].sum()), "open_later": int(schedule[horizon:].sum()),
              "s_due_in": suffix(due_in), "s_due_later": suffix(due_later), "s_fund": suffix(fund),
              "c_fund_incl": np.cumsum(fund, axis=1),
              "s_limit_max": np.maximum.accumulate(full_limit[:, ::-1], axis=1)[:, ::-1],
              "ceiling": exposure_bound(line)}
    cache[horizon] = tables
    return tables
