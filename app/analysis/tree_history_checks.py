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
    return {name: {'histories': 0, 'history_draws': 0, 'failed_histories': 0, 'examples': []}
            for name in ('no_future', 'no_skip')}


def merge(a, b):
    for name, row in b.items():
        for key in ('histories', 'history_draws', 'failed_histories'):
            a[name][key] += row[key]
        a[name]['examples'].extend(row['examples'][:max(0, 50 - len(a[name]['examples']))])
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

    def __init__(self, db, nodes):
        self.fc = self.forecaster()
        self.fc.nodes = dict(nodes)
        self.fc.grouped = {k.split('|#', 1)[0] for k in nodes if '|#' in k and '.g' in k.split('|#', 1)[1]}
        self.db = db
        self.checkers = OrderedDict()
        self.state = empty()
        self.local_blob = lru_cache(maxsize=4096)(self.local_blob)

    def pair(self, instance, row):
        key = instance, row
        if key not in self.checkers:
            d = next(d for d in self.fc.disputes if d.instance_id == instance)
            fc = copy(self.fc)
            fc.nodes, fc.grouped = dict(self.fc.nodes), set(self.fc.grouped)
            fc.draws = self.fc.draws.sub(np.arange(self.fc.draws.n) == row)
            fc.draws.prefixes = None
            self.checkers[key] = (Checker(fc, d, 0, 0), FutureChecker(fc, d, 0, {}, explain=False))
            if len(self.checkers) > 8:
                self.checkers.popitem(last=False)
        self.checkers.move_to_end(key)
        return self.checkers[key]

    def local_blob(self, blob, row):
        def local(value):
            if isinstance(value, np.ndarray) and value.ndim and len(value) == self.fc.draws.n:
                return value[row:row + 1]
            if isinstance(value, dict):
                return {k: local(v) for k, v in value.items()}
            if isinstance(value, tuple):
                return tuple(local(v) for v in value)
            if isinstance(value, list):
                return [local(v) for v in value]
            return value
        return pack_row(local(unpack_row(blob)))

    def records(self, pid, p):
        keys = {k for edge, _ in p.edges for k in atoms(edge)}
        for base, tags, _ in p.classes:
            keys.update(base + '|' + tag for tag in tags)
        records = {}
        for key in keys:
            matches = []
            for prefix, history, blob in self.db.execute(
                    'SELECT DISTINCT r.prefix,r.history,q.blob FROM refs r JOIN questions q '
                    'ON q.key=r.key AND q.hash=r.hash WHERE r.piece=? AND r.key=?', (pid, key)):
                pre = tuple(tuple(s) for s in (json.loads(prefix) or []))
                hist = json.loads(history)
                hist = None if hist is None else tuple(tuple(s) for s in hist)
                if (hist is not None and hist != p.steps) or (hist is None and p.steps[:len(pre)-1] != pre[:-1]):
                    continue
                matches.append((pre, hist, [blob]))
            records[key] = matches
        return records

    def history(self, pid, p):
        on = path_mask(p, self.fc.draws.n)
        rows = np.arange(self.fc.draws.n) if on is None else np.flatnonzero(on)
        records = self.records(pid, p)
        if not any(d.instance_id == p.instance_id for d in self.fc.disputes):
            for result in self.state.values():
                result['histories'] += 1
                result['history_draws'] += len(rows)
                result['failed_histories'] += 1
                if len(result['examples']) < 50:
                    result['examples'].append({'piece': pid, 'steps': p.steps,
                                               'error': f'Unknown dispute: {p.instance_id}'})
            return
        failures = {name: [] for name in self.state}
        for slot, row in enumerate(rows):
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
                if bad and len(failures[name]) < 5:
                    failures[name].append({'draw': int(row), 'violations': bad[:5]})
            # Probe cache is otherwise unbounded across millions of histories.
            if len(order._probes) > 4096:
                order._probes.clear()
        for name, result in self.state.items():
            result['histories'] += 1
            result['history_draws'] += len(rows)
            if failures[name]:
                result['failed_histories'] += 1
                if len(result['examples']) < 50:
                    result['examples'].append({'piece': pid, 'steps': p.steps, 'checks': failures[name]})
