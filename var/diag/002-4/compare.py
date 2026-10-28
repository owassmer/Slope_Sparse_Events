"""Run the real CLI, comparing old/new analysis on the very same sampled histories."""
import importlib.util
import json
import subprocess
import sys
import time
import types
from pathlib import Path

import numpy as np

import app.analysis.core as core
import app.analysis.page as page

BASE = 'b903b5aae72e74e2432831fe4bba6168a08db784'
legacy = types.ModuleType('app.analysis._two_pass_reference')
sys.modules[legacy.__name__] = legacy
exec(open('var/diag/002-4/core_two_pass.py').read(), legacy.__dict__)  # core.py before 002-4 (outcome/002 at bfe419d2)
original = core.Analysis.__init__
references = {}


def equal(x, y, path='root'):
    if isinstance(x, np.ndarray):
        np.testing.assert_array_equal(x, y, err_msg=path)
    elif isinstance(x, dict):
        assert x.keys() == y.keys(), path
        for k in x:
            equal(x[k], y[k], path + '.' + str(k))
    elif isinstance(x, (list, tuple)):
        assert len(x) == len(y), path
        for i, (a, b) in enumerate(zip(x, y)):
            equal(a, b, path + '.' + str(i))
    elif isinstance(x, (core.Bins, core.Counts)) or type(x).__name__ in ('Bins', 'Counts'):
        equal(vars(x), vars(y), path)
    else:
        assert x == y, (path, x, y)


def init(self, feed, setup, model, stress=False, sens=None, dispute_model=None, progress=None):
    if stress:
        return original(self, feed, setup, model, stress, sens, dispute_model, progress)
    n = len(model.combos)
    for label, cls in [('before', legacy.Analysis), ('after', None)]:
        starts = {}
        started = time.perf_counter()
        def tick(phase, done, total):
            now = time.perf_counter()
            starts.setdefault(phase, now)
            if done == total:
                seconds = now - starts[phase]
                print(f'{label} analysis {phase}: {done}/{total} paths, {seconds:.6f} s, {seconds/total:.9f} s/history', flush=True)
            if progress:
                progress(phase, done, total)
        if cls:
            old = cls(feed, setup, model, sens=sens, dispute_model=dispute_model, progress=tick)
        else:
            original(self, feed, setup, model, sens=sens, dispute_model=dispute_model, progress=tick)
        seconds = time.perf_counter() - started
        print(f'{label} analysis TOTAL: {n} histories, {seconds:.6f} s, {seconds/n:.9f} s/history', flush=True)
    for name in ('r', 'bank_r'):
        a, b = getattr(old, name), getattr(self, name)
        for key in vars(a):
            if key != 'setup':
                equal(getattr(a, key), getattr(b, key), name + '.' + key)
    for name in ('views', 'attribution', 'scenarios', 'judgment_sensitivity'):
        equal(getattr(old, name)(), getattr(self, name)(), name)
    references[id(self)] = old
    print('PASS: all reduction arrays, bins, counts, metrics, daily series, attribution, scenarios and sensitivities exactly equal', flush=True)
    print('Bin resolutions (cents): ' + json.dumps({k: {'min': float(v.width.min()), 'max': float(v.width.max())} for k, v in self.r.bins.items()}), flush=True)


core.Analysis.__init__ = init
finish_histograms = core.Reduction.finish_histograms

def measured_histograms(self, bins):
    print(f'after histogram spool: {self.n} histories, {self._histogram_spool.tell()} bytes', flush=True)
    return finish_histograms(self, bins)

core.Reduction.finish_histograms = measured_histograms
payload = page.page_payload

def compare_payload(a, *args, **kwargs):
    new = payload(a, *args, **kwargs)
    if id(a) in references:
        old = payload(references.pop(id(a)), *args, **kwargs)
        equal(old, new, 'page_payload')
        Path('var/diag/002-4').mkdir(parents=True, exist_ok=True)
        Path('var/diag/002-4/payload.json').write_text(json.dumps(new, default=str))
        print('PASS: entire real-case page payload exactly equal (zero percentile drift)', flush=True)
    return new

page.page_payload = compare_payload
from app.cli import main
main()
