"""Bind exact repair branches to saved descendants, preserving unaffected siblings."""
from __future__ import annotations

import gzip
import json
import pickle
from collections import Counter
from pathlib import Path


def records(path):
    with gzip.open(path, 'rb') as stream:
        while True:
            try:
                yield pickle.load(stream)
            except EOFError:
                return


def build(root, output):
    root, output = Path(root), Path(output)
    reports = [json.loads(p.read_text()) for p in root.glob('*/complete.json')]
    if sorted(r['worker'] for r in reports) != list(range(40)) or not all(r['complete'] for r in reports):
        raise ValueError('All 40 complete scan reports are required')
    inventory = [tuple(p) for r in reports for p in r['parts']]
    if len(set(inventory)) != len(inventory) or sorted({p[0] for p in inventory}) != list(range(100)):
        raise ValueError('Incomplete or duplicate original source assignments')
    events, sites, counts = {}, {}, Counter()
    for path in root.glob('*/sites-*.pkl.gz'):
        with gzip.open(path, 'rb') as stream:
            result = pickle.load(stream)
        counts.update(result['counts'])
        for site, row in result['sites'].items():
            sites.setdefault(site, set()).update(row['reasons'])
            for event in row['events']:
                events.setdefault(event, set()).add(site)
    indices = sorted(root.glob('*/index-*.pkl.gz'))
    if len(indices) != len(inventory) or len(list(root.glob('*/sites-*.pkl.gz'))) != len(inventory):
        raise ValueError('Missing scan results or descendant indices')
    units, located = {}, set()
    for path in indices:
        for event, instance, steps, _condition in records(path):
            for site in events.get(event, ()):
                owner, before, _method, phase = site
                if owner != instance or steps[:len(before)] != before:
                    raise ValueError('Site does not belong to indexed history')
                origin = steps[len(before)]
                prefix = steps[:len(before) + 1]
                key = (instance, prefix)
                if origin[0] == 'judgment_response':
                    method, target_phase = 'notes_petition', 'I1'
                    targets = ((prefix, method, target_phase),)
                else:
                    method = {'I1': 'ruling', 'ruling': 'post', 'post': 'tail'}[phase]
                    targets = ((prefix, method, None),)
                    if origin[2] == 'accelerated':
                        if 'digest_merge' not in sites[site]:
                            raise ValueError('Quiet alternative is not a saved merged holder branch')
                        filed = before + ((origin[0], origin[1], 'holders_file'),)
                        targets += ((filed, method, None),)
                row = units.setdefault(key, {'targets': targets, 'reasons': set()})
                if row['targets'] != targets:
                    raise ValueError('Ambiguous continuation targets')
                row['reasons'].update(sites[site])
                located.add(event)
    if located != set(events):
        raise ValueError('Some candidate events were not found in saved indices')

    # An ancestor repair already walks all of its descendant continuations.
    trie, selected = {}, []
    for key, row in sorted(units.items(), key=lambda item: (len(item[0][1]), item[0])):
        instance, prefix = key
        node = trie.setdefault(instance, {})
        covered = False
        for step in prefix:
            if None in node:
                covered = True
                break
            node = node.setdefault(step, {})
        if covered or None in node:
            continue
        node[None] = len(selected)
        selected.append({'instance': instance, 'prefix': prefix, **row, 'old_events': []})

    raw = removed = 0
    parts = {}
    conditions = {}
    for path in indices:
        mine = []
        for event, instance, steps, _condition in records(path):
            raw += 1
            node = trie.get(instance, {})
            for step in steps:
                if None in node:
                    break
                node = node.get(step, {})
                if not node:
                    break
            if None in node:
                unit = node[None]
                selected[unit]['old_events'].append(event)
                mine.append((event, unit))
                conditions[event] = _condition
                removed += 1
        parts[path.name] = mine
    if raw != counts['raw_paths'] or any(not u['old_events'] for u in selected):
        raise ValueError('Descendant coverage differs from the complete scan')
    result = {'sources': 100, 'parts': len(inventory), 'raw_paths': raw, 'replaced_raw_paths': removed,
              'retained_raw_paths': raw - removed, 'candidate_sites': len(sites), 'units': selected,
              'removed_by_part': parts, 'event_conditions': conditions, 'reading': 'entered', 'production_ready': False}
    output.write_bytes(pickle.dumps(result, protocol=5))
    summary = {k: v for k, v in result.items() if k not in ('units', 'removed_by_part', 'event_conditions')}
    summary.update(units=len(selected), continuations=sum(len(u['targets']) for u in selected))
    output.with_suffix('.json').write_text(json.dumps(summary, indent=2))
    print(summary, flush=True)
    return result


if __name__ == '__main__':
    import sys
    build(*sys.argv[1:])
