"""Bounded real-history timing/equivalence run against the uncached replay checker.

Use a pool SQLite database made from real pieces. No walk or model is run.
Each completed block is flushed to JSONL so a runner timeout preserves results.
"""
import argparse
import hashlib
import json
import pickle
import sqlite3
import time
import zlib
from pathlib import Path

from app.analysis.tree_history_checks import HistoryChecks


class ComparedChecks(HistoryChecks):
    """Capture every verdict, not just the reduction's capped examples."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.verdicts = {}

    def pair(self, instance, row):
        pair = super().pair(instance, row)
        for name, checker in zip(('no_skip', 'no_future'), pair, strict=True):
            if getattr(checker, '_audited', False):
                continue
            original = checker.history

            def history(p, original=original, name=name):
                try:
                    bad = original(p)
                except Exception as exc:
                    bad = [{'kind': 'replay_error', 'error': f'{type(exc).__name__}: {exc}'}]
                identity = (instance, row, name, hashlib.sha256(pickle.dumps(p)).hexdigest())
                digest = hashlib.sha256(json.dumps(bad, sort_keys=True).encode()).hexdigest()
                self.verdicts.setdefault(identity, []).append(digest)
                return bad

            checker.history, checker._audited = history, True
        return pair


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--limit', type=int, default=300)
    parser.add_argument('--batch-size', type=int, default=20)
    parser.add_argument('--compare', action='store_true')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f'{args.database.resolve().as_uri()}?mode=ro', uri=True) as db, args.output.open('w') as out:
        nodes = {k: pickle.loads(b) for k, b in db.execute('SELECT * FROM nodes')}
        cls = ComparedChecks if args.compare else HistoryChecks
        fast = cls(db, nodes)
        slow = cls(db, nodes, cached=False) if args.compare else None
        rows = iter(db.execute('SELECT piece,blob FROM paths ORDER BY id LIMIT ? OFFSET ?',
                               (args.limit, args.offset)))
        checked = 0
        while checked < args.limit:
            batch = []
            for _ in range(min(args.batch_size, args.limit - checked)):
                row = next(rows, None)
                if row is None:
                    break
                pid, blob = row
                batch.append((pid, pickle.loads(zlib.decompress(blob))))
            if not batch:
                break
            start = time.perf_counter()
            # Reset reporting, not prefix caches, between blocks. This compares
            # every block's examples rather than only the first fifty failures.
            from app.analysis.tree_history_checks import empty

            fast.state = empty()
            if slow is not None:
                fast.verdicts.clear()
                slow.verdicts.clear()
            fast.batch(batch)
            seconds = time.perf_counter() - start
            result = dict(offset=args.offset + checked, histories=len(batch), seconds=seconds,
                          seconds_per_history=seconds / len(batch),
                          history_draws=fast.state['no_future']['history_draws'])
            if slow is not None:
                slow.state = empty()
                start = time.perf_counter()
                for pid, path in batch:
                    slow.history(pid, path)
                result.update(slow_seconds=time.perf_counter() - start,
                              identical=fast.state == slow.state and fast.verdicts == slow.verdicts,
                              verdict_pairs=sum(map(len, fast.verdicts.values())))
                if not result['identical']:
                    result.update(fast=fast.state, slow=slow.state)
            out.write(json.dumps(result) + '\n')
            out.flush()
            print(json.dumps({k: v for k, v in result.items() if k not in ('fast', 'slow')}), flush=True)
            if result.get('identical') is False:
                raise AssertionError('cached and uncached verdicts differ; see output')
            checked += len(batch)


if __name__ == '__main__':
    main()
