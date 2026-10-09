"""Knuth probes below a saved population DFS antichain (never model probabilities)."""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

import numpy as np

from app.analysis.population_walk import PieceWalk, context
from app.disputes.chronological import ChronologicalWalk


class _Selected(Exception):
    """Unwind scoped ancestor replay after the selected child returns."""


class RemainingProbe(PieceWalk):
    def __init__(self, fc, dispute, saved, route, rng):
        super().__init__(fc, dispute)
        self.configure(routes=[route], **{k: saved[k] for k in ('partition', 'partitions', 'depth')},
                       watch_results=saved.get('watch_results', {}))
        if saved.get('recurrence_bounds', vars(type(self.bounds)())) != vars(self.bounds):
            raise ValueError('saved recurrence bounds differ from production scenario')
        self.target = tuple(route)
        self.rng = rng
        self.intercept = None
        self.weight = 1
        self.histories = self.history_draws = self.seconds = 0
        self.charging = False
        self.discovery = None
        self.discovery_seconds = 0

    def _loop(self, *args):
        if self.discovery is not None:
            if not self.discovery.read:
                return ChronologicalWalk._loop(self, *args)
            return
        if self.intercept is not None:
            return self.intercept(args)
        return self.visit(args, ())

    def _watched(self, s, no, watch, then):
        if self.discovery is not None:
            # Exact reader discovery traverses nested constructors normally;
            # their route ordinals are not those of the probing parent.
            return ChronologicalWalk._watched(self, s, no, watch, then)
        key = json.dumps((self.stack[-1][0], s.steps, no))
        if key not in self.watch_results and len(self.stack[-1][0]) >= len(self.target):
            # Constructor support depends on whether ANY descendant reads the
            # event, not whether our sampled descendant does. Resolve exactly,
            # stopping at the first reader, then replay in the original scope.
            old = self.discovery
            self.discovery = watch
            started = time.perf_counter()
            try:
                ChronologicalWalk._watched(self, s, no, watch, then)
            finally:
                self.discovery = old
                if old is None:
                    self.discovery_seconds += time.perf_counter() - started
                self.out.clear()
                self.keys.clear()
            self.watch_results[key] = watch.read
        watch.read |= self.watch_results.get(key, False)
        return ChronologicalWalk._watched(self, s, no, watch, then)

    def emit(self, s, outcome):
        if self.discovery is not None or not self.charging or not self.owns(self.stack[-1][0]):
            return
        from app.disputes.forecast import path_mask
        before = len(self.out)
        ChronologicalWalk.emit(self, s, outcome)
        for p in self.out[before:]:
            mask = path_mask(p, self.fc.draws.n)
            self.histories += self.weight
            self.history_draws += self.weight * (self.fc.draws.n if mask is None else int(mask.sum()))

    def visit(self, args, route):
        if len(route) >= self.depth and not self.owns(route):
            return
        below = len(route) >= len(self.target)
        self.stack.append([route, 0])
        children = []
        previous = self.intercept, self.charging
        if not below:
            index = 0
            chosen = self.target[len(route)]

            def ancestor(child):
                nonlocal index
                take = index == chosen
                index += 1
                if take:
                    self.visit(child, route + (chosen,))
                    raise _Selected()

            self.intercept, self.charging = ancestor, False
            try:
                try:
                    ChronologicalWalk._loop(self, *args)
                except _Selected:
                    pass
                if index <= chosen:
                    raise ValueError('saved child ordinal no longer exists')
            finally:
                self.intercept, self.charging = previous
                self.stack.pop()
            return
        self.intercept = children.append
        self.charging = below
        started = time.perf_counter()
        discovery_before = self.discovery_seconds
        try:
            ChronologicalWalk._loop(self, *args)
            if below:
                self.seconds += self.weight * (time.perf_counter() - started -
                                               (self.discovery_seconds - discovery_before))
            self.out.clear()
            self.keys.clear()
            if not children:
                if not below:
                    raise ValueError(f'saved route no longer exists: {self.target}')
                return
            chosen = self.rng.randrange(len(children)) if below else self.target[len(route)]
            if chosen >= len(children):
                raise ValueError('saved child ordinal no longer exists')
            if below:
                self.weight *= len(children)
            index = 0

            def select(child):
                nonlocal index
                take = index == chosen
                index += 1
                if take:
                    self.visit(child, route + (chosen,))
                    raise _Selected()

            self.intercept = select
            self.charging = False  # ancestor replay must never count terminal draws twice
            try:
                ChronologicalWalk._loop(self, *args)
            except _Selected:
                pass
        finally:
            self.intercept, self.charging = previous
            self.stack.pop()
            self.out.clear()
            self.keys.clear()


class ForestProbe(RemainingProbe):
    """Share deterministic ancestor replay across the preselected IID probes."""

    def __init__(self, fc, dispute, saved, routes, rng, on_sample):
        synthetic = dict(saved[0], partition=0, partitions=1, remaining=[])
        synthetic['watch_results'] = {}
        for state in saved:
            for key, value in state.get('watch_results', {}).items():
                if key in synthetic['watch_results'] and synthetic['watch_results'][key] != value:
                    raise ValueError('inconsistent saved watch results')
                synthetic['watch_results'][key] = value
        super().__init__(fc, dispute, synthetic, (), rng)
        self.targets = {}
        for i, route in routes:
            self.targets.setdefault(tuple(route), []).append(i)
        self.prefixes = {r[:n] for r in self.targets for n in range(len(r) + 1)}
        self.saved = saved
        self.on_sample = on_sample
        self.sampling = False
        self.target = (None,) * 10000

    def visit(self, args, route):
        if self.sampling:
            return super().visit(args, route)
        if route not in self.prefixes:
            return
        if route in self.targets:
            for i in self.targets[route]:
                self.partition, self.partitions, self.depth = (self.saved[i][k] for k in ('partition', 'partitions', 'depth'))
                self.target, self.sampling = route, True
                self.weight = 1
                self.histories = self.history_draws = self.seconds = 0
                try:
                    super().visit(args, route)
                    self.on_sample(i, route, self)
                finally:
                    self.sampling = False
                    self.target = (None,) * 10000
                    self.partition, self.partitions = 0, 1
            if not any(len(r) > len(route) and r[:len(route)] == route for r in self.targets):
                return
        self.stack.append([route, 0])
        previous = self.intercept, self.charging
        index = 0
        last = max(r[len(route)] for r in self.targets if len(r) > len(route) and r[:len(route)] == route)

        def descend(child):
            nonlocal index
            child_route = route + (index,)
            index += 1
            self.visit(child, child_route)
            if index > last:
                raise _Selected()

        self.intercept, self.charging = descend, False
        try:
            try:
                ChronologicalWalk._loop(self, *args)
            except _Selected:
                pass
        finally:
            self.intercept, self.charging = previous
            self.stack.pop()


def spread(values):
    n = len(values)
    mean = float(np.mean(values)) if n else None
    se = float(np.std(values, ddof=1) / np.sqrt(n)) if n > 1 else None
    return dict(mean=mean, standard_error=se,
                interval_90=[max(0, mean - 1.644854 * se), mean + 1.644854 * se] if se is not None else None)


def estimate_remaining(paths, *, output, probes=200, seed=13, seconds=1000,
                       finished=(), calibration_limit=20, calibration_offset=0, timing=()):
    """IID importance route selection, then uniform children; inverse-selection weights.

    Each sample estimates the WHOLE forest, not only the selected piece. Zero
    contributions to other pieces belong in their variances. Fixed-count runs
    support inference; a timed prefix is only a progress record.
    """
    import signal

    from app.analysis.walk_measurement import TimeLimit

    if probes < 2 or not 0 < seconds <= 1100:
        raise ValueError('probes >= 2 and 0 < seconds <= 1100 required')
    paths = sorted(set(Path(p) for p in paths))
    if not paths:
        raise ValueError('no continuation states matched')
    saved = [json.loads(p.read_text()) for p in paths]
    exact = {}
    if finished:
        # Require the full starting antichain, not a split share or a later
        # partial result. Never use the completed piece's empty remaining list.
        def signature(state, key):
            return (state['partition'], state['partitions'], state['depth'],
                    tuple(sorted(tuple(r) for r in state[key])))

        finals = {}
        for p in sorted(Path(p) for p in finished):
            state = json.loads(p.read_text())
            if state['complete'] and state.get('input_routes'):
                finals[signature(state, 'input_routes')] = (p, state)
        matches = [(p, s, finals[signature(s, 'remaining')]) for p, s in zip(paths, saved, strict=True)
                   if s['remaining'] and signature(s, 'remaining') in finals]
        matches = matches[calibration_offset:calibration_offset + calibration_limit]
        if not matches:
            raise ValueError('no complete single-share pieces match these starting states')
        paths, saved = [m[0] for m in matches], [m[1] for m in matches]
        exact = {i: dict(path=str(m[2][0]), **{k: m[2][1][k] for k in ('histories', 'history_draws')})
                 for i, m in enumerate(matches)}
    layouts = {(s['partitions'], s['depth']) for s in saved}
    if len(layouts) != 1:
        raise ValueError('cannot combine different partition layouts')
    by_partition = {}
    for state in saved:
        by_partition.setdefault(state['partition'], []).extend(tuple(r) for r in state['remaining'])
    for partition_routes in by_partition.values():
        ordered = sorted(partition_routes)
        if any(b[:len(a)] == a for a, b in zip(ordered, ordered[1:], strict=False)):
            raise ValueError('overlapping continuation routes would double-count work')
    routes = [(i, route) for i, state in enumerate(saved) for route in state['remaining']]
    # A uniform-only pilot almost never samples the few near-root routes.
    # Mix two depth proposals with uniform support; these are sampling weights,
    # NOT probabilities of financial events. The partition factor accounts for
    # root routes that own only one hash partition of their descendants.
    proposal = np.ones(len(routes)) / len(routes) if routes else np.array([])
    for base in (2, 4):
        weights = np.array([base ** (-len(route)) /
                            (saved[i]['partitions'] if len(route) < saved[i]['depth'] else 1)
                            for i, route in routes])
        if len(weights):
            proposal += weights / weights.sum()
    proposal /= 3
    selection_probability = {(i, tuple(r)): float(q) for (i, r), q in zip(routes, proposal, strict=True)}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    samples = []
    result = dict(status='running', requested_probes=probes, seed=seed, pieces=len(paths), routes=len(routes),
                  method='IID route proposal: equal mixture of uniform, 2^-depth and 4^-depth (partition-adjusted); uniform scheduler children; inverse-selection weighting',
                  interval='Approximate 90% normal Monte Carlo intervals from independent probes; heavy tails can cause undercoverage. Only completed fixed-count runs support inference.',
                  time_scope='Local scheduler and terminal emission CPU wall seconds, excluding ancestor replay, setup, sampling replay, serialization and retained-tree memory. Not elapsed time on parallel hosts.')
    metrics = ('histories', 'history_draws', 'seconds')
    rates = {}
    for p in sorted(set(Path(p) for p in timing)):
        state = json.loads(p.read_text())
        host = 'AWS' if any(part.endswith('.aws') for part in p.parts) else 'GitHub'
        group = rates.setdefault(host, dict(pieces=0, seconds=0, histories=0, history_draws=0))
        group['pieces'] += 1
        for metric in metrics:
            group[metric] += state[metric]
    result['observed_timing'] = rates
    result['timing_conversion'] = ('Worker-hours, not parallel elapsed hours. Two constant-throughput conversions from '
                                   'recorded piece seconds (including restart and serialization); intervals propagate '
                                   'probe uncertainty only, not future host/route speed variation.')

    def save():
        result['completed_probes'] = len(samples)
        result['elapsed_seconds'] = time.perf_counter() - started
        result['remaining'] = {m: spread([s[m] for s in samples]) for m in metrics}
        if not exact:
            result['implied_worker_hours'] = {
                host: {m: spread([s[m] * rate['seconds'] / rate[m] / 3600 for s in samples])
                       if rate[m] else spread([]) for m in ('histories', 'history_draws')}
                for host, rate in rates.items()}
        result['by_piece'] = sorted([
            dict(path=str(p), sampled=sum(s['piece'] == i for s in samples),
                 **{m: spread(([s[m] for s in samples if s['piece'] == i] if exact else
                               [s[m] if s['piece'] == i else 0 for s in samples])
                              if any(s['piece'] == i for s in samples) else []) for m in metrics})
            for i, p in enumerate(paths)], key=lambda r: r['histories']['mean'] or 0, reverse=True)
        if exact:
            by_path = {p['path']: p for p in result['by_piece']}
            checks = []
            for i, actual in exact.items():
                piece = by_path[str(paths[i])]
                check = dict(start=str(paths[i]), exact=actual, completed_probes=piece['sampled'])
                for m in ('histories', 'history_draws'):
                    bounds = piece[m]['interval_90']
                    check[m] = dict(estimate=piece[m], covered=(bounds[0] <= actual[m] <= bounds[1])
                                    if bounds is not None and piece['sampled'] == probes else None)
                checks.append(check)
            result['calibration'] = checks
            result['coverage'] = {m: dict(covered=sum(c[m]['covered'] is True for c in checks),
                                          evaluated=sum(c[m]['covered'] is not None for c in checks))
                                  for m in ('histories', 'history_draws')}
            # Calibration is stratified. Do not label pooled per-piece samples
            # as an estimate of the whole continuation forest.
            result.pop('remaining', None)
        tmp = output / 'estimate.tmp'
        tmp.write_text(json.dumps(result, indent=2) + '\n')
        tmp.replace(output / 'estimate.json')

    def stop(*_):
        raise TimeLimit()

    old = signal.signal(signal.SIGALRM, stop)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        save()
        fc, d = context()
        if fc.draws.n != 512:
            raise ValueError('requires the native 512-draw population')
        result['recurrence_bounds'] = vars(ChronologicalWalk(fc, d).bounds)
        if exact:
            result['method'] = 'Per-piece IID uniform remaining route, then uniform scheduler children; inverse-selection weighting'
        for state in saved:
            if state.get('recurrence_bounds', vars(type(ChronologicalWalk(fc, d).bounds)())) != result['recurrence_bounds']:
                raise ValueError('saved recurrence bounds differ from production scenario')
        rng = random.Random(seed)
        with (output / 'probes.jsonl').open('w') as stream:
            def received(i, route, walk):
                scale = len(saved[i]['remaining']) if exact else 1 / selection_probability[i, tuple(route)]
                sample = dict(piece=i, route=route, route_probability=1 / scale,
                              **{m: getattr(walk, m) * scale for m in metrics})
                samples.append(sample)
                stream.write(json.dumps(sample) + '\n')
                stream.flush()
                save()

            if routes:
                selected = ([(i, rng.choice(state['remaining'])) for i, state in enumerate(saved) for _ in range(probes)]
                            if exact else rng.choices(routes, weights=proposal, k=probes))
                ForestProbe(fc, d, saved, selected, rng, received).run()
            else:
                samples.extend(dict(piece=None, route=None, **dict.fromkeys(metrics, 0)) for _ in range(probes))
            if len(samples) != probes * (len(saved) if exact else 1):
                raise ValueError('some selected routes were not reached; continuation state is incompatible')
        result['status'] = 'completed'
    except TimeLimit:
        result['status'] = 'time_limit_incomplete_probe_discarded'
    except BaseException as exc:
        result.update(status='failed', error=repr(exc))
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
        save()
    return result
