"""Describe saved and corrected segment roots without enumerating their subtrees."""
from __future__ import annotations

import dataclasses
import gzip
import hashlib
import json
import os
import pickle
import subprocess
import sys
import types
from pathlib import Path

import numpy as np

from app.disputes import forecast, parallel
from tools.pool_fleet import fetch, put


def identity(value):
    if isinstance(value, forecast._Walk):
        return ('walk', value.d.instance_id if value.d is not None else None)
    if dataclasses.is_dataclass(value):
        return (type(value).__name__, tuple((f.name, identity(getattr(value, f.name)))
                                           for f in dataclasses.fields(value)))
    if isinstance(value, types.MethodType):
        return ('method', value.__func__.__qualname__, identity(value.__self__))
    if isinstance(value, types.FunctionType):
        return ('function', value.__qualname__, tuple(identity(c.cell_contents) for c in value.__closure__ or ()))
    if isinstance(value, np.ndarray):
        return ('array', value.dtype.str, value.shape, hashlib.sha256(value.tobytes()).hexdigest())
    if isinstance(value, dict):
        return tuple(sorted((identity(k), identity(v)) for k, v in value.items()))
    if isinstance(value, (tuple, list)):
        return tuple(identity(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(identity(v) for v in value))
    if value is None or isinstance(value, (str, int, bool, float, bytes)):
        return value
    raise TypeError(f'Unbound segment argument: {type(value)}')


def main(which):
    root = Path('var/notes-topology')
    root.mkdir(parents=True, exist_ok=True)
    fetch(os.environ['NOTES_TOPOLOGY_MANIFEST_URL'], root / 'manifest.json')
    manifest = json.loads((root / 'manifest.json').read_text())
    if which == 'original':
        source = subprocess.check_output(['git', 'show', manifest['original_revision'] + ':app/disputes/forecast.py'])
        exec(compile(source, forecast.__file__, 'exec'), forecast.__dict__)
    roots = root / 'empty.pkl'
    roots.write_bytes(pickle.dumps([]))
    os.environ['SLOPE_WALK_ROOTS'] = str(roots)
    os.environ['SLOPE_WALK_CUT'] = '10'
    os.environ['SLOPE_WALK_MINUTES'] = '0'
    parallel.CUT, parallel.WALL_S = 10, 0
    calls = []
    segment_code = next(c for c in parallel._child.__code__.co_consts
                        if isinstance(c, types.CodeType) and c.co_name == 'segment')

    def profile(frame, event, result):
        if event != 'return':
            return
        if frame.f_code is segment_code:
            caller = frame.f_back.f_locals
            st = frame.f_locals['st']
            calls.append((result[0], identity((caller['name'], caller['s'], caller['a'], caller['kw'],
                                              st['regions'], st['wstack']))))
        elif frame.f_code is parallel._child.__code__:
            with gzip.open(root / 'calls.pkl.gz', 'wb') as stream:
                pickle.dump(calls, stream, protocol=5)

    sys.setprofile(profile)
    try:
        parallel.shard(manifest['run'], 0, 1, 1, str(root / 'walk'))
    finally:
        sys.setprofile(None)
    put(manifest['outputs'][which], (root / 'calls.pkl.gz').read_bytes())
    with gzip.open(root / 'calls.pkl.gz', 'rb') as stream:
        saved = pickle.load(stream)
    print({'version': which, 'segments': len(saved)}, flush=True)


if __name__ == '__main__':
    main(sys.argv[1])
