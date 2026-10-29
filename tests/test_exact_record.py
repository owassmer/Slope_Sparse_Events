"""One draw's record at the compiled frontier, before its answer is booked."""
from collections import Counter

import numpy as np
import pytest

from app.analysis.events import Chain
from app.disputes.forecast import as_of, group_classes, situation_class
from app.exact import agenda, decode, oracle, record, replay, walk
from app.exact import fields_record as r
from app.exact import fields_walk as w


def rows(ch, steps):
    """follow's loop, exposing each decision before advance books it."""
    for draw in range(ch.n):
        k, ln = replay.constants(ch, draw)
        st = replay.state(ch, draw, ln)
        ws = replay.workspace(ch, draw, st)
        wk = walk.WK(listing=ch.fin is not None and ch.fin.listing_deadline is not None)
        walk.init(st.S)
        i = 0
        while True:
            agenda.refresh(st, k)
            kind, which, day = walk.frontier(st, k, wk)
            if kind == w.IT_NONE:
                break
            if kind != w.IT_DECISION:
                agenda.until(st, k, day + 1)
                continue
            agenda.until(st, k, day)
            if i >= len(steps) or walk.step_dec(steps[i]) != which:
                st.S[w.ANS0 + which] = w.ABSENT
                continue
            step = steps[i]
            idx = -1
            if not agenda.waits(step[0], step[1]):
                idx = agenda.prepare(st, k, step)
            ints, floats = np.empty(r.NI, np.int64), np.empty(r.NF, np.float64)
            record.record(st, k, day, step[0], step[1], ws.scratch, ints, floats)
            yield draw, i, ints, floats
            if idx >= 0:
                proj = agenda.NO_PROJECTION
                if step[0] == agenda.a.SETTLE and step[2] == agenda.a.YES:
                    start, end = agenda.settlement_window(st, k, step[1])
                    pd = end if k.settle_end else start + k.settle_lag
                    got = agenda.project(st, k, ws, idx, max(start, 0), pd, False)
                    proj = (got[6], got[1], got[3])
                agenda.conclude(st, k, step, idx, proj)
            else:
                agenda.advance(st, k, step, ws)
            walk.record_step(st.S, step, True)
            i += 1
            if step[0] in (agenda.a.RESPONSE, agenda.a.CASH_FLOOR, agenda.a.CASH_OUT) and step[2] == agenda.a.B_OFFER:
                if i < len(steps) and steps[i, 0] == agenda.a.OFFERING:
                    agenda.advance(st, k, steps[i], ws)
                    walk.record_step(st.S, steps[i], True)
                    i += 1
        assert i == len(steps)


def flatten(obj, path=''):
    if isinstance(obj, dict):
        return {k: v for key, val in obj.items() for k, v in flatten(val, path + '.' + key).items()}
    if isinstance(obj, (tuple, list)):
        return {k: v for i, val in enumerate(obj) for k, v in flatten(val, path + '.' + str(i)).items()}
    return {path: obj}


def test_record_class_partition():
    # A real history exercises numba's actual signature; every decoded class is
    # checked independently by the existing consumer (including explicit groups).
    for h in oracle.histories(2, (0, 145, 365), seed=2, style='long'):
        fc, d = oracle.context(h.draws)
        ch = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
        ch.instrument_cash()
        steps = np.array([replay.encode(ch, s) for s in h.path.steps])
        for _draw, _i, ints, floats in rows(ch, steps):
            decoded = decode.record(ints, floats)
            code = np.empty(r.NC, np.int64)
            for appeal in (False, True):
                for stay in (False, True):
                    record.situation_class(ints, code, appeal=appeal, stay=stay)
                    base = situation_class(decoded, appeal=appeal, stay=stay)[0]
                    assert decode.situation_class(code) == base
                    record.situation_class(ints, code, appeal=appeal, stay=stay, context=1)
                    context = 'executing|motions_pending|stay_pending'
                    assert decode.situation_class(code, ('', context)) == (base + '.ctx' + context.encode().hex()
                                                                          if base else '')
                    if ints[r.HAS_GROUPS]:
                        record.situation_class(ints, code, appeal=appeal, stay=stay, group=True)
                        want = group_classes(situation_class(decoded, appeal=appeal, stay=stay), decoded['groups'])
                        assert decode.situation_class(code) == want[0]
            before, after = flatten(decoded), flatten(as_of(decoded))
            assert before.keys() == after.keys()
            for key in before:
                np.testing.assert_array_equal(before[key], after[key], err_msg=key)


@pytest.mark.parametrize('style', ['uniform', 'long'])
@pytest.mark.parametrize('seed', [2, 5, 13])
def test_record_fidelity(style, seed):
    counts = Counter()
    for h in oracle.histories(2, (0, 145, 365), seed=seed, style=style):
        fc, d = oracle.context(h.draws)
        py = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
        tr = py.run(h.path.steps)
        ch = Chain(d, fc.setup, fc.m, fc.draws, fc.sens)
        ch.instrument_cash()
        steps = np.array([replay.encode(ch, s) for s in h.path.steps])
        counts['histories'] += 1
        try:
            for draw, i, ints, floats in rows(ch, steps):
                node = h.path.steps[i][0]
                counts[node] += 1
                # Trace replaces a moved stay's motion row by its later court
                # row. Compare the retained motion snapshot at this boundary;
                # a court record needs the elementary scheduler (not follow).
                source = py.questions[i] if node == 'stay' else tr.questions[i]
                expected = flatten(as_of(source))
                actual = flatten(decode.record(ints, floats))
                for field in expected.keys() | actual.keys():
                    x, y = expected.get(field, 'MISSING'), actual.get(field, 'MISSING')
                    if isinstance(x, np.ndarray):
                        x = x[draw]
                    if isinstance(y, np.ndarray):
                        y = y[0]
                    if x != y and field.startswith('.sit.offerings.'):
                        # follow's phase-1 outcome at initiation precedes a
                        # same-day phase-2 question. Python still queues it.
                        # No earlier/later existing attempt may differ here.
                        assert x == 'MISSING'
                        slot = int(field.split('.')[3])
                        assert actual[f'.sit.offerings.{slot}.0'][0] == ints[r.DAY]
                        counts['same_day_offering_record'] += 1
                        continue
                    np.testing.assert_equal(y, x, err_msg=f'{style} {seed} {h.draws[draw]} {i} {node} {field}')
                    counts['fields'] += 1
                code = np.empty(r.NC, np.int64)
                record.situation_class(ints, code, stay=True)
                assert decode.situation_class(code) == situation_class(as_of(source), stay=True)[draw]
        except ValueError as exc:
            assert str(exc) in {
                'projection requires offering outcome booked at its own close date',
                'projection requires registration answer booked at its own order date',
                'projection requires petition answer booked at its own decision date',
            }, str(exc)
            counts[str(exc)] += 1
    print(style, seed, dict(counts))
