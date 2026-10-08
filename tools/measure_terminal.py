"""Bounded local terminal-cost/collision and population-throughput measurement.

No model calls, pruning, or replacement of financial calculations. Outputs stay
in var/diag/001-7a. Each invocation is capped at 180 measured seconds.
"""
# Closures are installed, run synchronously, and restored within each batch.
# ruff: noqa: B023
from __future__ import annotations

import argparse
import inspect
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from tools.measure_walk_sharing import digest


class Deadline(Exception):
    pass


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('terminal', 'solo', 'population'), default='terminal')
    p.add_argument('--size', type=int, choices=(1, 8, 64), default=1)
    p.add_argument('--seconds', type=float, default=120)
    p.add_argument('--output', default='var/diag/001-7a')
    a = p.parse_args()
    if not 0 < a.seconds <= 180:
        p.error('seconds must be in (0, 180]')
    from app.analysis import events
    from app.analysis.build import basis_for, run_context
    from app.analysis.walk_measurement import LEAD_RUN
    from app.disputes import notes
    from app.disputes.chronological import ChronologicalWalk
    from app.disputes.forecast import Forecaster
    from app.disputes.parallel import _variant

    def forbidden(*args, **kwargs):
        raise RuntimeError('measurement forbids judgment calls')

    ctx = run_context(LEAD_RUN, Path('runs/recorded'))
    setup, sens = _variant(ctx)
    ids = [146] + [i for i in range(512) if i != 146][:a.size - 1]
    batches = [[i] for i in ids] if a.mode == 'solo' else [ids]
    results = []
    for batch in batches:
        fc = Forecaster(ctx['live'], ctx['findings'], borrower=ctx['borrower'], review=ctx['review'],
                        horizon=setup.horizon, hydrate=forbidden, setup=setup,
                        basis=basis_for(ctx['feed'], setup), slots=ctx['slots'], model=ctx['m'], sens=sens)
        d = next(d for d, _ in fc.ordered() if d.stage == 'liability_pending' and d.borrower_role == 'debtor')
        fc.draws = fc.draws.sub(np.isin(np.arange(fc.draws.n), batch))
        native = sorted(batch)
        fc.draws.prefixes = {}
        w = ChronologicalWalk(fc, d)
        times, calls = defaultdict(float), Counter()
        states, histories = Counter(), set()
        per_draw = Counter()
        active = []
        patches = []
        replay_steps = 0
        replay_detail = Counter()
        engine_seconds = defaultdict(float)
        engine_calls = Counter()
        terminal_count = 0
        census_time = 0.

        def wrap(obj, name, category):
            old = getattr(obj, name)
            def measured(*args, **kwargs):
                nonlocal replay_steps
                if not active:
                    return old(*args, **kwargs)
                # Deferred reconstruction is charged to deferred questions, not
                # the main path replay/tail. Nested timings are exclusive.
                label = 'deferred_questions' if active[-1][0] == 'deferred_questions' else category
                start = time.perf_counter()
                active.append([label, 0.])
                if name == '_advanced' and label == 'replay':
                    replay_detail['requests'] += 1
                    replay_detail['requested_steps'] += len(args[1])
                if name == 'advance' and label == 'replay':
                    replay_steps += 1
                    if inspect.currentframe().f_back.f_code.co_name == '_advanced':
                        replay_detail['direct_advanced_steps'] += 1
                try:
                    return old(*args, **kwargs)
                finally:
                    elapsed = time.perf_counter() - start
                    label, nested = active.pop()
                    times[label] += elapsed - nested
                    calls[label] += 1
                    if active:
                        active[-1][1] += elapsed
            setattr(obj, name, measured)
            patches.append((obj, name, old))

        if a.mode == 'terminal':
            class Output(list):
                def append(self, value):
                    return super().append(value)
            from app.analysis import engine

            original_run = engine.run
            def engine_run(*args, **kwargs):
                if not active:
                    return original_run(*args, **kwargs)
                category = active[-1][0]
                started = time.perf_counter()
                try:
                    return original_run(*args, **kwargs)
                finally:
                    engine_seconds[category] += time.perf_counter() - started
                    engine_calls[category] += 1
            engine.run = engine_run
            patches.append((engine, 'run', original_run))
            w.out = Output()
            wrap(w.out, 'append', 'handoff')
            for obj, name, category in [
                (events, '_advanced', 'replay'),
                (events.Chain, 'advance', 'replay'),
                (events.Chain, 'finish', 'tail_cash_and_line'),
                (events.Chain, '_snapshot_tail', 'traces_rows'),
                (events.Chain, 'court_questions', 'traces_rows'),
                (events.Chain, 'stay_facts', 'traces_rows'),
                (events.Chain, '_size_unapproved', 'traces_rows'),
                (fc, 'record_late', 'deferred_questions'),
                (notes, 'record', 'deferred_questions'),
                (w, 'equivalence', 'reduction_equivalence'),
                (w, '_classes_of', 'traces_rows'),
            ]:
                wrap(obj, name, category)
        emit, loop = w.emit, w._loop
        budget = a.seconds / len(batches)
        start = time.perf_counter()
        cpu = time.process_time()

        def emitted(s, outcome):
            nonlocal terminal_count, census_time
            mask = w.mask_of(s.steps)
            mask = np.ones(fc.draws.n, dtype=bool) if mask is None else mask
            if a.mode == 'terminal':
                t = time.perf_counter()
                frame = inspect.currentframe().f_back
                while frame is not None and frame.f_code.co_name != '_loop':
                    frame = frame.f_back
                loc = frame.f_locals
                ch = loc['chain']
                ev = ch.ev
                cursors = w._cursors(s, loc['pending'])
                front = ch.next_decisions(cursors)
                key = digest(((ev.cash, ev.lock, ev.capacity, ev.petition, ev.kinds, ev.incurred, ev.proceeds),
                              cursors, [(c.chain, c.decision, c.day) for c in front.candidates],
                              loc['committed'], ch.stays, ch.hearing_requested, ch.suspended, ch.delisted))
                states[key] += 1
                histories.add(digest((s.steps, loc['pending'])))
                census_time += time.perf_counter() - t
                active.append(['traces_rows_other_emit', 0.])
            t = time.perf_counter()
            try:
                result = emit(s, outcome)
            finally:
                if a.mode == 'terminal':
                    label, nested = active.pop()
                    times[label] += time.perf_counter() - t - nested
            terminal_count += 1
            per_draw.update(native[i] for i in np.flatnonzero(mask))
            return result

        def looping(*args, **kwargs):
            if time.perf_counter() - start >= budget:
                raise Deadline
            return loop(*args, **kwargs)

        w.emit, w._loop = emitted, looping
        status = 'complete'
        try:
            w.run()
        except Deadline:
            status = 'bounded DFS prefix'
        finally:
            for obj, name, old in reversed(patches):
                setattr(obj, name, old)
        wall = time.perf_counter() - start
        results.append(dict(draws=native, status=status, wall_seconds=wall,
                            cpu_seconds=time.process_time()-cpu, census_seconds=census_time,
                            terminal_histories=terminal_count, history_draws=sum(per_draw.values()),
                            per_draw=dict(per_draw), exclusive_terminal_seconds=dict(times), calls=dict(calls),
                            replay_advance_calls=replay_steps, replay_detail=dict(replay_detail),
                            engine_inclusive_seconds=dict(engine_seconds), engine_calls=dict(engine_calls),
                            unique_histories=len(histories),
                            distinct_terminal_states=len(states), repeated_groups=sum(v > 1 for v in states.values()),
                            reusable_tails=sum(v-1 for v in states.values()), multiplicities=dict(Counter(states.values()))))
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    report = dict(mode=a.mode, size=a.size, run=LEAD_RUN, results=results,
                  limitation='Bounded DFS prefixes, not random samples; same draws but not matched histories. Setup excluded.')
    (out / f'{a.mode}-{a.size}.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
