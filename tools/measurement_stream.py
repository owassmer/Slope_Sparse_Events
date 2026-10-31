"""Incremental, interruptible diagnostic output (no model or cloud access)."""
from __future__ import annotations

import json
import signal
import time
from collections import Counter
from contextlib import contextmanager


class MeasurementStream:
    def __init__(self, target, metadata, interval=60):
        self.target, self.result = target, dict(metadata)
        self.interval = interval
        self.start = time.perf_counter()
        self.histories = self.history_draws = self.violations = 0
        self.counts, self.questions, self.kinds = Counter(), Counter(), Counter()
        self.examples = {}
        self.errors = Counter()

    def summary(self, status):
        self.result.update(status=status, histories=self.histories, history_draws=self.history_draws,
                           total_seconds=time.perf_counter() - self.start, violations=self.violations,
                           by_question_node=dict(self.counts), questions_checked=dict(self.questions),
                           kinds=dict(self.kinds), examples=self.examples,
                           unchecked_history_draws=sum(self.errors.values()), check_errors=dict(self.errors))
        tmp = self.target.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.result, indent=2) + '\n')
        tmp.replace(self.target)

    def append(self, record):
        self.output.write(json.dumps(record) + '\n')
        self.output.flush()
        self.histories += 1
        for check in record.get('checks', []):
            self.history_draws += 1
            if 'check_error' in check:
                self.errors[check['check_error']] += 1
                continue
            self.questions.update(check.get('questions_checked', {}))
            for violation in check['violations']:
                self.violations += 1
                node = violation.get('node') or violation.get('step', violation.get('decision', ['unknown']))[0]
                self.counts[node] += 1
                self.kinds[violation.get('kind', 'no_skip')] += 1
                self.examples.setdefault(node, dict(row=check['row'], **violation))

    def __enter__(self):
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.output = self.target.with_suffix('.jsonl').open('w')
        self.old_term = signal.getsignal(signal.SIGTERM)
        self.old_alarm = signal.getsignal(signal.SIGALRM)
        def stop(signum, frame):
            raise SystemExit(0)
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGALRM, lambda *_: self.summary('running'))
        signal.setitimer(signal.ITIMER_REAL, self.interval, self.interval)
        self.summary('running')
        return self

    def __exit__(self, cls, exc, tb):
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGTERM, self.old_term)
        signal.signal(signal.SIGALRM, self.old_alarm)
        self.output.close()
        if exc is not None and cls is not SystemExit:
            self.result['error'] = repr(exc)
        self.summary('completed' if cls is None else 'stopped' if cls is SystemExit else 'failed')


@contextmanager
def emitted_histories(walk, consume):
    """Check after emit returns, when all deferred recording is complete."""
    original = walk.emit
    def emit(*args, **kwargs):
        before = len(walk.out)
        result = original(*args, **kwargs)
        for path in walk.out[before:]:
            consume(path)
        return result
    walk.emit = emit
    try:
        yield
    finally:
        walk.emit = original
