"""Unbilled native-root inventory and full-population timing (001-1k).

Uses fresh_walk's cut and parallel's actual skeleton, not hand-written prefixes.
No hydrate/judgment call is needed to enumerate or walk paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import resource
import sys
import tempfile
import time
import types
from dataclasses import asdict
from pathlib import Path

import numpy as np

from tools.fresh_walk import CUT, RUN

OUT = Path('var/diag/001-1k')


def skeleton():
    from app.analysis.build import basis_for, run_context
    from app.disputes import forecast as F
    from app.disputes import parallel

    ctx = run_context(RUN, Path('runs/recorded'))
    setup, sens = parallel._variant(ctx)
    def no_hydration(_):
        raise RuntimeError('measurement must not request hydration or judgment')

    fc = F.Forecaster(ctx['live'], ctx['findings'], borrower=ctx['borrower'], review=ctx['review'],
                      horizon=setup.horizon, hydrate=no_hydration, setup=setup,
                      basis=basis_for(ctx['feed'], setup), slots=ctx['slots'], model=ctx['m'], sens=sens)
    assert fc.draws.n == 512
    d = next(d for d, _ in fc.ordered() if d.stage == F.PENDING and d.borrower_role == 'debtor')
    segment = next(c for c in parallel._child.__code__.co_consts
                   if isinstance(c, types.CodeType) and c.co_name == 'segment')
    calls = []

    def profile(frame, event, result):
        if event != 'return' or frame.f_code is not segment:
            return
        caller = frame.f_back.f_locals
        w, s = caller['self'], caller['s']
        mask = w.mask_of(s.steps)
        population = np.ones(fc.draws.n, dtype=bool) if mask is None else mask.copy()
        reason = None
        state = asdict(s)
        state['edge_count'] = len(state.pop('edges'))
        calls.append((dict(name=prefix_name(s.steps), key=result[0], continuation=caller['name'],
                           state=state, prefix_steps=len(s.steps), supported_draws=int(population.sum()),
                           chronological_unavailable=reason),
                      (w, s, caller['a'], caller['kw'], population, list(w._watch))))

    # _child instruments these classes in-place. Restore them before timing either walker.
    originals = {cls: dict(vars(cls)) for cls in (F._Walk, F.Forecaster)}
    old_cut = parallel.CUT
    env = {k: v for k, v in os.environ.items() if k.startswith('SLOPE_WALK_')}
    try:
        for k in env:
            del os.environ[k]
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp, 'empty.pkl')
            empty.write_bytes(pickle.dumps([]))
            os.environ['SLOPE_WALK_ROOTS'] = str(empty)
            parallel.CUT = CUT
            sys.setprofile(profile)
            parallel._child(fc, d, 0, tmp, sys.stderr)
    finally:
        sys.setprofile(None)
        parallel.CUT = old_cut
        os.environ.pop('SLOPE_WALK_ROOTS', None)
        os.environ.update(env)
        for cls, attrs in originals.items():
            for name, value in attrs.items():
                if name not in ('__dict__', '__weakref__'):
                    setattr(cls, name, value)
    fc._raise_more = set(fc._raise_more)
    return fc, d, calls


def prefix_name(steps):
    """Content address: inventory insertions cannot rename a root."""
    encoded = json.dumps(steps, separators=(',', ':')).encode()
    return 'prefix-' + hashlib.sha256(encoded).hexdigest()[:16]


def inventory(calls):
    roots = [r for r, _ in calls]
    eligible = sorted((r for r in roots if r['chronological_unavailable'] is None),
                      key=lambda r: (r['supported_draws'], r['prefix_steps'], r['name']))
    positions = np.linspace(0, len(eligible) - 1, min(20, len(eligible)), dtype=int)
    sample = [eligible[i]['name'] for i in positions]
    report = dict(run=RUN, cut=CUT, draws=512, roots=roots, sample=sample,
                  size_measure='supported draw count, then prefix length; skeleton has no descendant counts',
                  unsupported_roots=sum(r['chronological_unavailable'] is not None for r in roots))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'roots.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', nargs='?', help='prefix name from inventory')
    parser.add_argument('--walker', choices=['chronological', 'current'], default='chronological')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--smoke-row', type=int, help='local wiring check only; not a 512-draw measurement')
    args = parser.parse_args()
    os.environ['SLOPE_JEV_CACHE_ONLY'] = '1'
    t0 = time.perf_counter()
    fc, d, calls = skeleton()
    report = inventory(calls)
    if args.root is None:
        print(json.dumps({k: v for k, v in report.items() if k != 'roots'}))
        return
    from app.disputes.chronological import ChronologicalWalk
    from app.disputes.forecast import _Walk, path_mask

    matches = [item for item in calls if item[0]['name'] == args.root]
    if not matches:
        raise ValueError(f'unknown prefix: {args.root}')
    r, (old, state, a, kw, population, watches) = matches[0]
    if args.smoke_row is not None:
        if not 0 <= args.smoke_row < fc.draws.n or not population[args.smoke_row]:
            raise ValueError('smoke row is outside root support')
        population = np.arange(fc.draws.n) == args.smoke_row
    result = dict(root=r, walker=args.walker, draws=fc.draws.n, walked_population=int(population.sum()),
                  setup_s=time.perf_counter() - t0)
    suffix = '' if args.smoke_row is None else f'-smoke{args.smoke_row}'
    target = OUT / f'{args.root}-{args.walker}{suffix}.json'
    from tools.measurement_stream import MeasurementStream, emitted_histories

    result['status'] = 'started'
    target.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)
    start = time.perf_counter()
    with MeasurementStream(target, result) as stream:
        if args.check:
            sys.path.insert(0, str(Path('tests').resolve()))
            from walk_order import Checker
            checkers = {int(row): Checker(fc, d, int(row), len(state.steps))
                        for row in np.flatnonzero(population)}

        def consume(p):
            checks = []
            if args.check:
                mask = path_mask(p, fc.draws.n)
                for row, checker in checkers.items():
                    if mask is None or mask[row]:
                        from datetime import timedelta

                        bad = checker.history(p)
                        for violation in bad:
                            violation['date'] = (fc.review + timedelta(days=int(violation['day']))).isoformat()
                        checks.append(dict(row=row, violations=bad))
            stream.append(dict(history=stream.histories, steps=p.steps, outcome=p.outcome,
                               elapsed_s=time.perf_counter() - start, checks=checks))

        if args.walker == 'chronological':
            if r['chronological_unavailable']:
                raise NotImplementedError(r['chronological_unavailable'])
            w = ChronologicalWalk(fc, d)
            with emitted_histories(w, consume):
                w.run_from(state, population)
        else:
            w = old
            w._population, w._watch = population, watches
            with emitted_histories(w, consume):
                getattr(_Walk, r['continuation'])(w, state, *a, **kw)
        result.update(status='walked', walk_s=time.perf_counter() - start, histories=len(w.out),
                      peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss *
                      (1 if sys.platform == 'darwin' else 1024))
        stream.result.update(result)
        if stream.violations:
            raise RuntimeError(f'no-skip check: {stream.violations} violations')
    print(json.dumps(stream.result), flush=True)


if __name__ == '__main__':
    main()
