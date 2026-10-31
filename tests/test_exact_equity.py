"""Real-history fidelity at every equity booking and after every Chain.step.

Method hooks isolate the equity portion of verdict/ruling/levy/pay/petition steps:
export immediately before _atm_rebook, run compiled on that state, compare after.
Thus no Python-computed equity result is fed to its compiled replacement.
"""
import inspect
from collections import Counter

import numpy as np
import pytest

from app.analysis import events
from app.exact import equity as q
from app.exact import export_equity as x
from app.exact import fields as f
from app.exact import fields_equity as e
from app.exact import oracle

DRAWS = (0, 145, 365)
DAYS = (-1, 0, 20, 60, 114, 150, 179, f.BIG)
TERM_KEYS = ('gross', 'costs', 'net', 'price_cents_x1e4', 'shares', 'close_days', 'shelf_capacity')


def _occasions(ch, extra=()):
    return {s: i for i, s in enumerate(sorted(set(ch._n1) | {o['occasion'] for o in ch._offers} | set(extra)))}


def _compare(got, want, where):
    S, D, P, _, offers, atm, answers, _, coupons = got
    ws, wd, wp, _, wo, wa, wn, _, wc = want
    np.testing.assert_array_equal(S[e.N_OFFERS:e.HAVE_ATM + 1], ws[e.N_OFFERS:e.HAVE_ATM + 1], err_msg=where)
    for ix in (f.MARK_RAISED, f.INCURRED_NOTES_INTEREST):
        assert S[ix] == ws[ix], where
    for a, b in ((D, wd), (P, wp), (offers, wo), (atm, wa), (answers, wn), (coupons, wc)):
        np.testing.assert_array_equal(a, b, err_msg=where)


@pytest.mark.parametrize('style', ['uniform', 'long'])
@pytest.mark.parametrize('seed', [11, 23, 47])
def test_real_histories(monkeypatch, style, seed):
    counts = Counter()
    # Generate before patching: the walk itself is not the target of this fidelity test.
    histories = oracle.histories(3, DRAWS, seed=seed, style=style)
    originals = {name: getattr(events.Chain, name) for name in ('_atm_rebook', 'initiate', 'offering_outcome', 'step')}

    def booking(name):
        def wrapped(ch, *args):
            if not ch.equity:
                return originals[name](ch, *args)
            extra = (args[1],) if name == 'initiate' else (args[0],) if name == 'offering_outcome' else ()
            ids = _occasions(ch, extra)
            before = [x.equity_state(ch, r, ids) for r in range(ch.n)]
            cfg, schedule = x.config(ch), x.atm_schedule(ch)
            result = originals[name](ch, *args)
            for r, state in enumerate(before):
                S, D, P, takes, offers, atm, answers, parts, coupons = state
                scratch = np.zeros(len(schedule), dtype=np.int64)
                if name == '_atm_rebook':
                    q.atm_rebook(S, D, P, takes, parts, offers, atm, schedule, coupons, cfg, scratch)
                elif name == 'initiate':
                    day = int(ch.per_draw(args[0])[r])
                    got = q.initiate(S, D, P, takes, parts, offers, atm, schedule, coupons, answers,
                                     cfg, scratch, day, x.year_start(ch, day), ids[args[1]])
                    assert got == result[r]
                else:
                    got = q.offering_outcome(S, D, P, takes, parts, offers, atm, schedule, coupons, answers,
                                             cfg, scratch, ids[args[0]], args[1])
                    assert got == result[r]
                _compare(state, x.equity_state(ch, r, ids), f'{name} {style} {seed} draw {r}')
                counts[name] += 1
                if name == '_atm_rebook':
                    counts['rebook_from_' + inspect.currentframe().f_back.f_code.co_name] += 1
            return result
        return wrapped

    for name in ('_atm_rebook', 'initiate', 'offering_outcome'):
        monkeypatch.setattr(events.Chain, name, booking(name))

    def step(ch, node, ctx, branch):
        result = originals['step'](ch, node, ctx, branch)
        ids = _occasions(ch)
        ch._reprice()
        cfg, schedule = x.config(ch), x.atm_schedule(ch)
        states = [x.equity_state(ch, r, ids) for r in range(ch.n)]
        grid = ch._price_owed_grid()
        for r, state in enumerate(states):
            S, _, _, takes, _, _, _, parts, _ = state
            got = np.empty(ch.N, dtype=np.int64)
            q.price_owed_grid(S, takes, parts, cfg, got)
            np.testing.assert_array_equal(got, grid[r])
            counts['price_grid_cells'] += ch.N
        cum, inflow, lock, levy = ch.balance_state()
        for day in DAYS:
            days = np.full(ch.n, day, dtype=np.int64)
            lower = x.year_start(ch, day)
            wants = {name: getattr(ch, name)(days) for name in
                     ('price_owed', 'share_price_on', 'atm_to_date', 'atm_shares_to_date', 'ledger_left',
                      'offering_price_x1e4', 'offering_pending_on', 'offering_bound_reached',
                      'offering_available', 'offer_available', 'offer_shortfall')}
            terms = ch.offering_terms(days)
            groups = [ch.option_group(n, days) for n in ('judgment_response', 'financing_at_floor')]
            for r, state in enumerate(states):
                S, D, P, takes, offers, atm, _, parts, _ = state
                q.reprice(S, P, takes, parts, cfg)
                cashargs = (cum[r], ch.basis.need[r], ch.basis.inflow[r], inflow[r], lock[r], levy[r])
                pending = f.BIG if ch.pending_levy is None else int(ch.pending_levy[r])
                common = (S, P, offers, atm, schedule, cfg, day, lower)
                reads = dict(price_owed=q.price_owed(S, takes, parts, cfg, day),
                             share_price_on=q.share_price_on(P, day),
                             atm_to_date=q.atm_to_date(S, D, day),
                             atm_shares_to_date=q.atm_shares_to_date(S, atm, schedule, day),
                             ledger_left=q.ledger_left(S, offers, atm, schedule, cfg, day),
                             offering_price_x1e4=q.offering_price_x1e4(P, cfg, day),
                             offering_pending_on=q.offering_pending_on(S, offers, day),
                             offering_bound_reached=q.offering_bound_reached(S, offers, cfg, day, ch.N),
                             offering_available=q.offering_available(*common),
                             offer_available=q.offer_available(S, P, takes, offers, atm, schedule, cfg, day, lower,
                                                               pending, *cashargs),
                             offer_shortfall=q.offer_shortfall(S, takes, cfg, day, pending, *cashargs))
                for name, got in reads.items():
                    assert got == wants[name][r], (name, node, branch, day, r, got, wants[name][r])
                    counts['accessors'] += 1
                for key, got in zip(TERM_KEYS, q.offering_terms(*common), strict=True):
                    assert got == terms[key][r], (key, node, day, r)
                    counts['term_fields'] += 1
                for i, response in enumerate((True, False)):
                    got = q.option_group(S, P, takes, offers, atm, schedule, cfg, response, day, lower, pending, *cashargs)
                    assert got == groups[i][r], (node, day, r)
                    counts['groups'] += 1
                for name, oid in ids.items():
                    assert q.offering_day(S, offers, oid) == ch.offering_day(name)[r]
                counts['draw_days'] += 1
        counts['steps'] += 1
        counts['step_draws'] += ch.n
        return result

    monkeypatch.setattr(events.Chain, 'step', step)
    for h in histories:
        fc, d = oracle.context(h.draws)
        events.event_trace(d, h.path, fc.setup, fc.m, fc.draws, fc.sens)
        counts['histories'] += 1
    assert counts['accessors'] > 0
    print(style, seed, dict(counts))


def test_outcome_before_initiation_and_two_failed_initiations():
    fc, d = oracle.context((0,))
    ch = events.Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
    ch.instrument_cash()
    ids = {'first': 0, 'second': 1, 'third': 2}
    cfg, schedule = x.config(ch), x.atm_schedule(ch)
    state = x.equity_state(ch, 0, ids)
    S, D, P, takes, offers, atm, answers, parts, coupons = state
    scratch = np.zeros(len(schedule), dtype=np.int64)

    for occasion, day in (('first', 0), ('second', cfg.close_days), ('third', 2 * cfg.close_days)):
        got = q.offering_outcome(S, D, P, takes, parts, offers, atm, schedule, coupons, answers,
                                 cfg, scratch, ids[occasion], False)
        assert got == ch.offering_outcome(occasion, False)[0] == f.BIG
        _compare(state, x.equity_state(ch, 0, ids), 'outcome before initiation')
        got = q.initiate(S, D, P, takes, parts, offers, atm, schedule, coupons, answers, cfg, scratch,
                         day, x.year_start(ch, day), ids[occasion])
        assert got == ch.initiate(np.array([day]), occasion)[0] == (occasion != 'third')
        _compare(state, x.equity_state(ch, 0, ids), 'failed initiation count')
    assert S[e.N_OFFERS] == 2
    assert q.offering_bound_reached(S, offers, cfg, cfg.close_days - 1, ch.N) is False
    assert q.offering_bound_reached(S, offers, cfg, cfg.close_days, ch.N) is True


def test_baby_shelf_threshold_and_last_whole_share():
    fc, d = oracle.context((0,))
    ch = events.Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
    cfg = x.config(ch)._replace(nonaffiliate=100_000_000, update=0)
    S, D, P, takes, offers, atm, answers, parts, coupons = x.equity_state(ch, 0, {})
    P[e.P_PRICE] = 75.0
    assert not q.limited(P, cfg, 0)  # exactly $75 million: I.B.1
    P[e.P_PRICE] = np.nextafter(75.0, 0.0)
    assert q.limited(P, cfg, 0)
    assert not q.limited(P, cfg, -1)

    # Annual capacity = floor(7 * 44) // 3 = 102 cents: two $0.44 shares,
    # then no whole share fits. Commission reduces receipts, not gross usage.
    cfg = cfg._replace(nonaffiliate=7, quantity=10, ledger=100, commission=300, close_price=44.0)
    schedule = np.array([[0, 1, -366], [1, 2, -365]], dtype=np.int64)
    atm = np.zeros((2, 2), dtype=np.int64)
    q.atm_rebook(S, D, P, takes, parts, offers, atm, schedule, coupons, cfg, np.zeros(2, dtype=np.int64))
    np.testing.assert_array_equal(atm[:, e.A_CSOLD], [2, 2])
    np.testing.assert_array_equal(atm[:, e.A_SOLD], [1, 0])
    assert D[e.D_ATM, 1] == 88 * 9700 // 10_000
    assert q.shelf_capacity(S, P, offers, atm, schedule, cfg, 1, -365) == 14
