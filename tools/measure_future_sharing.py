"""Local empirical continuation comparison; no model calls or product changes.

uv run python -m tools.measure_future_sharing --seconds 150
Only fully visited subtrees count. Sampling is a bounded DFS prefix, not random.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from tools.measure_walk_sharing import digest


class Deadline(Exception):
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=150)
    parser.add_argument('--depths', default='15,17,19,21')
    args = parser.parse_args()
    if not 0 < args.seconds <= 180:
        parser.error('local measurement must be between 0 and 180 seconds')
    from app.analysis.build import basis_for, run_context
    from app.analysis.events import plain
    from app.disputes.chronological import ChronologicalWalk
    from app.disputes.forecast import Forecaster, unpack_row
    from app.disputes.parallel import _variant
    from tools.fresh_walk import RUN

    def forbidden(*a, **kw):
        raise RuntimeError('measurement forbids hydration/judgment calls')

    ctx = run_context(RUN, Path('runs/recorded'))
    setup, sens = _variant(ctx)
    fc = Forecaster(ctx['live'], ctx['findings'], borrower=ctx['borrower'], review=ctx['review'],
                    horizon=setup.horizon, hydrate=forbidden, setup=setup,
                    basis=basis_for(ctx['feed'], setup), slots=ctx['slots'], model=ctx['m'], sens=sens)
    d = next(d for d, _ in fc.ordered() if d.stage == 'liability_pending' and d.borrower_role == 'debtor')
    fc.draws = fc.draws.sub(np.arange(fc.draws.n) == 0)
    fc.draws.prefixes = {}
    walk = ChronologicalWalk(fc, d)
    depths = set(map(int, args.depths.split(',')))
    groups = defaultdict(list)
    stack = []
    started = time.monotonic()
    loop, record, late, emit = walk._loop, fc.record, fc._keep_late, walk.emit
    equivalence = walk.equivalence
    completed = 0
    blobs = {}

    def cash(chain):
        ev = chain.ev
        return (ev.cash, ev.lock, ev.capacity, ev.petition, ev.kinds, ev.incurred, ev.proceeds)

    def event(value, prefix=None):
        blob = digest(value)
        if stack and value[0] in ('questions', 'deferred_question'):
            blobs[blob] = value
        for frame in stack:
            if prefix is None or len(prefix) >= frame['start']:
                frame['trace'].append((value[0], blob))

    def recorded(keys, tr):
        result = record(keys, tr)
        event(('questions', [(key, unpack_row(blob)) for key, blob in result]))
        return result

    def deferred(key, prefix, row):
        event(('deferred_question', key, row), prefix)
        return late(key, prefix, row)

    def equivalent(s, outcome, tr, mask):
        event(('completed_cash', cash(type('View', (), {'ev': tr.events})())))
        return equivalence(s, outcome, tr, mask)

    def emitted(s, outcome):
        nonlocal completed
        result = emit(s, outcome)
        event(('terminal', outcome))
        completed += 1
        return result

    def measured(s, chain, pending, outcome, committed, mask, rows):
        if time.monotonic() - started >= args.seconds:
            raise Deadline
        cursors = walk._cursors(s, pending)
        frontier = chain.next_decisions(cursors)
        selected = [(c.chain, c.decision, c.day) for j, c in enumerate(frontier.candidates)
                    if (frontier.pick == j).any()]
        depth = len(set(pending) | {type(c.decision)(n, cxt) for c in frontier.candidates
                                    for n, cxt, _ in s.steps})
        # All unresolved heads and their keyed dates, not only the selected head.
        dated = [(c.chain, c.decision, c.day) for c in frontier.candidates]
        key = digest((cash(chain), cursors, dated, committed))
        eligible = depth in depths and selected and selected[0][0] != 'deterministic'
        state = {f.name: getattr(s, f.name) for f in dataclasses.fields(s) if f.name != 'edges'}
        state['steps'] = [(n, c, plain(b)) for n, c, b in s.steps]
        state.update(pending=pending, outcome=outcome)
        if eligible:
            stack.append(dict(start=len(s.steps), trace=[]))
        event(('boundary', cash(chain), selected))
        try:
            result = loop(s, chain, pending, outcome, committed, mask, rows)
        except BaseException:
            if eligible:
                stack.pop()
            raise
        if eligible:
            trace = stack.pop()['trace']
            groups[depth, key].append(dict(signature=digest(trace), state=state, trace=trace,
                stays=digest((chain.stays, chain.hearing_requested, chain.suspended, chain.delisted)),
                cash_signature=digest([x for x in trace if x[0] in ('boundary', 'completed_cash', 'terminal')])) )
        return result

    walk.equivalence = equivalent
    walk._loop, fc.record, fc._keep_late, walk.emit = measured, recorded, deferred, emitted
    status = 'exhausted'
    try:
        walk.run()
    except Deadline:
        status = 'bounded DFS prefix'
    finally:
        walk._loop, fc.record, fc._keep_late, walk.emit = loop, record, late, emit
        walk.equivalence = equivalence
    folder = Path('var/diag/001-5d')
    folder.mkdir(parents=True, exist_ok=True)
    counts = []
    differences = []

    def delta(a, b, path=''):
        if digest(a) == digest(b):
            return []
        if isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys():
            return [v for k in a for v in delta(a[k], b[k], f'{path}.{k}')]
        if isinstance(a, (tuple, list)) and isinstance(b, (tuple, list)) and len(a) == len(b):
            return [v for i, (x, y) in enumerate(zip(a, b, strict=True)) for v in delta(x, y, f'{path}[{i}]')]
        return [dict(field=path, left=repr(a), right=repr(b))]
    for depth in sorted(depths):
        buckets = [v for (dep, _), v in groups.items() if dep == depth]
        repeated = [v for v in buckets if len(v) > 1]
        equal = [v for v in repeated if len({x['signature'] for x in v}) == 1]
        varying = set()
        for v in equal:
            varying.update(k for k in v[0]['state'] if len({digest(x['state'][k]) for x in v}) > 1)
        refined = defaultdict(list)
        for i, bucket in enumerate(buckets):
            for x in bucket:
                refined[i, x['stays']].append(x)
        refined_repeated = [v for v in refined.values() if len(v) > 1]
        counts.append(dict(depth=depth, histories=sum(map(len, buckets)), groups=len(buckets),
                           cash_identical_groups=sum(len({x['cash_signature'] for x in v}) == 1 for v in repeated),
                           with_stays_and_listing_repeated=len(refined_repeated),
                           with_stays_and_listing_identical=sum(len({x['signature'] for x in v}) == 1 for v in refined_repeated),
                           repeated_groups=len(repeated), identical_groups=len(equal),
                           different_groups=len(repeated)-len(equal),
                           fields_varying_with_identical_futures=sorted(varying)))
        for v in repeated:
            if len({x['signature'] for x in v}) > 1:
                first = v[0]
                attribution = []
                for other in v[1:]:
                    if first['signature'] == other['signature']:
                        continue
                    mismatch = next(((x, y) for x, y in zip(first['trace'], other['trace'], strict=False) if x != y), None)
                    attribution.append(dict(state_fields=delta(first['state'], other['state']),
                        first_future_difference=delta(blobs[mismatch[0][1]], blobs[mismatch[1][1]])
                        if mismatch and mismatch[0][1] in blobs and mismatch[1][1] in blobs else repr(mismatch)))
                differences.append(dict(depth=depth, attribution=attribution, states=[repr(x['state']) for x in v],
                                        signatures=[x['signature'] for x in v],
                                        traces=[repr(x['trace']) for x in v]))
    report = dict(status=status, seconds=time.monotonic()-started, native_row=0,
                  completed_paths=completed, counts=counts, differences=differences,
                  grouping='cash/locks/capacity/petition/kinds/incurred/proceeds arrays; cursors; all frontier dates; committed date',
                  comparison='recursive ordered boundary cash and obligations, selected decision/date, actual recorded question keys/facts, terminal outcome',
                  limitations='Completed subtrees only; bounded DFS sample. Identical signatures are empirical, not proof of a minimal state. No probability weighting.')
    (folder / 'questions.json').write_text(json.dumps({k: repr(v) for k, v in blobs.items()}, indent=2))
    (folder / 'results.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != 'differences'}, indent=2))


if __name__ == '__main__':
    main()
