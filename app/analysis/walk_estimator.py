"""Uniform Knuth probes of the actual chronological scheduler; no forecasts."""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

import numpy as np

from app.disputes.chronological import ChronologicalWalk


class ProbeWalk(ChronologicalWalk):
    """Enumerate immediate children, then replay one inside its original scope.

    Replaying the parent preserves scoped question classes/masks and constructor
    continuations. Only the enumeration pass is charged to the estimated walk;
    replay and sampling overhead are not costs of an exhaustive walk.
    """

    def __init__(self, fc, dispute, rng):
        super().__init__(fc, dispute)
        self.rng = rng
        self.intercept = None
        self.weight = 1
        self.leaves = self.cost = self.shared_cost = 0
        self.top = None
        self.degrees = []

    def _loop(self, *args):
        if self.intercept is not None:
            return self.intercept(args)
        children = []
        self.intercept = children.append
        before = len(self.out)
        started = time.perf_counter()
        try:
            super()._loop(*args)
        finally:
            self.intercept = None
        cost = (time.perf_counter() - started) * self.weight
        self.cost += cost
        if self.top is None:
            self.shared_cost += cost
        self.leaves += self.weight * (len(self.out) - before)
        self.out.clear()
        self.keys.clear()
        degree = len(children)
        self.degrees.append(degree)
        if not degree:
            return
        chosen = self.rng.randrange(degree)
        if degree > 1 and self.top is None:
            parent = args[0].steps
            child = children[chosen][0].steps
            self.top = repr(child[len(parent):]) or f'child-{chosen}'
        self.weight *= degree
        index = 0

        def select(child):
            nonlocal index
            take = index == chosen
            index += 1
            if take:
                self.intercept = None
                try:
                    self._loop(*child)
                finally:
                    self.intercept = select

        self.intercept = select
        try:
            super()._loop(*args)
        finally:
            self.intercept = None


def interval(values):
    """Approximate 95% normal Monte Carlo interval, not a guaranteed bound."""
    n = len(values)
    mean = float(np.mean(values)) if n else None
    se = float(np.std(values, ddof=1) / np.sqrt(n)) if n > 1 else None
    return dict(mean=mean, standard_error=se,
                approximate_95_percent_interval=[max(0, mean - 1.96 * se), mean + 1.96 * se]
                if se is not None else None)


def summarize(samples):
    total = sum(s['seconds'] for s in samples)
    branches = {}
    for branch in sorted({s['top_branch'] for s in samples}):
        costs = [s['seconds'] - s['shared_seconds'] if s['top_branch'] == branch else 0 for s in samples]
        leaves = [s['leaves'] if s['top_branch'] == branch else 0 for s in samples]
        branches[branch] = dict(probes=sum(s['top_branch'] == branch for s in samples),
                                leaves=interval(leaves), seconds=interval(costs),
                                cost_share=sum(costs) / total if total else None)
    return dict(completed_probes=len(samples), leaves=interval([s['leaves'] for s in samples]),
                seconds=interval([s['seconds'] for s in samples]), top_branches=branches,
                shared_seconds=interval([s['shared_seconds'] for s in samples]),
                shared_cost_share=sum(s['shared_seconds'] for s in samples) / total if total else None)


def estimate(row=0, probes=200, seconds=900, seed=20261001, output=Path('var/diag/001-5c')):
    import os
    import signal

    from app.analysis.build import basis_for, run_context
    from app.analysis.walk_measurement import LEAD_RUN, TimeLimit
    from app.disputes.forecast import Forecaster
    from app.disputes.parallel import _variant

    if not 0 <= row < 512 or probes < 2 or not 0 < seconds <= 1000:
        raise ValueError('row 0..511, probes >=2, seconds in (0,1000] required')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / f'draw-{row}.json'
    started = time.perf_counter()
    samples = []
    result = dict(run=LEAD_RUN, native_row=row, seed=seed, requested_probes=probes,
                  seconds_limit=seconds, status='running',
                  method='Uniform independent Knuth root-to-leaf probes; no model probabilities',
                  interval='Approximate 95% normal Monte Carlo interval; heavy tails may understate uncertainty. '
                           'Time-limited prefixes can be biased; use completed fixed-count runs for inference.',
                  cost_scope='Scheduler plus terminal emission; excludes setup, replay/sampling overhead and '
                             'retention of all leaves. Warm caches; not a memory or wall-clock guarantee.')

    def save():
        result.update(summarize(samples), elapsed_seconds=time.perf_counter() - started)
        tmp = target.with_suffix('.tmp')
        tmp.write_text(json.dumps(result, indent=2) + '\n')
        tmp.replace(target)

    def forbidden(_):
        raise RuntimeError('estimator cannot hydrate or call a judgment model')

    def stop(*_):
        raise TimeLimit()

    old_cache = os.environ.get('SLOPE_JEV_CACHE_ONLY')
    os.environ['SLOPE_JEV_CACHE_ONLY'] = '1'
    old = signal.signal(signal.SIGALRM, stop)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        save()
        ctx = run_context(LEAD_RUN, Path('runs/recorded'))
        setup, sens = _variant(ctx)
        fc = Forecaster(ctx['live'], ctx['findings'], borrower=ctx['borrower'], review=ctx['review'],
                        horizon=setup.horizon, hydrate=forbidden, setup=setup,
                        basis=basis_for(ctx['feed'], setup), slots=ctx['slots'], model=ctx['m'], sens=sens)
        if fc.draws.n != 512:
            raise ValueError('requires native 512-draw population')
        d = next(d for d, _ in fc.ordered() if d.stage == 'liability_pending' and d.borrower_role == 'debtor')
        fc.draws = fc.draws.sub(np.arange(fc.draws.n) == row)
        fc.draws.prefixes = {}
        result['setup_seconds'] = time.perf_counter() - started
        rng = random.Random(seed)
        with target.with_suffix('.jsonl').open('w') as stream:
            for i in range(probes):
                walk = ProbeWalk(fc, d, rng)
                walk.run()
                sample = dict(probe=i, leaves=walk.leaves, seconds=walk.cost,
                              shared_seconds=walk.shared_cost, top_branch=walk.top or 'unbranched',
                              degrees=walk.degrees)
                samples.append(sample)
                stream.write(json.dumps(sample) + '\n')
                stream.flush()
                save()
        result['status'] = 'completed'
    except TimeLimit:
        result['status'] = 'time_limit_incomplete_probe_discarded'
    except BaseException as exc:
        result.update(status='failed', error=repr(exc))
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
        if old_cache is None:
            os.environ.pop('SLOPE_JEV_CACHE_ONLY', None)
        else:
            os.environ['SLOPE_JEV_CACHE_ONLY'] = old_cache
        save()
    return result
