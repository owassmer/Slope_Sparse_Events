"""Unbilled whole-tree timing, separate from the inline correctness diagnostics."""
from __future__ import annotations

import json
import os
import resource
import signal
import sys
import time
from pathlib import Path

import numpy as np

from app.analysis.frontier import Decision
from app.disputes.chronological import ChronologicalWalk

# Same daily-cash recorded setup as the 001-5a measurement.
LEAD_RUN = 'akoustis_20240514-agent_plus_jev-20260929T052558Z'


class TimeLimit(Exception):
    pass


class MeasuredWalk(ChronologicalWalk):
    def __init__(self, fc, dispute, report):
        super().__init__(fc, dispute)
        self.report = report
        self.histories = self.depth = self.max_depth = 0

    def _loop(self, s, chain, pending, *args):
        self.depth = len(pending | {Decision(n, c) for n, c, _ in s.steps})
        self.max_depth = max(self.max_depth, self.depth)
        self.report(self, False)
        return super()._loop(s, chain, pending, *args)

    def emit(self, *args, **kwargs):
        before = len(self.out)
        result = super().emit(*args, **kwargs)
        self.histories += len(self.out) - before
        self.report(self, True)
        return result


def measure(row=0, seconds=900, run=LEAD_RUN, output=Path('var/diag/001-5b')):
    """Keep the actual walk and its outputs; no hydration, replay checks or pruning."""
    if not 0 <= row < 512 or not 0 < seconds <= 1000:
        raise ValueError('row must be 0..511; seconds must be positive and <=1000')
    from app.analysis.build import basis_for, run_context
    from app.disputes.forecast import Forecaster
    from app.disputes.parallel import _variant

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / f'draw-{row}.json'
    started = time.perf_counter()
    last = started
    walk = None
    result = dict(run=run, native_row=row, scope='whole tree from review',
                  inline_checks=False, seconds_limit=seconds,
                  counting='emitted terminal histories, not probability weighted',
                  depth_definition='resolved decisions, including answers without booked steps',
                  status='running', setup_seconds=None, histories=0, depth=0, max_depth=0)

    def snapshot(w=None):
        if w is not None:
            result.update(histories=len(w.out), depth=w.depth, max_depth=w.max_depth)
        result.update(elapsed_seconds=time.perf_counter() - started,
                      peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss *
                      (1 if sys.platform == 'darwin' else 1024))
        tmp = target.with_suffix('.tmp')
        tmp.write_text(json.dumps(result, indent=2) + '\n')
        tmp.replace(target)
        stream.write(json.dumps(result) + '\n')
        stream.flush()

    def report(w, emitted):
        nonlocal last
        now = time.perf_counter()
        if emitted or now - last >= 5:
            snapshot(w)
            last = now

    def stop(*_):
        raise TimeLimit()

    def forbidden(_):
        raise RuntimeError('walk measurement cannot hydrate or call a judgment model')

    old_cache = os.environ.get('SLOPE_JEV_CACHE_ONLY')
    os.environ['SLOPE_JEV_CACHE_ONLY'] = '1'
    old = signal.getsignal(signal.SIGALRM)
    with target.with_suffix('.jsonl').open('w') as stream:
        snapshot()
        signal.signal(signal.SIGALRM, stop)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        try:
            ctx = run_context(run, Path('runs/recorded'))
            setup, sens = _variant(ctx)
            fc = Forecaster(ctx['live'], ctx['findings'], borrower=ctx['borrower'], review=ctx['review'],
                            horizon=setup.horizon, hydrate=forbidden, setup=setup,
                            basis=basis_for(ctx['feed'], setup), slots=ctx['slots'], model=ctx['m'], sens=sens)
            if fc.draws.n != 512:
                raise ValueError('measurement requires the native 512-draw population')
            d = next(d for d, _ in fc.ordered()
                     if d.stage == 'liability_pending' and d.borrower_role == 'debtor')
            fc.draws = fc.draws.sub(np.arange(fc.draws.n) == row)
            fc.draws.prefixes = {}
            result['setup_seconds'] = time.perf_counter() - started
            walk = MeasuredWalk(fc, d, report)
            walk.run()
            result['status'] = 'completed'
        except TimeLimit:
            result['status'] = 'time_limit'
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
            result['final_count'] = len(walk.out) if walk is not None and result['status'] == 'completed' else None
            snapshot(walk)
    return result
