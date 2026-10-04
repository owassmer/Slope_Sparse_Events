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
CUT = 6  # roots six steps below the verdict: the shared top every task walks takes seconds, not minutes
TARGET = 4000  # paths per task: about 45 minutes of walking at the measured 0.7 s a path
DEPTHS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14)  # refinement depths below a root
MAX_PARTITIONS = 9999  # part numbers are 1000000 + 10000 * group + partition


def canon(step):
    """A decision as both trees record it: group codes and settlement-offer labels dropped; a denied stay motion
    compared with the earlier tree's single 'no'."""
    node, ctx, branch = step
    if branch.startswith('@') or node == 'settle':
        branch = branch.split('=', 1)[-1]
    if node == 'stay' and branch == 'denied':
        branch = 'no'
    return node, ctx, branch


def key(steps) -> int:
    return int.from_bytes(hashlib.blake2b(repr(tuple(steps)).encode(), digest_size=8).digest(), 'little')


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
    """For one saved group: the paths whose decisions include each root's (shared equally where several roots
    match), and the size of each continuation below a root at each depth, in the saved walk's order."""
    archive, roots = job
    sets = [frozenset(r) for r in roots]
    index = defaultdict(list)
    for i, r in enumerate(roots):
        index[r[-1]].append(i)
    paths, children, unmatched = Counter(), Counter(), 0
    with tempfile.TemporaryDirectory(prefix='cost-scan-') as tmp:
        with tarfile.open(archive) as tar:
            tar.extractall(tmp, filter='data')
        for file in sorted(Path(tmp).rglob('part*.pkl')):
            part = pickle.loads(file.read_bytes())
            for _key, kind, payload, _cond in part['events']:
                if kind != 'path':
                    continue
                steps = [canon(x) for x in payload[0].steps]
                have = set(steps)
                found = {i for x in have for i in index.get(x, ()) if sets[i] <= have}
                if not found:
                    unmatched += 1
                    continue
                w = 1 / len(found)
                for i in found:
                    paths[i] += w
                    rest = [x for x in steps if x not in sets[i]]
                    for d in DEPTHS:
                        children[i, d, key(rest[:d])] += w
            del part
    return paths, children, unmatched


def costs(calls_file, archives_dir, out):
    calls = pickle.loads(Path(calls_file).read_bytes())
    roots = sorted({tuple(canon(x) for x in prefix) for _key, _method, prefix in calls})
    archives = sorted(str(p) for p in Path(archives_dir).rglob('*.tgz'))
    paths, children, unmatched = Counter(), Counter(), 0
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as ex:
        for i, (p, c, u) in enumerate(ex.map(_scan_one, [(a, roots) for a in archives])):
            paths.update(p)
            children.update(c)
            unmatched += u
            print(f'scanned {i + 1}/{len(archives)} archives', flush=True)
    largest, count = defaultdict(dict), defaultdict(Counter)
    for (i, d, _child), n in children.items():
        largest[i][d] = max(largest[i].get(d, 0), n)
        count[i][d] += 1
    # A root no saved path reached (a decision the earlier tree never offered) is costed at the median root.
    measured = sorted(paths.values())
    fallback = measured[len(measured) // 2] if measured else 1
    position = {r: i for i, r in enumerate(roots)}
    by_root = defaultdict(list)
    for k, _method, prefix in calls:
        by_root[position[tuple(canon(x) for x in prefix)]].append(tuple(k))
    result = {'roots': [{'keys': by_root[i], 'paths': round(paths.get(i, fallback)), 'measured': i in paths,
                         'largest': {str(d): round(n) for d, n in largest.get(i, {}).items()},
                         'branches': {str(d): n for d, n in count.get(i, {}).items()}}
                        for i in range(len(roots))],
              'saved_paths': round(sum(paths.values())) + unmatched, 'unmatched_saved_paths': unmatched,
              'measured_roots': len(paths), 'native_prefixes': len(roots), 'native_roots': len(calls)}
    Path(out).write_text(json.dumps(result))
    print(json.dumps({k: v for k, v in result.items() if k != 'roots'}), flush=True)


def depth_for(root) -> int:
    """The shallowest refinement depth whose largest continuation fits one task (unmeasured: the base depth)."""
    if root['paths'] <= TARGET or not root['largest']:
        return DEPTHS[0]
    # The earlier walk's order sets these sizes; a margin covers continuations the corrected order regroups.
    return next((d for d in DEPTHS if root['largest'].get(str(d), 0) <= 0.75 * TARGET), DEPTHS[-1])


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
        group.update(partitions=partitions, indexes=list(range(partitions)), cut=CUT, saved=0, recovery=None,
                     fresh=True)
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
