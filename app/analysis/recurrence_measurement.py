"""Paired, synthetic scheduler-answer samples. No model calls or observed frequencies."""
from __future__ import annotations

import hashlib
import json
import os
import random
import signal
import time
from pathlib import Path

import numpy as np

from app.analysis.walk_estimator import interval
from app.disputes.chronological import ChronologicalWalk
from app.disputes.recurrence import DECLARATIONS, SCENARIOS


class SampleWalk(ChronologicalWalk):
    """Sample supported scheduler children, replaying in their original scope.

    These answer sets are deliberately defined over flattened constructor outcomes,
    NOT individual model questions (settlement combines offer and acceptance).
    """
    def __init__(self, fc, dispute, seed, answers, bounds):
        super().__init__(fc, dispute, bounds=bounds)
        self.seed, self.answers = seed, answers
        self.intercept = None
        self.touched = False
        self.potential = set()
        self.result = None
        self.history = None

    def _ask(self, s, d, outcome):
        self.potential.update(name for name, bound in SCENARIOS.items() if bound.reached(d.node, s.steps))
        self.touched |= self.bounds.reached(d.node, s.steps)
        return super()._ask(s, d, outcome)

    def offer(self, s, occasion, then):
        self.potential.update(name for name, bound in SCENARIOS.items() if bound.reached('offering', s.steps))
        self.touched |= self.bounds.reached('offering', s.steps)
        return super().offer(s, occasion, then)

    def _loop(self, *args):
        if self.intercept is not None:
            return self.intercept(args)
        children = []
        self.intercept = children.append
        try:
            super()._loop(*args)
        finally:
            self.intercept = None
        if not children:
            return
        if self.result is not None:
            raise RuntimeError('mixed terminal/child expansion needs explicit sampling')
        # Common random numbers until the scenarios diverge. No global RNG
        # consumption by enumeration, replay, or the number of sibling answers.
        key = repr((self.seed, args[0].steps, sorted((d.node, d.ctx) for d in args[2])))
        rng = random.Random(hashlib.sha256(key.encode()).digest())
        weights = answer_weights(len(children), self.answers)
        chosen = rng.choices(range(len(children)), weights=weights)[0]
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

    def emit(self, s, outcome):
        from app.analysis.engine import run

        tr = self.terminal_trace(s)
        self.equivalence(s, outcome, tr, self.mask_of(s.steps))
        basis = self.fc.draws.basis
        ledger = run(basis.line, basis.opening, tr.events)
        filed = int(ledger.petition[0] >= 0)
        self.result = [float(ledger.collected[0]) / 100, filed,
                       float(ledger.stayed[0]) / 100 if filed else 0.0]
        self.history = s.steps


def answer_weights(n, answers):
    if answers == 'uniform':
        return [1] * n
    if answers == 'early_heavy':
        return [4] + [1] * (n - 1)
    if answers == 'late_heavy':
        return [1] * (n - 1) + [4]
    raise ValueError(answers)


def context(row):
    from app.analysis.build import basis_for, run_context
    from app.analysis.walk_measurement import LEAD_RUN
    from app.disputes.forecast import Forecaster
    from app.disputes.parallel import _variant

    def forbidden(_):
        raise RuntimeError('recurrence measurement cannot call or hydrate a judgment model')

    ctx = run_context(LEAD_RUN, Path('runs/recorded'))
    setup, sens = _variant(ctx)
    fc = Forecaster(ctx['live'], ctx['findings'], borrower=ctx['borrower'], review=ctx['review'],
                    horizon=setup.horizon, hydrate=forbidden, setup=setup,
                    basis=basis_for(ctx['feed'], setup), slots=ctx['slots'], model=ctx['m'], sens=sens)
    if fc.draws.n != 512 or str(setup.horizon) != '2024-11-10':
        raise ValueError('requires native 512 draws and 10 Nov horizon')
    d = next(d for d, _ in fc.ordered() if d.stage == 'liability_pending' and d.borrower_role == 'debtor')
    fc.draws = fc.draws.sub(np.arange(fc.draws.n) == row)
    fc.draws.prefixes = {}
    return fc, d


def signed_interval(values):
    result = interval(values)
    if result['standard_error'] is not None:
        m, se = result['mean'], result['standard_error']
        result['approximate_95_percent_interval'] = [m - 1.96 * se, m + 1.96 * se]
    return result


def summarize_pairs(records):
    result = {}
    for answers in ('uniform', 'early_heavy', 'late_heavy'):
        result[answers] = {}
        for scenario in list(SCENARIOS)[1:]:
            pairs = [r for r in records if r['answers'] == answers and r['scenario'] == scenario]
            touched = [r for r in pairs if r['touched']]
            def stats(rows):
                return {metric: signed_interval([r['after'][i] - r['before'][i] for r in rows])
                        for i, metric in enumerate(('collections_dollars', 'filing_probability',
                                                    'filing_exposure_dollars'))}
            def levels(side, rows=pairs):
                values = {metric: interval([r[side][i] for r in rows])
                          for i, metric in enumerate(('collections_dollars', 'filing_probability',
                                                      'filing_exposure_dollars'))}
                filed = [r[side][2] for r in rows if r[side][1]]
                values['balance_given_filing_dollars'] = interval(filed)
                return values
            result[answers][scenario] = dict(n=len(pairs), touched=len(touched),
                touched_share=len(touched) / len(pairs) if pairs else None,
                before=levels('before'), after=levels('after'),
                touched_before=levels('before', touched), touched_after=levels('after', touched),
                aggregate_delta=stats(pairs),
                per_touched_history_delta=stats(touched))
    return result


def measure(row=146, samples=100, seconds=900, output=Path('var/diag/001-11a'), seed=1101):
    from app.analysis.walk_measurement import TimeLimit

    if not 0 <= row < 512 or samples < 1 or not 0 < seconds <= 1000:
        raise ValueError('invalid row, sample count or time limit')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / f'economics-{row}.json'
    records = []
    started = time.monotonic()
    old_cache = os.environ.get('SLOPE_JEV_CACHE_ONLY')
    os.environ['SLOPE_JEV_CACHE_ONLY'] = '1'
    def stop(*_):
        raise TimeLimit()
    old_signal = signal.signal(signal.SIGALRM, stop)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    result = dict(row=row, samples=samples, seed=seed, status='running', declarations=DECLARATIONS,
        method='Paired synthetic flattened scheduler-child answers: uniform; first child weight 4; last child weight 4; all others weight 1. Not model judgments.',
        units='USD. Filing exposure is balance times filing indicator; zero means no filing, NOT zero recovery. Conditional balance is exposure mean / filing probability; undefined when no filings.',
        scope='Same native operating draw; horizon ends 10 Nov. Per-history JSONL retains paired balances and filing indicators. Touched means a bound applies at a reached expansion, including unavailable initiation among siblings. Equal-weight selected draws are not the full 512-draw population.')
    def save():
        result.update(summary=summarize_pairs(records), elapsed_seconds=time.monotonic() - started)
        target.write_text(json.dumps(result, indent=2) + '\n')
    try:
        fc, d = context(row)
        with target.with_suffix('.jsonl').open('w') as stream:
            for answers in ('uniform', 'early_heavy', 'late_heavy'):
                for i in range(samples):
                    base = SampleWalk(fc, d, seed + i, answers, SCENARIOS['unbounded'])
                    base.run()
                    for name, bounds in list(SCENARIOS.items())[1:]:
                        # Until a rule applies, the scheduler and common uniforms
                        # are identical. Reuse that exact paired outcome, not an
                        # approximation or a zero-probability pruning rule.
                        walk = base
                        if name in base.potential:
                            walk = SampleWalk(fc, d, seed + i, answers, bounds)
                            walk.run()
                        record = dict(sample=i, answers=answers, scenario=name, touched=walk.touched,
                                      before=base.result, after=walk.result,
                                      before_history=base.history, after_history=walk.history)
                        records.append(record)
                        stream.write(json.dumps(record) + '\n')
                        stream.flush()
                    save()
        result['status'] = 'completed'
    except TimeLimit:
        result['status'] = 'time_limit_partial_not_fixed_count'
    except BaseException as exc:
        result.update(status='failed', error=repr(exc))
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_signal)
        if old_cache is None:
            os.environ.pop('SLOPE_JEV_CACHE_ONLY', None)
        else:
            os.environ['SLOPE_JEV_CACHE_ONLY'] = old_cache
        save()
    return result
