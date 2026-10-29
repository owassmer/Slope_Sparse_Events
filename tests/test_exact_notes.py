"""One-draw notes fidelity at method boundaries in real, complete histories."""
from collections import Counter

import numpy as np
import pytest

from app.analysis import events
from app.exact import export_equity as ex
from app.exact import export_notes as x
from app.exact import fields as f
from app.exact import fields_notes as n
from app.exact import notes as q
from app.exact import oracle

DRAWS = (0, 145, 365)
DAYS = (-1, 0, 20, 60, 90, 114, 150, 160, 179, f.BIG)
CTX = {'I1': n.CTX_I1, 'post': n.CTX_POST, 'ruling': n.CTX_RULING}
BRANCH = {'no': n.NO, 'yes': n.YES, 'holders_file': n.HOLDERS_FILE, 'accelerated': n.ACCELERATED}
DELIST = {'none': 0, 'accelerated': 1, 'repurchase_unpaid': 2, 'petition_delist': 3,
          'petition_delist_holders': 4, 'petition_repurchase': 5, 'petition_repurchase_holders': 6}


def ids(ch):
    return {s: i for i, s in enumerate(sorted(set(ch._n1) | {o['occasion'] for o in ch._offers}))}


def eq_args(ch, state):
    _, _, P, takes, offers, atm, _, parts, _ = state
    schedule = ex.atm_schedule(ch)
    return P, takes, parts, offers, atm, schedule, ex.config(ch), np.zeros(len(schedule), dtype=np.int64)


def compare(state, after, where):
    S, D, _, _, offers, atm, _, _, coupons = state
    ws, wd, _, _, wo, wa, _, _, wc = after
    a, b = S.copy(), ws.copy()
    # Stays' whole-state derived fields are the stays port's, not this area's.
    for ix in (f.STAYED_FROM, f.LOCK_AMOUNT, f.LOCK_DAY):
        a[ix] = b[ix] = 0
    np.testing.assert_array_equal(a, b, err_msg=where)
    for got, want in ((D, wd), (offers, wo), (atm, wa), (coupons, wc)):
        np.testing.assert_array_equal(got, want, err_msg=where)


@pytest.mark.parametrize('style', ['uniform', 'long'])
@pytest.mark.parametrize('seed', [11, 23, 47])
def test_real_histories(monkeypatch, style, seed):
    histories = oracle.histories(3, DRAWS, seed=seed, style=style)
    counts = Counter()
    names = ('instrument_cash', 'coupon_when_due', 'book_default', 'listing_step', 'decide_distress', 'mark', 'step')
    original = {name: getattr(events.Chain, name) for name in names}
    original_nonpayment = events.Chain.nonpayment_day
    cash_reads = []

    def read_nonpayment(ch):
        out = original_nonpayment(ch)
        cash_reads.append(out.copy())
        return out

    monkeypatch.setattr(events.Chain, 'nonpayment_day', read_nonpayment)

    def booking(name):
        def wrapped(ch, *args, **kw):
            selected = (name not in ('mark', 'decide_distress', 'step')
                        or name == 'mark' and args[0] == 'notes_due'
                        or name == 'decide_distress' and args[0] == 'nonpayment'
                        or name == 'step' and args[0] in ('notes_due_date', 'delisting_notes'))
            if not selected:
                return original[name](ch, *args, **kw)
            occasion = ids(ch)
            before = [x.notes_state(ch, r, occasion) for r in range(ch.n)]
            cfg = x.config(ch)
            how = n.HOW.index(ch._how)
            pos = len(cash_reads)
            result = original[name](ch, *args, **kw)
            # Capture the processor's actual read on the state AFTER coupon refunds.
            npday = cash_reads[-1] if len(cash_reads) > pos else np.full(ch.n, f.BIG)
            for r, state in enumerate(before):
                S, D, _, _, _, _, _, _, coupons = state
                eq = eq_args(ch, state)
                if name == 'instrument_cash':
                    q.instrument_cash(S, D, coupons, cfg, eq, x.coupon_schedule(ch), ch.basis.legal[r])
                elif name == 'coupon_when_due':
                    q.coupon_when_due(S, D, coupons)
                elif name == 'mark':
                    where = kw.get('where', args[2] if len(args) > 2 else True)
                    if where is None:
                        where = True
                    q.mark(S, int(ch.per_draw(args[1])[r]), bool(ch.per_draw(where)[r]), how, ch.N)
                elif name == 'book_default':
                    got = q.book_default(S, D, coupons, cfg, eq, CTX[args[0]], BRANCH[args[1]],
                                         bool(args[2][r]), int(npday[r]))
                    assert got == result[r]
                elif name == 'listing_step':
                    got = q.listing_step(S, D, coupons, cfg, eq, x.listing_dates(ch), args[0] == 'listing_date',
                                         0 if args[1] == 'compliance' else 1,
                                         {'compliant': 0, 'hearing': 1, 'suspended': 2}.get(args[2], 0))
                    assert got == result[r]
                elif name == 'decide_distress':
                    got = q.decide_nonpayment(S, D, coupons, cfg, eq, int(args[3][r]),
                                             {'due': 0, 'petition': 1, 'holders_file': 2}[args[2]])
                    assert got == result[r]
                elif args[0] == 'notes_due_date':
                    got = q.notes_due_date(S, cfg, ch.N, args[1] == 'holders', int(npday[r]))
                    assert got == result[r]
                else:
                    calendar = x.repurchase_calendar(ch, [int(S[f.DELISTED])])
                    got = q.delisting_notes(S, D, coupons, cfg, eq, calendar, DELIST[args[2]])
                    assert got == result[r]
                compare(state, x.notes_state(ch, r, occasion), f'{name} {args[:3]} {style} {seed} draw {r}')
                counts['booking_' + (args[0] if name == 'step' else name)] += 1
            return result
        return wrapped

    for name in names[:-1]:
        monkeypatch.setattr(events.Chain, name, booking(name))
    booked_step = booking('step')

    def step(ch, node, ctx, branch):
        result = booked_step(ch, node, ctx, branch)
        ch._reprice()
        cfg = x.config(ch)
        states = [x.notes_state(ch, r, ids(ch)) for r in range(ch.n)]
        npday = ch.nonpayment_day()
        default = {ctx: ch.judgment_default(ctx) for ctx in CTX}
        entered, available, route = ch.judgment_amount_entered, ch.default_available_day, ch.holder_route_days_path
        schedule, dates = x.coupon_schedule(ch), x.listing_dates(ch)
        calendar = x.repurchase_calendar(ch, list(DAYS) + list(dates))
        for r, state in enumerate(states):
            S, _, P, takes, _, _, _, _, _ = state
            assert q.judgment_amount_entered(S, cfg, ch.N) == entered[r]
            assert q.default_available_day(S, takes, cfg, ch.N) == available[r]
            assert q.holder_route_days_path(S, cfg, int(npday[r])) == route[r]
            assert q.holder_route_days(cfg) == ch.holder_route_days()
            assert q.nonpayment_day(S, int(npday[r])) == ch.distress_day('nonpayment', '')[r]
            counts['accessors'] += 5
            for ctx, code in CTX.items():
                ripe, on = q.judgment_default(S, takes, cfg, code)
                assert (ripe, on) == (default[ctx][0][r], default[ctx][1][r])
                counts['accessors'] += 1
            for j, when in enumerate(ch.fin.interest_dates):
                assert q.coupon_cash_cents(P, schedule[j], cfg) == ch.per_draw(ch.coupon_cash_cents(when))[r]
                counts['accessors'] += 1
            for key, i in zip(n.LISTING_KEYS, range(len(dates)), strict=True):
                assert q.listing_dates(dates, i) == ch.listing_dates()[key]
                counts['accessors'] += 1
        for day in DAYS:
            amount, standing = ch.standing_amount(day), ch.judgment_standing(day)
            for r, state in enumerate(states):
                S, _, _, takes, _, _, _, _, _ = state
                assert q.standing_amount(S, cfg, day) == amount[r]
                assert n.STANDING[q.judgment_standing(S, takes, cfg, day)] == standing[r]
                assert q.repurchase_day(calendar, day) == ch.repurchase_day(day)
                counts['accessors'] += 3
                counts['draw_days'] += 1
        counts['steps'] += 1
        counts['step_draws'] += ch.n
        return result

    monkeypatch.setattr(events.Chain, 'step', step)
    for h in histories:
        fc, d = oracle.context(h.draws)
        events.event_trace(d, h.path, fc.setup, fc.m, fc.draws, fc.sens)
        counts['histories'] += 1
    assert counts['accessors'] and counts['booking_instrument_cash']
    print(style, seed, dict(counts))


def test_booking_branches_and_boundaries():
    """Exact limits not guaranteed by random selection: every holders' branch,
    same-day coupon, petition ties, and last/outside-horizon automatic default."""
    fc, d = oracle.context(DRAWS)
    base = events.Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
    base.instrument_cash()
    base.step('verdict', '', 'claimant_theory')
    base.step('post_trial_motions', '', 'yes')
    base._atm_rebook()  # Chain.advance does this after every step.
    counts = Counter()

    def check(ch, action, python, compiled):
        states = [x.notes_state(ch, r, ids(ch)) for r in range(ch.n)]
        cfg = x.config(ch)
        result = python()
        for r, state in enumerate(states):
            got = compiled(state, cfg, eq_args(ch, state), r)
            if result is not None:
                assert got == result[r]
            compare(state, x.notes_state(ch, r, ids(ch)), action)
            counts[action] += 1

    for ctx in CTX:
        for branch in BRANCH:
            ch = base.clone()
            if ctx == 'ruling':
                # Exact changed-judgment boundary: a ruling after the original clock.
                ch.F = np.maximum(ch.F, ch.e_ix + ch.fin.judgment_default_days)
                ch.cls_amount = ch.entered - 1
                ch._atm_rebook()
            # The cash reader is called after prepare_default's coupon refund.
            probe = ch.clone()
            probe.book_default(ctx, 'accelerated' if branch != 'no' else 'no', np.ones(ch.n, dtype=bool))
            npday = probe.nonpayment_day()
            check(ch, 'default_' + ctx + '_' + branch,
                  lambda ch=ch, ctx=ctx, branch=branch: ch.book_default(ctx, branch, np.ones(ch.n, dtype=bool)),
                  lambda st, c, eq, r, ctx=ctx, branch=branch, npday=npday: q.book_default(
                      st[0], st[1], st[-1], c, eq, CTX[ctx], BRANCH[branch],
                                                     True, int(npday[r])))
    for branch in ('due', 'petition', 'holders_file'):
        for day in (0, base.coupons[0][0], base.N - 1, base.N, f.BIG):
            ch = base.clone()
            check(ch, 'nonpayment_' + branch,
                  lambda ch=ch, branch=branch, day=day: ch.decide_distress('nonpayment', '', branch, np.full(ch.n, day)),
                  lambda st, c, eq, r, day=day, branch=branch: q.decide_nonpayment(st[0], st[1], st[-1], c, eq, day,
                                                         {'due': 0, 'petition': 1, 'holders_file': 2}[branch]))
    for branch in ('compliant', 'hearing', 'suspended'):
        ch = base.clone()
        check(ch, 'listing_' + branch, lambda ch=ch, branch=branch: ch.listing_step('listing', '', branch),
              lambda st, c, eq, r, ch=ch, branch=branch: q.listing_step(st[0], st[1], st[-1], c, eq, x.listing_dates(ch), False, 0,
                                                {'compliant': 0, 'hearing': 1, 'suspended': 2}[branch]))
    for branch in DELIST:
        ch = base.clone()
        ch.listing_step('listing', '', 'suspended')
        calendar = x.repurchase_calendar(ch, ch.delisted)
        check(ch, 'delisting_' + branch, lambda ch=ch, branch=branch: ch.step('delisting_notes', '', branch),
              lambda st, c, eq, r, calendar=calendar, branch=branch: q.delisting_notes(
                  st[0], st[1], st[-1], c, eq, calendar, DELIST[branch]))
    for holders in (False, True):
        ch = base.clone()
        ch.book_default('I1', 'accelerated', np.ones(ch.n, dtype=bool))
        npday = ch.nonpayment_day()
        check(ch, 'notes_due_date', lambda ch=ch, holders=holders: ch.step('notes_due_date', 'holders' if holders else 'issuer', ''),
              lambda st, c, eq, r, ch=ch, holders=holders, npday=npday: q.notes_due_date(
                  st[0], c, ch.N, holders, int(npday[r])))
    print('boundary bookings', dict(counts))


@pytest.mark.parametrize('mode', ['all_cash', 'all_shares', 'shares_to_capacity'])
def test_coupon_modes_and_default_limits(mode):
    fc, d = oracle.context((0,))
    sens = dict(fc.sens, coupon_cash_share=mode)
    ch = events.Chain(d, fc.setup, fc.m, fc.draws, sens)
    state = x.notes_state(ch, 0, {})
    cfg = x.config(ch)
    q.instrument_cash(state[0], state[1], state[-1], cfg, eq_args(ch, state),
                      x.coupon_schedule(ch), ch.basis.legal[0])
    ch.instrument_cash()
    compare(state, x.notes_state(ch, 0, {}), 'coupon mode ' + mode)
    ch.step('verdict', '', 'claimant_theory')
    ch.step('post_trial_motions', '', 'yes')
    for reading in ('entered', 'post_ruling', 'both'):
        ch.sens['judgment_default_reading'] = reading
        for delta in (-1, 0, 1):
            ch.entered = ch.fin.judgment_default_threshold_cents + ch.fin.insured_cents + delta
            for stay in (f.BIG, int(ch.e_ix[0] + ch.fin.judgment_default_days)):
                ch.stayed_from = np.array([stay])
                S, _, _, takes, *_ = x.notes_state(ch, 0, {})
                cfg = x.config(ch)
                for ctx, code in CTX.items():
                    ripe, on = ch.judgment_default(ctx)
                    assert q.judgment_default(S, takes, cfg, code) == (ripe[0], on[0])
                assert q.default_available_day(S, takes, cfg, ch.N) == ch.default_available_day[0]
    # A continuing general-nonpayment default opens the holders' route on equality.
    S[f.MARK_NOTES_DUE] = 50
    assert q.holder_route_days_path(S, cfg, 50) == 0
    assert q.holder_route_days_path(S, cfg, 51) == cfg.route


def test_legacy_listing_gates_and_calendar_refusals():
    fc, d = oracle.context((0,))
    base = events.Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
    base.equity = False  # exercise the explicitly retained non-equity listing branch
    dates = x.listing_dates(base)
    for due in (int(dates[n.VOTE_CALL]), f.BIG):
        for ctx in ('kept', *n.LISTING_KEYS):
            for branch in ('compliant', 'delisted_suspension', 'delisted_panel'):
                for date_only in (True, False):
                    ch = base.clone()
                    ch.marks['notes_due'] = np.array([due])
                    state = x.notes_state(ch, 0, {})
                    got = q.listing_legacy(state[0], ch.N, dates, date_only,
                                           -1 if ctx == 'kept' else n.LISTING_KEYS.index(ctx),
                                           -1 if branch == 'compliant' else n.LISTING_KEYS.index(branch))
                    want = ch.step('listing_date' if date_only else 'listing', ctx, branch)
                    assert got == want[0]
                    compare(state, x.notes_state(ch, 0, {}), 'legacy listing gate')
    calendar = x.repurchase_calendar(base, [0])
    with pytest.raises(ValueError, match='absent'):
        q.repurchase_day(calendar, 1)
    state = x.notes_state(base, 0, {})
    dates[n.PANEL] = base.N - 1
    with pytest.raises(NotImplementedError, match='panel decides'):
        q.listing_step(state[0], state[1], state[-1], x.config(base), eq_args(base, state), dates, False, 0, 1)


def test_strict_mark_and_coupon_refund():
    fc, d = oracle.context((0,))
    ch = events.Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
    ch.instrument_cash()
    state = x.notes_state(ch, 0, {})
    S, D, _, _, _, _, _, _, coupons = state
    day = int(coupons[0, n.C_DAY])
    for when, how in ((day, n.DECLARED), (day, n.AUTOMATIC_J), (-1, n.AUTOMATIC_J),
                      (ch.N, n.AUTOMATIC_J), (day - 1, n.AUTOMATIC_J)):
        q.mark(S, when, True, how, ch.N)
        ch._how = n.HOW[how]
        ch.mark('notes_due', when)
        q.coupon_when_due(S, D, coupons)
        ch.coupon_when_due()
        compare(state, x.notes_state(ch, 0, {}), 'strict earlier mark, refund once')
    assert S[n.NOTES_DUE_HOW] == n.AUTOMATIC_J
    assert D[f.D_KIND_NOTES_INTEREST, day] == 0
