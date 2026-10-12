"""Generate dependency stages, never submit a workflow.

nodes --runs WALK -> node-union --runs NODES -> parts --runs WALK --node-run UNION
-> merge --runs PARTS -> combine --runs MERGE. Repeat merge for larger populations.
Use identical walk/local inputs for nodes and parts. Local folders must be staged on
runners before use. Outputs over 256 lines are split into numbered run.txt files;
submit all files of a stage before generating its successor with all their run IDs.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import tempfile
from pathlib import Path


def commands(*, phase, runs=(), local=(), node_run=None, workers=4, batch_size=None,
             artifact_names=None, manifest_roots=()):
    base = ['uv', 'run', 'slope', 'pool-tree']
    prefix = 'OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 '
    if phase == 'parts' and not node_run:
        raise ValueError('parts require the frozen --node-run from node-union')
    combine = phase in ('node-union', 'merge', 'combine')
    # Node discovery and intermediate merges are bounded by artifact count.
    # Checked parts instead pack canonical ranges by manifest history-draws.
    size = batch_size or (16 if phase == 'merge' else 12)
    if size < 1:
        raise ValueError('batch size must be positive')
    inputs = []
    if phase == 'parts':
        inputs = reduction_inputs(runs, local, artifact_names, manifest_roots, workers)
    elif phase in ('node-union', 'combine'):
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
                                '--seconds', '1000', '--workers', str(workers)]
        if combine:
            args += ['--combine']
        if phase in ('nodes', 'node-union'):
            args += ['--nodes-only']
        elif phase == 'parts':
            args += ['--partial', '--gates-only', '--node-run', str(node_run)]
        elif phase == 'merge':
            args += ['--merge-only']
        lines.append(prefix + shlex.join(args))
    return lines


def reduction_inputs(runs, local, artifact_names, manifest_roots, workers):
    """Pack canonical blocks by manifest history-draws, not artifact count.

    Budget 600 check seconds/core at the measured slow rate (10ms/draw),
    leaving ~120s for ingestion in a 12-minute target, and 8 minutes slack.
    Within a piece the manifest gives a mean, not a per-history distribution;
    a 64-history task caps worker stragglers at 32,768 draws (~328 seconds).
    """
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
    budget = workers * 600 / .01
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
            cost = draws * (hi - lo) / count if count else 0
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
    return lines


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
    parser.add_argument('--phase', choices=('nodes', 'node-union', 'parts', 'merge', 'combine'), required=True)
    parser.add_argument('--runs', action='append', default=[])
    parser.add_argument('--run-list', type=Path)
    parser.add_argument('--local', action='append', default=[])
    parser.add_argument('--node-run')
    parser.add_argument('--workers', type=int, choices=range(1, 5), default=4)
    parser.add_argument('--batch-size', type=int)
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
                     manifest_roots=args.manifest_root)
    files = write_jobs(lines, args.output)
    print(f'{len(lines)} lines in {len(files)} run(s): ' + ', '.join(map(str, files)))
    print('Submit only this stage; use ALL resulting run IDs to generate the next stage.')


if __name__ == '__main__':
    main()
