"""Bind saved segment reuse and replacement to the corrected walk topology."""
from __future__ import annotations

import gzip
import json
import pickle
import sys
from collections import Counter
from pathlib import Path


def plan(original, corrected, reports, first_wave):
    """Only reuse unique identical roots with no changed continuation in their saved paths."""
    jobs = [r['job'] for r in reports]
    if sorted(jobs) != list(range(100)):
        raise ValueError('complete, exactly-once 100-source scope is required')
    old_keys = {key for key, _ in original}
    if len(old_keys) != len(original) or len({key for key, _ in corrected}) != len(corrected):
        raise ValueError('duplicate segment keys')
    flagged = {tuple(key) for report in reports for key, _ in report['flags']}
    counted = {tuple(key) for report in reports for key, _ in report['counts']}
    if not flagged <= counted <= old_keys:
        raise ValueError('scope references an unknown original segment')
    old_counts = Counter(identity for _, identity in original)
    new_counts = Counter(identity for _, identity in corrected)
    old_by_identity = {identity: key for key, identity in original if old_counts[identity] == 1}
    wave = set(first_wave)
    if not wave <= {key for key, _ in corrected}:
        raise ValueError('first wave references an unknown corrected segment')
    reuse, replace = [], []
    for key, identity in corrected:
        old = old_by_identity.get(identity)
        if old is not None and new_counts[identity] == 1 and old not in flagged and key not in wave:
            reuse.append((old, key))
        else:
            replace.append(key)
    if len(reuse) + len(replace) != len(corrected):
        raise ValueError('incomplete corrected coverage')
    return {'original_segments': len(original), 'corrected_segments': len(corrected),
            'flagged_original': sorted(flagged), 'reuse': reuse, 'replace': replace,
            'first_wave': sorted(wave), 'remaining': sorted(set(replace) - wave),
            'top_events': 'corrected_only', 'production_ready': False}


def main(original, corrected, scope, first_wave, out):
    def read(path):
        with gzip.open(path, 'rb') as stream:
            return pickle.load(stream)
    root = Path(scope)
    complete = json.loads((root / 'complete.json').read_text())
    reports = [json.loads((root / f'{job}.json').read_text()) for job in complete['sources']]
    with open(first_wave, 'rb') as stream:
        wave = pickle.load(stream)
    result = plan(read(original), read(corrected), reports, [key for key, _ in wave])
    Path(out).write_text(json.dumps(result))
    Path(out).with_suffix('.remaining.pkl').write_bytes(pickle.dumps(result['remaining'], protocol=5))
    print({k: len(result[k]) for k in ('flagged_original', 'reuse', 'replace', 'first_wave', 'remaining')})


if __name__ == '__main__':
    main(*sys.argv[1:])
