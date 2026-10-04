"""Restore dated, unclassed post-trial facts omitted by the class-only refresh."""
from __future__ import annotations

import concurrent.futures
import gzip
import json
import pickle
from pathlib import Path

from app.disputes.forecast import atoms

PROBES = {'post_trial_motions': ('post_trial_motions', '', 'no'),
          'post_trial_ruling': ('post_trial_ruling', '', 'unchanged')}
_NODES = {}


def requests(paths, nodes, draws=512):
    """Union retained populations at the same before-answer prefix across descendants."""
    from app.analysis.events import plain

    found = {}
    for path in paths:
        keys = set()
        for edge, _answer in path.edges:
            if 'post_trial_' in edge:
                keys.update(atoms(edge) & nodes.keys())
        mask = (1 << draws) - 1 if path.mask is None else int.from_bytes(path.mask, 'big')
        for key in keys:
            probe = PROBES[nodes[key].node]
            positions = [i for i, step in enumerate(path.steps) if step[:2] == probe[:2]]
            if len(positions) != 1:
                raise ValueError(f'Unambiguous decision boundary required: {key}')
            prefix = tuple((n, c, plain(a)) for n, c, a in path.steps[:positions[0]]) + (probe,)
            identity = key, prefix
            found[identity] = found.get(identity, 0) | mask
    return found


def _scan(file):
    with open(file, 'rb') as fh:
        paths = pickle.load(fh)
    return len(paths), requests(paths, _NODES)


def plan(control, walked, keys, output, cores=8):
    global _NODES
    ctl = pickle.loads(Path(control).read_bytes())
    _NODES = {k: ctl['nodes'][k] for k in keys}
    if any(k in ctl['classed'] or n.node not in PROBES for k, n in _NODES.items()):
        raise ValueError('Only omitted unclassed post-trial questions are supported')
    files = sorted(Path(walked).glob('part*.pkl'))
    combined, histories = {}, 0
    with concurrent.futures.ProcessPoolExecutor(max_workers=cores) as workers:
        for i, (count, rows) in enumerate(workers.map(_scan, files), 1):
            histories += count
            for identity, mask in rows.items():
                combined[identity] = combined.get(identity, 0) | mask
            if i % 100 == 0:
                print({'files': i, 'total': len(files), 'histories': histories,
                       'distinct_prefixes': len(combined)}, flush=True)
    if histories != ctl['walked'] or {k for k, _ in combined} != set(keys):
        raise ValueError('Retained history or omitted question coverage differs')
    with gzip.open(output, 'wb', compresslevel=1) as fh:
        pickle.dump(combined, fh, protocol=5)
    print({'planned': len(combined), 'histories': histories,
           'questions': len(keys)}, flush=True)


def calculate(run, control, planned, output):
    """Calculate each distinct conditioning prefix once; preserve raw question keys."""
    import numpy as np

    from app.analysis.events import BIG, event_trace
    from app.disputes import pool
    from app.disputes.forecast import DisputePath, _Prefix, as_of, pack_row

    ctl = pickle.loads(Path(control).read_bytes())
    fc = pool.forecaster(run, ctl)
    with gzip.open(planned, 'rb') as fh:
        work = pickle.load(fh)
    disputes = {d.instance_id: d for d in fc.disputes}
    fc.draws.prefixes = {}
    with gzip.open(output, 'wb', compresslevel=1) as fh:
        for i, ((key, steps), mask) in enumerate(sorted(work.items()), 1):
            node = fc.nodes[key]
            if fc._classified(key):
                raise ValueError('Unclassed repair would change question identity')
            trace = event_trace(disputes[node.instance_id],
                                DisputePath(instance_id=node.instance_id, steps=steps,
                                            outcome='', edges=()),
                                fc.setup, fc.m, fc.draws, fc.sens, day_only=True)
            row = as_of(fc.row_of(_Prefix.of(trace, digest=False)))
            on = np.unpackbits(np.frombuffer(mask.to_bytes((fc.draws.n + 7)//8, 'big'), np.uint8),
                               count=fc.draws.n).astype(bool)
            row = {**row, 'day': np.where(on, row['day'], BIG)}
            pickle.dump((key, steps, pack_row(row)), fh, protocol=5)
            if i % 100 == 0:
                print({'calculated': i, 'total': len(work)}, flush=True)
    print({'calculated': len(work), 'output': str(output)}, flush=True)


if __name__ == '__main__':
    import sys
    mode, *args = sys.argv[1:]
    if mode == 'plan':
        plan(args[0], args[1], json.loads(Path(args[2]).read_text()), args[3])
    elif mode == 'calculate':
        calculate(*args)
    else:
        raise ValueError(mode)
