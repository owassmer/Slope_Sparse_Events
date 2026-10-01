"""Find saved segments requiring continuation replacement after the notes correction."""
from __future__ import annotations

import json
import multiprocessing
import pickle
import sys
import tarfile
import time
import urllib.request
from collections import Counter
from functools import lru_cache
from pathlib import Path

from app.disputes.forecast import _conjunctions


@lru_cache(maxsize=32768)
def merged_holders(edge):
    if not edge.startswith('=') or ':holders_involuntary|judgment_' not in edge:
        return frozenset()
    return frozenset(k.split('|')[1] for conjunction in _conjunctions(edge) for k, branch in conjunction
                     if branch == 'yes' and ':holders_involuntary|judgment_' in k)


def reason(path):
    for node, context, branch in path.steps:
        if node == 'judgment_default' and branch in ('yes', 'holders_file'):
            return 'notes_filing'
        if node == 'judgment_response' and context == 'ripe' and branch.split('=')[-1] == 'file':
            return 'ripe_filing'
        if node == 'delisting_notes' and branch in ('petition_delist', 'petition_delist_holders'):
            return 'delisting_filing'
    quiet = {f'judgment_{context}' for node, context, branch in path.steps
             if node == 'judgment_default' and branch == 'accelerated'}
    if quiet and any(quiet & merged_holders(edge) for edge, _ in path.edges):
        return 'merged_filing'
    return None


def scan(source):
    for attempt in range(3):
        try:
            return _scan(source)
        except (OSError, EOFError, tarfile.TarError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def _scan(source):
    flags, counts = {}, Counter()
    with urllib.request.urlopen(source['url'], timeout=180) as response:
        with tarfile.open(fileobj=response, mode='r|gz') as archive:
            for member in archive:
                if not member.isfile() or not member.name.endswith('.pkl'):
                    continue
                part = pickle.load(archive.extractfile(member))
                for key, kind, value, _cond in part['events']:
                    if key[1] != 0 or kind != 'path':
                        continue
                    root = key[:3]
                    counts[root] += 1
                    if root not in flags and (why := reason(value[0])):
                        flags[root] = why
                del part
    return {'job': source['job'], 'flags': list(flags.items()), 'counts': list(counts.items())}


def main(manifest, output, cores):
    sources = [s for task in json.loads(Path(manifest).read_text())['tasks'] for s in task['sources']]
    if len({s['job'] for s in sources}) != len(sources):
        raise ValueError('duplicate source jobs')
    dest = Path(output)
    dest.mkdir(parents=True, exist_ok=True)
    completed = {int(p.stem) for p in dest.glob('[0-9]*.json')}
    with multiprocessing.get_context('spawn').Pool(cores) as workers:
        for result in workers.imap_unordered(scan, [s for s in sources if s['job'] not in completed]):
            path = dest / f"{result['job']}.json"
            path.with_suffix('.tmp').write_text(json.dumps(result))
            path.with_suffix('.tmp').replace(path)
            completed.add(result['job'])
            print({'sources': len(completed), 'total': len(sources), 'job': result['job'],
                   'paths': sum(n for _, n in result['counts']), 'flagged': len(result['flags'])}, flush=True)
    if completed != {s['job'] for s in sources}:
        raise ValueError('source coverage mismatch')
    (dest / 'complete.json').write_text(json.dumps({'sources': sorted(completed)}))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]))
