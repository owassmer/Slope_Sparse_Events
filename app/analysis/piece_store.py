"""Population-piece handoff (trusted local pickle files, not external input).

The gzip stream contains supported financial rows, native paths/edges and
question blobs interned by (question key, blob). Different situations at the
same key must not be collapsed. References retain their prefix/history links.
No unsupported financial row is reconstructed as zero. Consumers use `rows`
to select their operating draws/line, and the native path for probabilities.
`read_events` also reads the original uncompressed events.pkl format.
"""
from __future__ import annotations

import gzip
import pickle
from pathlib import Path

import numpy as np


def financial_rows(tr, mask, n):
    """Only the final arrays read by pooling/reduction, in native draw order."""
    from app.analysis.events import Trace, _cut_trace

    rows = np.arange(n) if mask is None else np.flatnonzero(mask)
    # Events already live on tr.rows; decision metadata was widened by the walk.
    if tr.rows is not None and not np.array_equal(tr.rows, rows):
        raise ValueError('terminal financial support differs from path support')
    metadata = _cut_trace(Trace(tr.events, cause=tr.cause, marks=tr.marks), rows, n)
    events = metadata.events if tr.rows is None else tr.events
    # Strip LiveEventCash's owner/cache: only the seven contractual arrays travel.
    from app.analysis.events import EventCash

    events = EventCash(*(getattr(events, k) for k in EventCash.__dataclass_fields__))
    return dict(rows=rows, events=events, cause=metadata.cause, marks=metadata.marks)


class PieceWriter:
    def __init__(self, directory: Path, n: int, *, full=False):
        self.n = n
        self.stream = gzip.open(directory / 'events.pkl.gz', 'xb', compresslevel=1)
        self.full = (directory / 'events.pkl').open('xb') if full else None
        self.questions = {}
        self.put_raw('format', ('population-piece', 1, n))

    def put_raw(self, kind, payload):
        pickle.dump((kind, payload), self.stream, protocol=pickle.HIGHEST_PROTOCOL)

    def question(self, key, blob):
        records = self.questions.setdefault(key, {})
        if blob not in records:
            records[blob] = len(records)
            self.put_raw('question_record', (key, records[blob], blob))
        return key, records[blob]

    def put(self, kind, payload):
        if self.full is not None and kind != 'financial':
            pickle.dump((kind, payload), self.full, protocol=pickle.HIGHEST_PROTOCOL)
        if kind == 'questions':
            prefix, records = payload
            self.put_raw('question_refs', (prefix, [self.question(k, b) for k, b in records]))
        elif kind == 'dated_question':
            key, prefix, history, blob = payload
            self.put_raw('dated_ref', (self.question(key, blob), prefix, history))
        else:
            self.put_raw(kind, payload)

    def close(self):
        self.stream.close()
        if self.full is not None:
            self.full.close()


def read_events(path: Path):
    """Native logical records from either format; financial records stay row-scoped.

    Native path class codes are already scoped to support and are unchanged.
    The catalog itself can consume question_record entries with `stored_events`.
    """
    questions = {}
    n = None
    for kind, payload in stored_events(path):
        if kind == 'format':
            name, version, n = payload
            if (name, version) != ('population-piece', 1):
                raise ValueError('unsupported population piece format')
        elif kind == 'question_record':
            key, index, blob = payload
            questions[key, index] = blob
        elif kind == 'question_refs':
            prefix, refs = payload
            yield 'questions', (prefix, [(k, questions[k, i]) for k, i in refs])
        elif kind == 'dated_ref':
            ref, prefix, history = payload
            yield 'dated_question', (ref[0], prefix, history, questions[ref])
        else:
            yield kind, payload


def stored_events(path: Path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rb') as stream:
        while True:
            try:
                item = pickle.load(stream)
            except EOFError:
                return
            yield item
