"""Generate dependency stages, never submit a workflow.

checks --runs WAVE -> check-combine --runs CHECKS runs before node union.
Then nodes --runs WALK -> node-union --runs NODES ->
parts --runs WALK --node-run UNION --check-run CHECKS -> merge -> combine.
Checks use the same canonical range packing and --remaining follow-ups as parts.
The check budget starts at .3s/history and .03s/history-draw; measured rates can
be supplied explicitly. These are planning rates, not runtime guarantees. Repeat merge for larger populations.
Use identical walk/local inputs for nodes and parts. Local folders must be staged on
runners before use. Outputs over 256 lines are split into numbered run.txt files;
submit all files of a stage before generating its successor with all their run IDs.
"""
from __future__ import annotations

import argparse
import json
import math
import shlex
import sqlite3
import subprocess
import tempfile
from pathlib import Path


def commands(*, phase, runs=(), local=(), node_run=None, workers=4, batch_size=None,
             artifact_names=None, manifest_roots=(), seconds_per_draw=None, remaining=None,
             seconds_per_history=None, check_runs=()):
    seconds_per_draw = (.03 if phase == 'checks' else .05) if seconds_per_draw is None else seconds_per_draw
    seconds_per_history = (.3 if phase == 'checks' else 1.3) if seconds_per_history is None else seconds_per_history
    base = ['uv', 'run', 'slope', 'pool-tree']
    prefix = 'OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 '
    if remaining is not None and phase not in ('parts', 'checks'):
        raise ValueError('remaining ranges apply only to parts')
    if phase == 'parts' and not node_run:
        raise ValueError('parts require the frozen --node-run from node-union')
    combine = phase in ('node-union', 'merge', 'combine', 'check-combine')
    # Node discovery and intermediate merges are bounded by artifact count.
    # Checked parts instead pack canonical ranges by manifest history-draws.
    size = batch_size or (16 if phase == 'merge' else 12)
    if size < 1:
        raise ValueError('batch size must be positive')
    inputs = []
    if phase in ('parts', 'checks'):
        inputs = reduction_inputs(runs, local, artifact_names, manifest_roots, workers,
                                  seconds_per_draw, remaining, seconds_per_history)
    elif phase in ('node-union', 'combine', 'check-combine'):
        inputs = [[arg for run in runs for arg in ('--runs', str(run))]
                  + [arg for root in local for arg in ('--local', str(root))]]
    else:
        for run in runs:
            if artifact_names is None:
                result = subprocess.run(['gh', 'api', f'repos/{{owner}}/{{repo}}/actions/runs/{run}/artifacts',
                                         '--paginate', '--jq', '.artifacts[].name'],
                                        check=True, capture_output=True, text=True)
                names = result.stdout.splitlines()
            else:
                if len(runs) != 1:
                    raise ValueError('an offline artifact list requires exactly one run')
                names = list(artifact_names)
            names = sorted(n for n in names if n.startswith('heavy-'))
            if not names:
                raise ValueError(f'{run}: no heavy artifacts')
            for i in range(0, len(names), size):
                inputs.append(['--runs', str(run)] +
                              [arg for name in names[i:i + size] for arg in ('--artifact', name)])
        folders = []
        for root in local:
            filename = 'partial.sqlite' if phase == 'merge' else 'piece.json'
            paths = sorted(Path(root).rglob(filename))
            if not paths:
                raise ValueError(f'{root}: no {filename}')
            folders.extend(str(p.parent) for p in paths)
        local_size = size if phase == 'merge' else (batch_size or 24)
        for i in range(0, len(folders), local_size):
            inputs.append([arg for folder in folders[i:i + local_size] for arg in ('--local', folder)])
    lines = []
    for i, source in enumerate(inputs):
        output = f'var/diag/nodes-{i:05d}' if phase in ('nodes', 'node-union') else f'var/pool/part-{i:05d}'
        database = output + '/partial.sqlite' if phase == 'merge' else 'scratch.sqlite'
        args = base + source + ['--database', database, '--output', output,
                                '--diagnostics', f'var/diag/pool-{i:05d}',
                                '--seconds', '800' if phase in ('parts', 'checks') else '1000',
                                '--workers', str(workers)]
        if combine:
            args += ['--combine']
        if phase in ('nodes', 'node-union'):
            args += ['--nodes-only']
        elif phase in ('checks', 'check-combine'):
            args += ['--checks-only']
        elif phase == 'parts':
            args += ['--partial', '--gates-only', '--node-run', str(node_run)]
            args += [arg for run in check_runs for arg in ('--check-run', str(run))]
        elif phase == 'merge':
            args += ['--merge-only']
        lines.append(prefix + shlex.join(args))
    return lines


def reduction_inputs(runs, local, artifact_names, manifest_roots, workers,
                     seconds_per_draw=.05, remaining=None, seconds_per_history=1.3):
    """Pack canonical blocks by manifest history-draws, not artifact count.

    Target 600 check seconds/core plus 120s ingestion. The walk-g1 logs
    show width-dependent cost: use 50ms/draw with a 1.3s/history floor for
    narrow histories (whose per-draw cost is much higher). These are elapsed
    worker-slot estimates, not CPU counters or a runtime guarantee.
    """
    if not math.isfinite(seconds_per_draw) or seconds_per_draw <= 0:
        raise ValueError('seconds per draw must be finite and positive')
    if not math.isfinite(seconds_per_history) or seconds_per_history < 0:
        raise ValueError('seconds per history must be finite and nonnegative')
    requested = None if remaining is None else set(remaining)
    if requested is not None and len(requested) != len(remaining):
        raise ValueError('duplicate remaining range')
    matched = set()
    inventory = []
    for root in local:
        paths = sorted(Path(root).rglob('piece.json'))
        if not paths:
            raise ValueError(f'{root}: no piece.json')
        inventory.extend((('--local', str(p.parent)), p.parent.name, json.loads(p.read_text())) for p in paths)
    if manifest_roots and len(runs) != 1:
        raise ValueError('offline manifests require exactly one run')
    for run in runs:
        if artifact_names is None:
            result = subprocess.run(['gh', 'api', f'repos/{{owner}}/{{repo}}/actions/runs/{run}/artifacts',
                                     '--paginate', '--jq', '.artifacts[].name'],
                                    check=True, capture_output=True, text=True)
            names = sorted(n for n in result.stdout.splitlines() if n.startswith('heavy-'))
        else:
            names = sorted(artifact_names)
        if not names:
            raise ValueError(f'{run}: no heavy artifacts')
        for name in names:
            small = name.replace('heavy-', 'out-', 1)
            with tempfile.TemporaryDirectory(prefix='pool-manifests-') as temp:
                if manifest_roots:
                    paths = sorted(p for root in manifest_roots for p in Path(root).rglob('piece.json')
                                   if small in p.parts)
                else:
                    subprocess.run(['gh', 'run', 'download', str(run), '--name', small, '--dir', temp], check=True)
                    paths = sorted(Path(temp).rglob('piece.json'))
                if not paths:
                    raise ValueError(f'{run}/{small}: no piece manifests; cannot size checked work')
                inventory.extend((('--runs', str(run), '--artifact', name), p.parent.name,
                                  json.loads(p.read_text())) for p in paths)
    budget = workers * 600
    lines, sources, ranges, work = [], [], [], 0.0

    def flush():
        if ranges:
            lines.append([arg for source in dict.fromkeys(sources) for arg in source] +
                         [arg for name, lo, hi in ranges for arg in ('--history-range', f'{name}:{lo}:{hi}')])

    seen = set()
    for source, name, meta in inventory:
        if name in seen:
            raise ValueError(f'ambiguous duplicate piece directory: {name}')
        seen.add(name)
        count, draws = meta['histories'], meta['history_draws']
        if count < 0 or draws < count or draws > count * 512:
            raise ValueError(f'{name}: invalid history-draw counts')
        for lo in range(0, count, 64) if count else [0]:
            hi = min(lo + 64, count)
            key = f'{name}:{lo}:{hi}'
            if requested is not None and key not in requested:
                continue
            matched.add(key)
            cost = max(seconds_per_draw * draws / count, seconds_per_history) * (hi - lo) if count else 0
            new_source = source not in sources
            different_run = bool(sources and source[0] == '--runs' and sources[-1][0] == '--runs'
                                 and source[1] != sources[-1][1])
            if ranges and (work + cost > budget or different_run or
                           (new_source and len(set(sources)) >= 12)):
                flush()
                sources, ranges, work = [], [], 0.0
            sources.append(source)
            if ranges and ranges[-1][0] == name and ranges[-1][2] == lo:
                ranges[-1] = (name, ranges[-1][1], hi)
            else:
                ranges.append((name, lo, hi))
            work += cost
    flush()
    if requested is not None and requested - matched:
        raise ValueError(f'unknown or noncanonical remaining ranges: {sorted(requested - matched)}')
    return lines


def read_remaining(path):
    """SQLite is authoritative after a hard stop; JSON is the normal result."""
    path = Path(path)
    if path.suffix == '.sqlite':
        with sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True) as db:
            cfg = dict(db.execute('SELECT key,value FROM config'))
        if not json.loads(cfg.get('complete', 'false')):
            raise ValueError('remaining ranges require a valid partial')
        value = json.loads(cfg['remaining_ranges'])
    else:
        value = json.loads(path.read_text())
        if isinstance(value, dict):
            value = value['remaining_ranges']
    if not isinstance(value, list) or not all(isinstance(r, str) for r in value):
        raise ValueError('remaining ranges must be a list of canonical range strings')
    return value


def write_jobs(lines, output):
    """Never generate an invalid >256-line GitHub matrix."""
    if not lines:
        raise ValueError('no jobs')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    files = []
    for i in range(0, len(lines), 256):
        target = output if len(lines) <= 256 else output.with_name(f'{output.stem}-{i // 256:03d}{output.suffix}')
        target.write_text('\n'.join(lines[i:i + 256]) + '\n')
        files.append(target)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('nodes', 'node-union', 'checks', 'check-combine', 'parts', 'merge', 'combine'), required=True)
    parser.add_argument('--runs', action='append', default=[])
    parser.add_argument('--run-list', type=Path)
    parser.add_argument('--local', action='append', default=[])
    parser.add_argument('--node-run')
    parser.add_argument('--check-run', action='append', default=[])
    parser.add_argument('--workers', type=int, choices=range(1, 5), default=4)
    parser.add_argument('--batch-size', type=int)
    parser.add_argument('--seconds-per-draw', type=float,
                        help='Worker-seconds/history-draw (checks: .03; legacy reduction: .05).')
    parser.add_argument('--seconds-per-history', type=float,
                        help='Narrow-history worker-seconds floor (checks: .3; legacy reduction: 1.3).')
    parser.add_argument('--remaining', type=Path, action='append', default=[],
                        help='Stopped partial.sqlite, remaining.json or result.json; repeat for multiple lines.')
    parser.add_argument('--artifact-list', type=Path, help='Offline inventory: one heavy-N per line, for exactly one --runs.')
    parser.add_argument('--manifest-root', action='append', default=[],
                        help='Offline downloaded out-N manifest roots (must include out-N directory).')
    parser.add_argument('--output', type=Path, default=Path('run.txt'))
    args = parser.parse_args()
    runs = args.runs
    if args.run_list:
        runs += [line.strip() for line in args.run_list.read_text().splitlines()
                 if line.strip() and not line.lstrip().startswith('#')]
    lines = commands(phase=args.phase, runs=runs, local=args.local,
                     node_run=args.node_run, workers=args.workers, batch_size=args.batch_size,
                     artifact_names=args.artifact_list.read_text().splitlines() if args.artifact_list else None,
                     manifest_roots=args.manifest_root, seconds_per_draw=args.seconds_per_draw,
                     seconds_per_history=args.seconds_per_history, check_runs=args.check_run,
                     remaining=[r for p in args.remaining for r in read_remaining(p)]
                     if args.remaining else None)
    files = write_jobs(lines, args.output)
    print(f'{len(lines)} lines in {len(files)} run(s): ' + ', '.join(map(str, files)))
    print('Submit only this stage; use ALL resulting run IDs to generate the next stage.')


if __name__ == '__main__':
    main()
