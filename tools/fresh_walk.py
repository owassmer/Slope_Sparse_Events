"""Seed the native subdivision queue using saved history counts for scheduling only."""
from __future__ import annotations

import json
import math
import os
import pickle
import subprocess
import sys
import tempfile
import types
from collections import defaultdict
from pathlib import Path

RUN = 'akoustis_20240514-agent_plus_jev-20260929T052558Z'


def normalized(prefix):
    return tuple((n, c, b.split('=', 1)[-1] if n == 'settle' else b) for n, c, b in prefix)


def allocate(calls, costs):
    weights = defaultdict(int)
    for row in costs['roots']:
        weights[row['method'], normalized(row['prefix'])] += row['histories']
    items, exact = [], 0
    for key, method, prefix in calls:
        weight = weights.get((method, normalized(prefix)))
        exact += weight is not None
        items.append((max(1, costs['fallback'] if weight is None else weight), tuple(key)))
    groups = [{'job': j, 'roots': [], 'estimated_histories': 0, 'largest_root': 0} for j in range(100)]
    for weight, key in sorted(items, reverse=True):
        group = min(groups, key=lambda g: (g['estimated_histories'], g['job']))
        group['roots'].append(key)
        group['estimated_histories'] += weight
        group['largest_root'] = max(group['largest_root'], weight)
    for group in groups:
        group.update(partitions=min(96, max(1, math.ceil(group['estimated_histories'] / 2000))),
                     depth=6 if group['largest_root'] > 25000 else 4,
                     saved=0, recovery=None, fresh=True)
    assigned = [key for group in groups for key in group['roots']]
    expected = [tuple(key) for key, _, _ in calls]
    if len(set(assigned)) != len(assigned) or set(assigned) != set(expected):
        raise ValueError('Balanced queue must own every native root exactly once')
    return groups, exact


def skeleton(out):
    from app.disputes import parallel
    segment = next(c for c in parallel._child.__code__.co_consts
                   if isinstance(c, types.CodeType) and c.co_name == 'segment')
    calls = []

    def profile(frame, event, result):
        if event != 'return':
            return
        if frame.f_code is segment:
            caller = frame.f_back.f_locals
            calls.append((result[0], caller['name'], caller['s'].steps))
        elif frame.f_code is parallel._child.__code__:
            Path(out, 'calls.pkl').write_bytes(pickle.dumps(calls))
    sys.setprofile(profile)
    try:
        parallel.shard(RUN, 0, 1, 1, out, 0)
    finally:
        sys.setprofile(None)


def prepare(bucket, prefix):
    import boto3
    from cloud_walk import create, publish, read

    s3 = boto3.client('s3')
    if read(s3, bucket, prefix + '/ready.json'):
        return
    with tempfile.TemporaryDirectory(prefix='fresh-walk-') as tmp:
        root = Path(tmp)
        roots = root / 'empty.pkl'
        roots.write_bytes(pickle.dumps([]))
        env = {**os.environ, 'SLOPE_WALK_ROOTS': str(roots), 'SLOPE_WALK_CUT': '10',
               'SLOPE_WALK_MINUTES': '0', 'SLOPE_JEV_CACHE_ONLY': '1',
               'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
        for name in ('SLOPE_WALK_REFINE', 'SLOPE_WALK_PREFIXES', 'SLOPE_VARIANT'):
            env.pop(name, None)
        log = root / 'topology.log'
        with log.open('w') as stream:
            subprocess.run(['.venv/bin/python', 'tools/fresh_walk.py', 'skeleton', str(root / 'out')],
                           env=env, stdout=stream, stderr=subprocess.STDOUT, check=True)
        with (root / 'out/part0.pkl').open('rb') as stream:
            topology = pickle.load(stream)
        if not topology['complete'] or topology['done'] or topology.get('subdivisions'):
            raise ValueError('Fresh base must contain only the complete native shared top')
        costs = read(s3, bucket, prefix + '/root-cost-input.json')
        calls = pickle.loads((root / 'out/calls.pkl').read_bytes())
        plans, matched = allocate(calls, costs)
        native = {(clock, 0, number) for number, clock, _ in topology['segs']}
        if {tuple(k) for p in plans for k in p['roots']} != native:
            raise ValueError('Scheduled roots differ from native skeleton')
        subprocess.run(['.venv/bin/python', '-m', 'app.disputes.pool', 'split',
                        str(root / 'out'), str(root / 'split')], env=env, check=True)
        queue = prefix + '/refine-v1'
        publish(s3, bucket, queue + '/base', 0, 'native-top', root / 'split', log)
        base = read(s3, bucket, queue + '/base/done/0.json')
        for plan in plans:
            job = plan['job']
            create(s3, bucket, queue + f'/base/done/{job}.json', {**base, 'job': job})
            create(s3, bucket, queue + f'/plans/{job}.json', plan)
        result = {'jobs': list(range(100)), 'roots': len(topology['segs']),
                  'tasks': sum(p['partitions'] for p in plans), 'matched_costs': matched,
                  'cost_balanced': True, 'plans': plans,
                  'revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}
        create(s3, bucket, prefix + '/config.json', result)
        create(s3, bucket, prefix + '/ready.json', result)
        print(json.dumps({k: v for k, v in result.items() if k != 'plans'}), flush=True)


if __name__ == '__main__':
    if sys.argv[1] == 'skeleton':
        skeleton(sys.argv[2])
    else:
        prepare(*sys.argv[1:])
