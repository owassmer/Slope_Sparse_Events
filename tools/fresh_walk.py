"""Seed the native subdivision queue, splitting each root by its measured cost.

`skeleton` walks the shared top once and lists every native root (key, continuation, prefix). `costs` reads a
completed walk's saved paths and measures, for each native root prefix, the paths below it and the largest
continuation at each refinement depth. `prepare` packs the roots into the 100 ownership groups and gives each group
the partition count and depth that make every task about TARGET paths. Costs only schedule work: coverage is checked
against the native skeleton, and an unmeasured root still gets walked.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import pickle
import subprocess
import sys
import tarfile
import tempfile
import types
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

RUN = 'akoustis_20240514-agent_plus_jev-20260929T052558Z'
GROUPS = 100
TARGET = 5000  # paths per task: about an hour of walking at the measured 0.7 s a path
DEPTHS = (4, 6, 8, 10, 12)
MAX_PARTITIONS = 99  # part numbers are 100000 + 100 * group + partition


def normalized(prefix):
    return tuple((n, c, b.split('=', 1)[-1] if n == 'settle' else b) for n, c, b in prefix)


def chain(steps) -> list[int]:
    """The digest of every prefix of `steps` (index n - 1: the first n steps), one hash per step."""
    out, h = [], b''
    for step in steps:
        h = hashlib.blake2b(h + repr(step).encode(), digest_size=8).digest()
        out.append(int.from_bytes(h, 'little'))
    return out


def digest(steps) -> int:
    return chain(steps)[-1] if steps else 0


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


def _scan_one(job):
    """Path counts below each root prefix, per-depth continuation sizes and ancestor counts in one saved group."""
    archive, prefixes, maxlen = job
    index = {p: i for i, p in enumerate(prefixes)}
    lengths = sorted({len(p) for p in prefixes}, reverse=True)
    roots, children, ancestors, unmatched = Counter(), Counter(), Counter(), 0
    with tempfile.TemporaryDirectory(prefix='cost-scan-') as tmp:
        with tarfile.open(archive) as tar:
            tar.extractall(tmp, filter='data')
        for file in sorted(Path(tmp).rglob('part*.pkl')):
            part = pickle.loads(file.read_bytes())
            for _key, kind, payload, _cond in part['events']:
                if kind != 'path':
                    continue
                steps = normalized(payload[0].steps)
                hashes = chain(steps)
                for h in hashes[:maxlen]:
                    ancestors[h] += 1
                root = next((index[steps[:n]] for n in lengths if steps[:n] in index), None)
                if root is None:
                    unmatched += 1
                    continue
                roots[root] += 1
                base = len(prefixes[root])
                for d in DEPTHS:
                    children[root, d, hashes[min(base + d, len(hashes)) - 1]] += 1
            del part
    return roots, children, ancestors, unmatched


def costs(calls_file, archives_dir, out):
    calls = pickle.loads(Path(calls_file).read_bytes())
    prefixes = sorted({normalized(prefix) for _key, _method, prefix in calls})
    maxlen = max(map(len, prefixes))
    archives = sorted(str(p) for p in Path(archives_dir).rglob('*.tgz'))
    roots, children, ancestors, unmatched = Counter(), Counter(), Counter(), 0
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as ex:
        for i, (r, c, a, u) in enumerate(ex.map(_scan_one, [(a, prefixes, maxlen) for a in archives])):
            roots.update(r)
            children.update(c)
            ancestors.update(a)
            unmatched += u
            print(f'scanned {i + 1}/{len(archives)} archives', flush=True)
    largest = defaultdict(dict)
    for (root, d, _child), n in children.items():
        largest[root][d] = max(largest[root].get(d, 0), n)
    # A root the saved walk never reached shares its nearest measured ancestor's unclaimed paths.
    matched_under = Counter()
    for root, n in roots.items():
        for k in range(1, len(prefixes[root])):
            matched_under[digest(prefixes[root][:k])] += n
    waiting = defaultdict(list)
    for i, p in enumerate(prefixes):
        if i not in roots:
            k = next((k for k in range(len(p) - 1, 0, -1) if ancestors.get(digest(p[:k]))), 0)
            waiting[k and digest(p[:k])].append(i)
    estimate = {}
    for anc, members in waiting.items():
        spare = max(len(members), ancestors.get(anc, 0) - matched_under.get(anc, 0)) if anc else len(members)
        for i in members:
            estimate[i] = max(1, spare // len(members))
    per_prefix = {i: {'paths': roots.get(i, estimate.get(i, 1)), 'measured': i in roots,
                      'largest': {str(d): n for d, n in largest.get(i, {}).items()}} for i in range(len(prefixes))}
    position = {p: i for i, p in enumerate(prefixes)}
    by_prefix = defaultdict(list)
    for key, _method, prefix in calls:
        by_prefix[position[normalized(prefix)]].append(tuple(key))
    result = {'roots': [{'keys': by_prefix[i], **per_prefix[i]} for i in range(len(prefixes))],
              'saved_paths': sum(roots.values()) + unmatched, 'unmatched_saved_paths': unmatched,
              'measured_roots': len(roots), 'native_prefixes': len(prefixes), 'native_roots': len(calls)}
    Path(out).write_text(json.dumps(result))
    print(json.dumps({k: v for k, v in result.items() if k != 'roots'}), flush=True)


def depth_for(root) -> int:
    """The shallowest refinement depth whose largest continuation fits one task (unmeasured: the base depth)."""
    if root['paths'] <= TARGET or not root['largest']:
        return DEPTHS[0]
    return next((d for d in DEPTHS if root['largest'].get(str(d), 0) <= TARGET), DEPTHS[-1])


def allocate(calls, measured):
    """Pack native roots into GROUPS ownership groups of one depth each; size each group's partitions to TARGET."""
    items = []
    for root in measured['roots']:
        share = max(1, root['paths'] // max(1, len(root['keys'])))
        items += [(share, tuple(key), depth_for(root)) for key in root['keys']]
    total = sum(w for w, _, _ in items)
    by_depth = defaultdict(list)
    for item in items:
        by_depth[item[2]].append(item)
    # Groups per depth in proportion to its paths, at least one each; largest remainders take the rest.
    weight = {d: sum(w for w, _, _ in xs) for d, xs in by_depth.items()}
    quota = {d: max(1, int(GROUPS * weight[d] / total)) for d in by_depth}
    for d in sorted(by_depth, key=lambda d: GROUPS * weight[d] / total - quota[d], reverse=True):
        if sum(quota.values()) >= GROUPS:
            break
        quota[d] += 1
    while sum(quota.values()) > GROUPS:
        quota[max(quota, key=lambda d: quota[d])] -= 1
    # A group with no root would only hold an empty task: hand surplus groups to depths with roots to spare.
    for d in quota:
        quota[d] = min(quota[d], len(by_depth[d]))
    while sum(quota.values()) < GROUPS:
        spare = [d for d in quota if quota[d] < len(by_depth[d])]
        quota[max(spare, key=lambda d: weight[d] / quota[d])] += 1
    groups, job = [], 0
    for d in sorted(by_depth):
        own = [{'job': job + j, 'roots': [], 'estimated_histories': 0, 'largest_root': 0, 'depth': d}
               for j in range(quota[d])]
        job += quota[d]
        for w, key, _ in sorted(by_depth[d], reverse=True):
            group = min(own, key=lambda g: (g['estimated_histories'], g['job']))
            group['roots'].append(key)
            group['estimated_histories'] += w
            group['largest_root'] = max(group['largest_root'], w)
        groups += own
    for group in groups:
        partitions = max(1, math.ceil(group['estimated_histories'] / TARGET))
        if partitions > MAX_PARTITIONS:
            raise ValueError(f"group {group['job']} needs {partitions} partitions; raise GROUPS")
        group.update(partitions=partitions, indexes=list(range(partitions)), saved=0, recovery=None, fresh=True)
    assigned = [key for group in groups for key in group['roots']]
    expected = [tuple(key) for key, _, _ in calls]
    if len(set(assigned)) != len(assigned) or set(assigned) != set(expected) or len(groups) != GROUPS:
        raise ValueError('Queue must own every native root exactly once in GROUPS groups')
    return groups


def prepare(bucket, prefix, skeleton_dir, costs_file):
    import boto3
    from cloud_walk import create, publish, read

    s3 = boto3.client('s3')
    if read(s3, bucket, prefix + '/ready.json'):
        return
    root = Path(skeleton_dir)
    with (root / 'out/part0.pkl').open('rb') as stream:
        topology = pickle.load(stream)
    if not topology['complete'] or topology['done'] or topology.get('subdivisions'):
        raise ValueError('Fresh base must contain only the complete native shared top')
    calls = pickle.loads((root / 'out/calls.pkl').read_bytes())
    measured = json.loads(Path(costs_file).read_text())
    plans = allocate(calls, measured)
    native = {(clock, 0, number) for number, clock, _ in topology['segs']}
    if {tuple(k) for p in plans for k in p['roots']} != native:
        raise ValueError('Scheduled roots differ from native skeleton')
    env = {**os.environ, 'SLOPE_JEV_CACHE_ONLY': '1', 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
    subprocess.run(['.venv/bin/python', '-m', 'app.disputes.pool', 'split',
                    str(root / 'out'), str(root / 'split')], env=env, check=True)
    queue = prefix + '/refine-v1'
    publish(s3, bucket, queue + '/base', 0, 'native-top', root / 'split', root / 'topology.log')
    base = read(s3, bucket, queue + '/base/done/0.json')
    for plan in plans:
        job = plan['job']
        create(s3, bucket, queue + f'/base/done/{job}.json', {**base, 'job': job})
        create(s3, bucket, queue + f'/plans/{job}.json', plan)
    result = {'jobs': list(range(GROUPS)), 'roots': len(topology['segs']),
              'tasks': sum(p['partitions'] for p in plans), 'target_paths_per_task': TARGET,
              'measured_roots': measured['measured_roots'], 'estimated_paths': sum(p['estimated_histories'] for p in plans),
              'depths': dict(Counter(p['depth'] for p in plans)), 'cost_balanced': True, 'plans': plans,
              'revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}
    create(s3, bucket, prefix + '/config.json', result)
    create(s3, bucket, prefix + '/ready.json', result)
    print(json.dumps({k: v for k, v in result.items() if k != 'plans'}), flush=True)


if __name__ == '__main__':
    action = sys.argv[1]
    if action == 'skeleton':
        skeleton(sys.argv[2])
    elif action == 'costs':
        costs(*sys.argv[2:5])
    elif action == 'plan':
        calls = pickle.loads(Path(sys.argv[2]).read_bytes())
        plans = allocate(calls, json.loads(Path(sys.argv[3]).read_text()))
        tasks = sorted((p['estimated_histories'] / p['partitions'] for p in plans for _ in p['indexes']), reverse=True)
        print(json.dumps({'tasks': len(tasks), 'paths': sum(p['estimated_histories'] for p in plans),
                          'depths': dict(Counter(p['depth'] for p in plans)),
                          'task_paths_max_p50_min': [round(tasks[0]), round(tasks[len(tasks) // 2]), round(tasks[-1])],
                          'partitions_max': max(p['partitions'] for p in plans)}))
    else:
        prepare(*sys.argv[2:6])
