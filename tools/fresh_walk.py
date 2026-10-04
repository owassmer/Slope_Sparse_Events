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


PATH_S = 0.7  # walking seconds per path (2 October median)
NODE_S = 0.074  # seconds per upper-level branch: the measured cut-10 top over the branches between cut 6 and 10
LONGEST_S = 9000  # a root's largest piece plus its upper levels: such tasks start first and end inside the walk
UPPER_SHARE = 0.15  # a heavy root's tasks are sized so its upper levels are at most this share of a task


def uppers(root, depth) -> float:
    """Seconds a task spends walking the root's upper levels to `depth` before reaching the pieces it owns."""
    return NODE_S * sum(root['branches'].get(str(k), 0) for k in range(1, depth + 1))


def choose(root):
    """(depth, task paths): the shallowest depth whose largest piece and upper levels fit LONGEST_S (else the
    depth with the shortest such task); task size grows with the upper levels so they stay UPPER_SHARE of a task."""
    if root['paths'] <= TARGET or not root['largest']:
        return DEPTHS[0], TARGET

    def longest(d):
        return root['largest'].get(str(d), 0) * PATH_S + uppers(root, d)
    fits = [d for d in DEPTHS if longest(d) <= LONGEST_S]
    depth = fits[0] if fits else min(DEPTHS, key=longest)
    return depth, max(TARGET, math.ceil(uppers(root, depth) / (UPPER_SHARE * PATH_S)))


def allocate(calls, measured):
    """Ownership groups: a root whose upper levels cost a minute or more per task gets a group of its own (its
    tasks re-walk only its own upper levels); the rest are packed by depth into the remaining groups."""
    roots = []
    for r in measured['roots']:
        depth, size = choose(r)
        share = max(1, r['paths'] // max(1, len(r['keys'])))
        piece = r['largest'].get(str(depth), share) if r['largest'] else share
        roots += [{'key': tuple(k), 'paths': share, 'depth': depth, 'size': size, 'upper': uppers(r, depth),
                   'piece': min(piece, share)} for k in r['keys']]
    heavy = sorted((r for r in roots if r['upper'] >= 60), key=lambda r: -r['upper'])[:GROUPS - 10]
    rest = [r for r in roots if r['upper'] < 60 or r not in heavy]
    groups = [{'job': j, 'members': [r]} for j, r in enumerate(heavy)]
    by_depth = defaultdict(list)
    for r in rest:
        by_depth[r['depth']].append(r)
    free, total = GROUPS - len(groups), sum(r['paths'] for r in rest)
    weight = {d: sum(r['paths'] for r in xs) for d, xs in by_depth.items()}
    quota = {d: min(len(by_depth[d]), max(1, int(free * weight[d] / total))) for d in by_depth}
    while sum(quota.values()) > free:
        quota[max(quota, key=lambda d: quota[d])] -= 1
    while sum(quota.values()) < free:
        spare = [d for d in quota if quota[d] < len(by_depth[d])]
        quota[max(spare, key=lambda d: weight[d] / quota[d])] += 1
    for d in sorted(by_depth):
        own = [{'job': len(groups) + j, 'members': [], 'load': 0} for j in range(quota[d])]
        for r in sorted(by_depth[d], key=lambda r: -r['paths']):
            g = min(own, key=lambda g: (g['load'], g['job']))
            g['members'].append(r)
            g['load'] += r['paths']
        groups += own
    plans = []
    for g in groups:
        m = g['members']
        paths = sum(r['paths'] for r in m)
        size = max(r['size'] for r in m)
        partitions = max(1, math.ceil(paths / size))
        if partitions > MAX_PARTITIONS:
            raise ValueError(f"group {g['job']} needs {partitions} partitions")
        task = max(paths / partitions, max(r['piece'] for r in m)) * PATH_S + sum(r['upper'] for r in m)
        plans.append({'job': g['job'], 'roots': [r['key'] for r in m], 'estimated_histories': paths,
                      'largest_root': max(r['paths'] for r in m), 'depth': m[0]['depth'],
                      'partitions': partitions, 'indexes': list(range(partitions)), 'task_seconds': round(task),
                      'upper_seconds': round(sum(r['upper'] for r in m)), 'cut': CUT, 'saved': 0,
                      'recovery': None, 'fresh': True})
    assigned = [key for p in plans for key in p['roots']]
    expected = [tuple(key) for key, _, _ in calls]
    if len(set(assigned)) != len(assigned) or set(assigned) != set(expected) or len(plans) != GROUPS:
        raise ValueError('Queue must own every native root exactly once in GROUPS groups')
    if any(len({r['depth'] for r in g['members']}) != 1 for g in groups):
        raise ValueError('A group mixes refinement depths')
    return plans


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
        tasks = sorted((p['task_seconds'] for p in plans for _ in p['indexes']), reverse=True)
        print(json.dumps({'tasks': len(tasks), 'paths': sum(p['estimated_histories'] for p in plans),
                          'depths': dict(Counter(p['depth'] for p in plans)),
                          'task_seconds_max_p50_min': [round(tasks[0]), round(tasks[len(tasks) // 2]), round(tasks[-1])],
                          'partitions_max': max(p['partitions'] for p in plans),
                          'process_hours': round(sum(tasks) / 3600),
                          'hours_on_336': round(sum(tasks) / 3600 / 336, 2)}))
    else:
        prepare(*sys.argv[2:6])
