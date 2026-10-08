"""Bounded population DFS pieces, with an explicit cover of all unfinished branches.

A route is a sequence of child ordinals in the chronological scheduler, not an
answer probability. Restart replays only ancestors; completed subtrees are never
walked again. All 512 draws start together, including on a restarted piece.
"""
from __future__ import annotations

import hashlib
import json
import shlex
import time
from pathlib import Path

from app.disputes.chronological import ChronologicalWalk


def forbidden(*args, **kwargs):
    raise RuntimeError('population walk forbids judgment calls')


def context():
    from app.analysis.build import basis_for, run_context
    from app.analysis.walk_measurement import LEAD_RUN
    from app.disputes.forecast import Forecaster
    from app.disputes.parallel import _variant

    ctx = run_context(LEAD_RUN, Path('runs/recorded'))
    setup, sens = _variant(ctx)
    fc = Forecaster(ctx['live'], ctx['findings'], borrower=ctx['borrower'], review=ctx['review'],
                    horizon=setup.horizon, hydrate=forbidden, setup=setup,
                    basis=basis_for(ctx['feed'], setup), slots=ctx['slots'], model=ctx['m'], sens=sens)
    d = next(d for d, _ in fc.ordered() if d.stage == 'liability_pending' and d.borrower_role == 'debtor')
    return fc, d


class PieceWalk(ChronologicalWalk):
    def configure(self, *, routes=((),), partition=0, partitions=1, depth=6, stop=lambda: False,
                  watch_results=None):
        self.routes = tuple(tuple(r) for r in routes)
        self.partition, self.partitions, self.depth = partition, partitions, depth
        self.stop = stop
        self.remaining = []
        self.stack = []
        self.visited = 0
        self.watch_results = dict(watch_results or {})

    def _watched(self, s, no, watch, then):
        # Appeal/execution constructors read their no-event subtree and then
        # amend its probability edges. A restart may skip that completed
        # subtree, but must retain its answer about whether the event was read.
        if self.partitions > 1 and len(self.stack[-1][0]) < self.depth:
            raise ValueError('partition depth crosses a watched constructor; use a shallower depth')
        key = json.dumps((self.stack[-1][0], s.steps, no))
        watch.read |= self.watch_results.get(key, False)
        read = super()._watched(s, no, watch, then)
        if key in self.watch_results and self.watch_results[key] != read:
            raise RuntimeError('watched subtree changed on restart')
        self.watch_results[key] = read
        return read

    def owns(self, route):
        # A short terminal belongs to one partition as well; no duplication of
        # the shared top's terminal histories across runner commands.
        key = route[:self.depth]
        digest = hashlib.blake2b(repr(key).encode(), digest_size=8).digest()
        return int.from_bytes(digest, 'little') % self.partitions == self.partition

    def _loop(self, *args):
        if not self._watch and hasattr(self, 'flush'):
            self.flush()
        if self.stack:
            parent = self.stack[-1]
            route = parent[0] + (parent[1],)
            parent[1] += 1
        else:
            route = ()
        below = any(route[:len(r)] == r for r in self.routes)
        ancestor = any(r[:len(route)] == route for r in self.routes)
        if not (below or ancestor):
            return
        if len(route) >= self.depth and not self.owns(route):
            return
        # Once every active watch has seen a reader its answer cannot change.
        # Unwinding can now amend the buffered edges and save that answer.
        if (not self._watch or all(w.read for w in self._watch)) and self.stop():
            # Save an antichain: once deferred, none of this route's descendants
            # are entered. Only owned descendants matter on the next invocation.
            if below:
                self.remaining.append(route)
            else:
                self.remaining.extend(r for r in self.routes if r[:len(route)] == route)
            return
        self.stack.append([route, 0])
        self.visited += 1
        try:
            return super()._loop(*args)
        finally:
            self.stack.pop()

    def emit(self, s, outcome):
        route = self.stack[-1][0]
        # Some draws terminate at an ancestor while its other draws continue.
        # Replaying that ancestor to reach an unfinished child must not emit
        # its already completed terminal population again.
        if self.owns(route) and any(route[:len(r)] == r for r in self.routes):
            return super().emit(s, outcome)


def walk_piece(*, output: Path, seconds=900, partition=0, partitions=1, depth=6, resume: Path | None = None,
               full_events=False):
    """Stream compressed supported financial rows, paths and interned questions.

    `full_events` additionally writes the original events.pkl for comparison.
    Read either format with piece_store.read_events; financial records retain
    native draw IDs and join paths by (steps, financial equivalence key).
    A partial piece is not a completed tree. Its next.txt commands are required
    work, not optional stress paths. Input checkpoints must be from this code.
    """
    if not 0 < seconds <= 1000 or not 0 <= partition < partitions or depth < 1:
        raise ValueError('invalid piece bounds')
    start = time.monotonic()
    routes = ((),)
    watch_results = {}
    if resume is not None:
        saved = json.loads(resume.read_text())
        routes = saved['remaining']
        watch_results = saved['watch_results']
        partition, partitions, depth = (saved[k] for k in ('partition', 'partitions', 'depth'))
    output.mkdir(parents=True, exist_ok=True)
    # Never overwrite already emitted data on an accidental retry.
    from app.analysis.piece_store import PieceWriter

    fc, d = context()
    writer = PieceWriter(output, fc.draws.n, full=full_events)
    w = PieceWalk(fc, d)
    w.configure(routes=routes, partition=partition, partitions=partitions, depth=depth,
                stop=lambda: time.monotonic() - start >= seconds, watch_results=watch_results)
    record, late, emit = fc.record, fc._keep_late, w.emit
    equivalence = w.equivalence
    seen = set()
    histories = history_draws = 0
    emitting = None

    put = writer.put

    def financial(s, outcome, tr, mask):
        from app.analysis.piece_store import financial_rows

        key = equivalence(s, outcome, tr, mask)
        # Keep the finished leaf, not a replay with potentially different support.
        put('financial', (s.steps, key, financial_rows(tr, mask, fc.draws.n)))
        return key

    w.equivalence = financial

    def early(keys, tr):
        result = record(keys, tr)
        put('questions', (fc._rec_at, result))
        return result

    def deferred(key, prefix, row):
        from app.disputes.forecast import pack_row
        put('dated_question', (key, prefix, emitting, pack_row(row)))
        return late(key, prefix, row)

    def emitted(s, outcome):
        nonlocal emitting
        emitting = s.steps
        try:
            emit(s, outcome)
        finally:
            emitting = None
        if not w._watch:
            flush()

    def flush():
        nonlocal histories, history_draws
        from app.disputes.forecast import path_mask

        new = fc.nodes.keys() - seen
        if new:
            put('nodes', {k: fc.nodes[k] for k in sorted(new)})
            seen.update(new)
        for p, key in zip(w.out, w.keys, strict=True):
            put('path', (p, key))
            mask = path_mask(p, fc.draws.n)
            histories += 1
            history_draws += fc.draws.n if mask is None else int(mask.sum())
        w.out.clear()
        w.keys.clear()

    fc.record, fc._keep_late, w.emit, w.flush = early, deferred, emitted, flush
    try:
        w.run()
        flush()
        put('classification', dict(grouped=fc.grouped, classed=fc.classed, node_group=fc.node_group,
                                   class_range=fc.class_range, class_members=fc.class_members,
                                   remitted=fc.remitted, ev_range=getattr(fc, 'ev_range', None)))
    finally:
        writer.close()
    result = dict(complete=not w.remaining, partition=partition, partitions=partitions, depth=depth,
                  remaining=w.remaining, watch_results=w.watch_results,
                  histories=histories, history_draws=history_draws,
                  visited=w.visited, seconds=time.monotonic() - start,
                  events_format='population-piece-v1',
                  events_bytes=(output / 'events.pkl.gz').stat().st_size)
    (output / 'piece.json').write_text(json.dumps(result, indent=2) + '\n')
    if w.remaining:
        # One bounded command continues this cover. Repeat until complete, never
        # interpret the timed prefix as an exhaustive result.
        (output / 'next.txt').write_text(
            f'OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run slope walk-tree --seconds {seconds} '
            f'--resume {shlex.quote(str(output / "piece.json"))} '
            f'--output {shlex.quote(str(output / "next"))}'
            f'{" --full-events" if full_events else ""}\n')
    return result
