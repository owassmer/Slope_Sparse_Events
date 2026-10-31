"""Splitting unfinished work preserves exact native-root and child ownership."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from tail_walk import chunks, validate  # noqa: E402


def test_root_split_preserves_original_partition_and_coverage():
    roots = [(i * 10, 0, i) for i in range(31)]
    plan = {'job': 19, 'roots': roots, 'partitions': 4, 'depth': 6}
    pieces = chunks(plan, 2, 14)
    assert len(pieces) == 14
    assert sorted(k for p in pieces for k in p['roots']) == roots
    assert all(p['indexes'] == [2] and p['partitions'] == 4 and p['depth'] == 6 for p in pieces)
    segments = tuple((i, 0, i) for i in range(11))
    parts = [{'complete': True, 'subdivisions': {
        key: {'segments': segments, 'done': segments[2::4], 'partition': 2, 'partitions': 4, 'depth': 6}
        for key in p['roots']}} for p in pieces]
    validate(parts, plan, 2)
    with pytest.raises(ValueError, match='Missing native roots'):
        validate(parts[:-1], plan, 2)
    with pytest.raises(ValueError, match='duplicate native root'):
        validate(parts + parts[:1], plan, 2)
    first = parts[0]['subdivisions'][roots[0]]
    first['done'] = segments[1::4]
    with pytest.raises(ValueError, match='coverage differs'):
        validate(parts, plan, 2)


def test_finer_partition_headers_preserve_events_and_reject_gaps():
    from copy import deepcopy

    from tail_fanout import collapse

    plan = {'roots': [(10, 0, 1)], 'partitions': 4, 'depth': 6}
    root = (10, 0, 1)
    segments = tuple((i, 0, i) for i in range(99))
    parts = [{'k': 400 + j, 'complete': True, 'clock': 50, 'nseg': 1, 'segs': [(1, 10, 1)],
              'events': [('unchanged', j)], 'subdivisions': {root: {
                  'segments': segments, 'done': segments[2 + 4 * j::32],
                  'partition': 2 + 4 * j, 'partitions': 32, 'depth': 6}}} for j in range(8)]
    original = deepcopy(parts)
    collapse(parts, plan, 2, 8)
    assert [p['events'] for p in parts] == [p['events'] for p in original]
    assert parts[0]['subdivisions'][root]['done'] == segments[2::4]
    assert all(not p['subdivisions'] for p in parts[1:])
    with pytest.raises(ValueError, match='Incomplete'):
        collapse(original[:-1], plan, 2, 8)
    bad = deepcopy(original)
    bad[0]['subdivisions'][root]['done'] = ()
    with pytest.raises(ValueError, match='child coverage'):
        collapse(bad, plan, 2, 8)
    bad = deepcopy(original)
    bad[1]['subdivisions'][root]['segments'] = tuple(reversed(segments))
    with pytest.raises(ValueError, match='topology'):
        collapse(bad, plan, 2, 8)
