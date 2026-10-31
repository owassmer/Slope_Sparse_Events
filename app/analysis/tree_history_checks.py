"""Exhaustive native-draw replay gates for pooled history ranges (no model calls)."""
import json
from collections import OrderedDict
from copy import copy
from dataclasses import replace
from functools import lru_cache

import numpy as np

from app.analysis.walk_checks import Checker, FutureChecker
from app.disputes.forecast import atoms, pack_row, path_mask, unpack_row


def empty():
    return {name: {'histories': 0, 'history_draws': 0, 'failed_histories': 0, 'examples': [],
                   'violation_counts': {}, 'categorized_history_draws': 0}
            for name in ('no_future', 'no_skip')}


def merge(a, b):
    for name, row in b.items():
        for key in ('histories', 'history_draws', 'failed_histories'):
            a[name][key] += row[key]
        a[name]['examples'].extend(row['examples'][:max(0, 50 - len(a[name]['examples']))])
        a[name]['categorized_history_draws'] = (a[name].get('categorized_history_draws', 0)
                                                + row.get('categorized_history_draws', 0))
        counts = a[name].setdefault('violation_counts', {})
        for signature, count in row.get('violation_counts', {}).items():
            counts[signature] = counts.get(signature, 0) + count
    return a


class HistoryChecks:
    @staticmethod
    def forecaster():
        from pathlib import Path

        from app.analysis.build import basis_for, run_context
        from app.disputes.forecast import Forecaster
        from app.disputes.parallel import _variant
        from tools.fresh_walk import RUN

        def forbidden(_):
            raise RuntimeError('pool gates never hydrate or call a judgment model')

        ctx = run_context(RUN, Path('runs/recorded'))
        setup, sens = _variant(ctx)
        return Forecaster(ctx['live'], ctx['findings'], borrower=ctx['borrower'], review=ctx['review'],
                             horizon=setup.horizon, hydrate=forbidden, setup=setup,
                             basis=basis_for(ctx['feed'], setup), slots=ctx['slots'], model=ctx['m'], sens=sens)

    def __init__(self, db, nodes, *, cached=True):
        self.cached = cached
        self.fc = self.forecaster()
        self.fc.nodes = dict(nodes)
        self.fc.grouped = {k.split('|#', 1)[0] for k in nodes if '|#' in k and '.g' in k.split('|#', 1)[1]}
        self.db = db
        self.checkers = OrderedDict()
        self.arrays = OrderedDict()
        self.state = empty()
        self.local_blob = lru_cache(maxsize=4096)(self.local_blob)
        self.record_variants = lru_cache(maxsize=256)(self.record_variants)

    def pair(self, instance, row):
        key = instance, row
        if key not in self.checkers:
            d = next(d for d in self.fc.disputes if d.instance_id == instance)
            fc = copy(self.fc)
            fc.nodes, fc.grouped = dict(self.fc.nodes), set(self.fc.grouped)
            fc.draws = self.fc.draws.sub(np.arange(self.fc.draws.n) == row)
            fc.draws.prefixes = None
            self.checkers[key] = (Checker(fc, d, 0, 0, cached=self.cached),
                                  FutureChecker(fc, d, 0, {}, explain=False, cached=self.cached))
            if len(self.checkers) > 8:
                self.checkers.popitem(last=False)
        self.checkers.move_to_end(key)
        return self.checkers[key]

    def local_blob(self, blob, row):
        def local(value):
            if isinstance(value, np.ndarray) and value.ndim and len(value) == self.fc.draws.n:
                return value[list(row)] if isinstance(row, tuple) else value[row:row + 1]
            if isinstance(value, dict):
                return {k: local(v) for k, v in value.items()}
            if isinstance(value, tuple):
                return tuple(local(v) for v in value)
            if isinstance(value, list):
                return [local(v) for v in value]
            return value
        return pack_row(local(unpack_row(blob)))

    def record_variants(self, pid, key):
        completed, early = {}, {}
        for ordinal, (prefix, history, blob) in enumerate(self.db.execute(
                'SELECT DISTINCT r.prefix,r.history,q.blob FROM refs r JOIN questions q '
                'ON q.key=r.key AND q.hash=r.hash WHERE r.piece=? AND r.key=?', (pid, key))):
            pre = tuple(tuple(s) for s in (json.loads(prefix) or []))
            hist = json.loads(history)
            hist = None if hist is None else tuple(tuple(s) for s in hist)
            target, identity = (early, pre[:-1]) if hist is None else (completed, hist)
            target.setdefault(identity, []).append((ordinal, pre, hist, [blob]))
        return completed, early

    def records(self, pid, p):
        keys = {k for edge, _ in p.edges for k in atoms(edge)}
        for base, tags, _ in p.classes:
            keys.update(base + '|' + tag for tag in tags)
        records = {}
        for key in keys:
            matches = []
            completed, early = self.record_variants(pid, key)
            matches.extend(completed.get(p.steps, ()))
            for i in range(len(p.steps) + 1):
                matches.extend(early.get(p.steps[:i], ()))
            records[key] = [m[1:] for m in sorted(matches)]
        return records

    def history(self, pid, p):
        self.batch([(pid, p)])

    def batch(self, histories):
        """Replay each history on its native support as one engine array.

        Every pair is checked after the example cap too. cached=False retains
        the uncached per-draw reference implementation.
        """
        work, results = {}, []
        # Narrow leaves often differ in support while sharing the same engine
        # prefix. Replay them on their batch's union, then judge only native lanes.
        # Never broaden the checked population or count the extra engine lanes.
        narrow = {}
        if self.cached:
            for _, p in histories:
                mask = path_mask(p, self.fc.draws.n)
                rows = np.arange(self.fc.draws.n) if mask is None else np.flatnonzero(mask)
                if len(rows) < 8:
                    narrow.setdefault(p.instance_id, set()).update(map(int, rows))
        known = {d.instance_id for d in self.fc.disputes}
        for pid, p in histories:
            on = path_mask(p, self.fc.draws.n)
            rows = np.arange(self.fc.draws.n) if on is None else np.flatnonzero(on)
            failures = {name: [] for name in self.state}
            results.append((pid, p, len(rows), failures))
            if p.instance_id not in known:
                continue
            records = self.records(pid, p)
            if self.cached:
                union = narrow.get(p.instance_id, ()) if len(rows) < 8 else ()
                support = np.array(sorted(union)) if 0 < len(union) <= 64 else rows
                self.array_history(p, rows, records, failures, support=support)
                continue
            for slot, row in enumerate(rows):
                work.setdefault((p.instance_id, int(row)), []).append((p, slot, records, failures))
        for (_, row), jobs in sorted(work.items()):
            for p, slot, records, failures in jobs:
                self.draw_history(p, slot, row, records, failures)
        for pid, p, count, failures in results:
            for name, result in self.state.items():
                result['histories'] += 1
                result['history_draws'] += count
                unknown = p.instance_id not in known
                if unknown or failures[name]:
                    result['failed_histories'] += 1
                    if len(result['examples']) < 50:
                        example = {'piece': pid, 'steps': p.steps}
                        if unknown:
                            example['error'] = f'Unknown dispute: {p.instance_id}'
                        else:
                            example['checks'] = failures[name]
                        result['examples'].append(example)

    def verdict(self, p, row, name, bad):
        """Count every violation before either the draw or example cap.

        Dates and amount-labelled keys vary across draws; retain the semantic
        failure (including changed fact fields), not millions of dated examples.
        These are violation counts, not counts of unique failed histories.
        """
        self.state[name]['categorized_history_draws'] += 1
        counts = self.state[name]['violation_counts']
        for violation in bad:
            fields = {k: violation[k] for k in ('kind', 'node', 'decision', 'changed', 'error')
                      if k in violation}
            if 'before' in violation:
                fields['before'] = violation['before'][:2] if violation['before'] else None
            if 'step' in violation:
                fields['step'] = violation['step'][:2]
            rebuilt = violation.get('rebuilt', {})
            if 'unavailable' in rebuilt:
                fields['unavailable'] = rebuilt['unavailable']
            if 'facts' in violation.get('changed', ()):
                original = violation['recorded']['facts']
                new = rebuilt['facts']
                fields['fact_fields'] = ([k for k in original if not FutureChecker.equivalent(
                    {k: original[k]}, {k: new.get(k)})] if original and new else ['unavailable'])
            signature = json.dumps(fields, sort_keys=True)
            counts[signature] = counts.get(signature, 0) + 1

    def array_history(self, p, rows, records, failures, *, support=None):
        from functools import lru_cache

        from app.analysis.array_history_checks import order_history
        from app.analysis.check_replay import Replay

        if not len(rows):
            return
        support = rows if support is None else support
        identity = p.instance_id, tuple(support)
        session = self.arrays.get(identity)
        if session is None:
            fc = copy(self.fc)
            fc.nodes, fc.grouped = dict(self.fc.nodes), set(self.fc.grouped)
            fc.draws = self.fc.draws.sub(np.isin(np.arange(self.fc.draws.n), support))
            fc.draws.prefixes = None
            d = next(d for d in fc.disputes if d.instance_id == p.instance_id)
            replay = Replay(fc, d, fc.draws, capacity=64)
            shared_replay = lru_cache(maxsize=64)(replay)
            session = fc, d, shared_replay, {}, {}, OrderedDict(), {}
            # Bound retained daily matrices by both support width and session
            # count; consecutive wide histories can reuse their array prefixes.
            self.arrays[identity] = session
            while len(self.arrays) > 1 and (len(self.arrays) > 8 or
                                          sum(len(k[1]) for k in self.arrays) > 128):
                self.arrays.popitem(last=False)
        elif identity in self.arrays:
            self.arrays.move_to_end(identity)
        fc, d, shared_replay, shared_rows, shared_decoded, order_states, futures = session
        records = {key: [(pre, hist, [self.local_blob(blob, tuple(support)) for blob in blobs])
                         for pre, hist, blobs in variants] for key, variants in records.items()}
        classes = []
        for base, tags, codes in p.classes:
            if codes is not None:
                expanded = np.full(len(support), -1, dtype=np.int8)
                expanded[np.searchsorted(support, rows)] = np.frombuffer(codes, dtype=np.int8)
                codes = expanded.tobytes()
            classes.append((base, tags, codes))
        local = replace(p, mask=None, classes=tuple(classes))
        try:
            orders = order_history(fc, d, local, shared_replay, order_states)
        except Exception as exc:
            orders = [[{'kind': 'replay_error', 'error': f'{type(exc).__name__}: {exc}'}] for _ in support]
        for row in rows:
            slot = int(np.searchsorted(support, row))
            if slot not in futures:
                future = FutureChecker(fc, d, slot, {}, explain=False)
                future.shared_rows = shared_rows
                future.shared_decoded = shared_decoded
                future.replayer.prefix_replay = shared_replay
                futures[slot] = future
            future = futures[slot]
            future.records = records
            try:
                bad_future = future.history(local)
            except Exception as exc:
                bad_future = [{'kind': 'replay_error', 'error': f'{type(exc).__name__}: {exc}'}]
            for name, bad in (('no_future', bad_future), ('no_skip', orders[slot])):
                self.verdict(p, int(row), name, bad)
                if bad and len(failures[name]) < 5:
                    failures[name].append({'draw': int(row), 'violations': bad[:5]})

        for cache in (shared_rows, shared_decoded):
            while len(cache) > 256:
                cache.pop(next(iter(cache)))

    def draw_history(self, p, slot, row, records, failures):
        order, future = self.pair(p.instance_id, int(row))
        future.records = {key: [(pre, hist, [self.local_blob(blob, int(row)) for blob in blobs])
                                for pre, hist, blobs in variants] for key, variants in records.items()}
        local = replace(p, mask=None, classes=tuple(
            (base, tags, None if codes is None else codes[slot:slot + 1])
            for base, tags, codes in p.classes))
        for name, checker in (('no_future', future), ('no_skip', order)):
            try:
                bad = checker.history(local)
            except Exception as exc:
                # Missing facts or an unreconstructible decision is not a pass.
                bad = [{'kind': 'replay_error', 'error': f'{type(exc).__name__}: {exc}'}]
            self.verdict(p, int(row), name, bad)
            if bad and len(failures[name]) < 5:
                failures[name].append({'draw': int(row), 'violations': bad[:5]})
        # Probe cache is otherwise unbounded across millions of histories.
        if len(order._probes) > 4096:
            order._probes.clear()
