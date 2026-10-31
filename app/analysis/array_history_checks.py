"""Independent order replay with one engine chain for a history's native draws.

Question-rule cursors remain lane-local: absence, petition and quiet groups can
resolve differently. Only the engine work is shared, never a lane's verdict.
"""
from copy import deepcopy

import numpy as np

from app.analysis.events import GROUPED, Chain, Trace, canon, plain
from app.analysis.frontier import Decision
from app.analysis.walk_checks import PROBE, State


def order_history(fc, d, p, replay, cache=None):
    n, N = fc.draws.n, fc.days
    tr = replay(p.steps, light=True)
    days = [np.broadcast_to(day, (n,)) for day in tr.day]
    bounds = np.where(tr.events.petition < 0, N, np.minimum(N, tr.events.petition))
    ch = None
    states = [State(a4='', pending=[Decision('settle', 'I0')]) for _ in range(n)]
    bad = [[] for _ in range(n)]
    last = np.full(n, -1)
    listing = any(f.status != 'superseded' and f.listing_deadline is not None for f in d.financing)
    probes = {}

    def due(steps, decision):
        probe = canon(steps) + ((decision.node, decision.ctx, PROBE[decision.node]),)
        if probe not in probes:
            t = replay(probe, day_only=True, light=True)
            day = np.broadcast_to(t.day[-1], (n,))
            pet = t.events.petition
            ok = (day < N) & ((pet < 0) | (day < pet))
            if decision.node == 'settle':
                ok &= t.settle_offer > 0
            if decision.node in GROUPED and len(probe) - 1 in t.groups:
                ok &= t.groups[len(probe) - 1] >= 0
            probes[probe] = day, ok
        return probes[probe]

    def state_key(i):
        return (p.steps[:i], tuple(day.tobytes() for day in days[:i]),
                tuple(None if j not in tr.groups else tr.groups[j].tobytes() for j in range(i)),
                bounds.tobytes())

    start = 0
    if cache is not None:
        for i in range(len(p.steps), -1, -1):
            key = state_key(i)
            if key in cache:
                saved_ch, states, bad, last = cache[key]
                ch, states, bad, last = saved_ch.clone(), deepcopy(states), deepcopy(bad), last.copy()
                trace = Trace(ch.ev)
                cache.move_to_end(key)
                start = i
                break
    if ch is None:
        ch = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
        ch.instrument_cash()
        if cache is not None:
            cache[state_key(0)] = ch.clone(), deepcopy(states), deepcopy(bad), last.copy()
    trace = Trace(ch.ev)
    for i in range(start, len(p.steps) + 1):
        if cache is not None and i > start:
            cache[state_key(i)] = ch.clone(), deepcopy(states), deepcopy(bad), last.copy()
            if len(cache) > 64:
                cache.popitem(last=False)
        terminal = i == len(p.steps)
        if not terminal:
            node, ctx, b = p.steps[i]
            if node == 'offering':
                ch.advance(trace, *p.steps[i])
                continue
            dates = days[i]
            me = Decision('listing_date', 'compliance') if node == 'listing' else Decision(node, ctx)
            quiet = b.startswith('@-1=')
        else:
            dates, me, quiet = bounds, None, False
        active = np.ones(n, dtype=bool)
        for row, st in enumerate(states):
            at, bound = int(dates[row]), int(bounds[row])
            if not terminal and (quiet or at >= bound):
                group = tr.groups.get(i)
                asked = at < bound and (group is None or int(group[row]) >= 0)
                if asked:
                    bad[row].append(dict(kind='unasked_step', step=list(p.steps[i]), day=at))
                if quiet and me in st.pending:
                    st.pending.remove(me)
                elif not quiet:
                    st.answer(node, ctx, plain(b))
                active[row] = False
            elif not terminal and at < last[row]:
                bad[row].append(dict(kind='out_of_order', step=list(p.steps[i]), day=at, after=int(last[row])))
        # All bookings before each draw's own boundary, not before another
        # draw's date. next_decisions only reads this private engine view.
        view = ch.clone()
        view.until(np.asarray(dates, dtype=np.int64))
        frontiers = {}
        for row in np.flatnonzero(active):
            st = states[row]
            at, bound = int(dates[row]), int(bounds[row])
            later = set()
            mine = None
            for _ in range(32):
                cursors = st.cursors(listing)
                if cursors not in frontiers:
                    frontiers[cursors] = view.next_decisions(cursors)
                f = frontiers[cursors]
                mine = next((c for c in f.candidates if c.decision == me), None)
                rank = (at, 9, 1 << 30) if mine is None else (at, mine.phase, mine.order)
                early = [c for c in f.candidates if c.chain != 'deterministic' and c.decision != me
                         and c.decision not in later
                         and (int(c.day[row]), c.phase, c.order) < rank and int(c.day[row]) < bound]
                if not early:
                    break
                c = min(early, key=lambda c: (int(c.day[row]), c.phase, c.order))
                probe_days, probe_ok = due(p.steps[:i], c.decision)
                day, ok = int(probe_days[row]), bool(probe_ok[row])
                if ok and (day < at or (day == at and me is not None)):
                    bad[row].append(dict(kind='missing' if me is None else 'skipped',
                                         decision=[c.decision.node, c.decision.ctx], day=day,
                                         before=None if me is None else list(p.steps[i]),
                                         at=None if me is None else at))
                elif ok:
                    later.add(c.decision)
                    continue
                st.absent(c.decision)
            if not terminal:
                if mine is None and me.node not in ('cash_floor', 'cash_out', 'nonpayment', 'listing', 'delisting_notes'):
                    bad[row].append(dict(kind='not_open', step=list(p.steps[i]), day=at,
                                         open=[[x.node, x.ctx] for x in st.pending]))
                st.answer(node, ctx, plain(b))
                last[row] = at
        if not terminal:
            ch.advance(trace, *p.steps[i])
    return bad
