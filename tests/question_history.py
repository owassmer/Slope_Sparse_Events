"""Recording adapter for walk_order.FutureChecker; no model calls or walker changes.

Histories contain keys/classes, not their per-occurrence facts. Save the actual
recording boundary alongside them instead of treating a new replay as the record.
"""
from contextlib import contextmanager

from app.disputes.forecast import pack_row


@contextmanager
def question_records(walk):
    fc = walk.fc
    records = {}
    record, late, emit = fc.record, fc._keep_late, walk.emit
    emitting = [None]

    def emitted(state, outcome):
        emitting[0] = state.steps
        try:
            return emit(state, outcome)
        finally:
            emitting[0] = None

    def remember(key, prefix, blob, deferred):
        records.setdefault((key, tuple(prefix), emitting[0] if deferred else None), set()).add(blob)

    def early(keys, tr):
        result = record(keys, tr)
        for key, blob in result:
            remember(key, fc._rec_at, blob, False)
        return result

    def completed(key, prefix, row):
        remember(key, prefix, pack_row(row), True)
        return late(key, prefix, row)

    fc.record, fc._keep_late, walk.emit = early, completed, emitted
    try:
        yield records
    finally:
        fc.record, fc._keep_late, walk.emit = record, late, emit
