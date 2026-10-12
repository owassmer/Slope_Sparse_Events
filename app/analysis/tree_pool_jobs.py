"""Generate dependency stages, never submit a workflow.

nodes --runs WALK -> node-union --runs NODES -> parts --runs WALK --node-run UNION
-> merge --runs PARTS -> combine --runs MERGE. Repeat merge for larger populations.
Use identical walk/local inputs for nodes and parts. Local folders must be staged on
runners before use. Outputs over 256 lines are split into numbered run.txt files;
submit all files of a stage before generating its successor with all their run IDs.
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
from pathlib import Path


def commands(*, phase, runs=(), local=(), node_run=None, workers=4, batch_size=None,
             artifact_names=None):
    base = ['uv', 'run', 'slope', 'pool-tree']
    prefix = 'OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 '
    if phase == 'parts' and not node_run:
        raise ValueError('parts require the frozen --node-run from node-union')
    combine = phase in ('node-union', 'merge', 'combine')
    # Twelve artifacts (~64k histories), or 24 local pieces, per walk job.
    # Gate-only reductions measured ~6 ms/history including ingestion on one core;
    # this leaves over half the 1000s budget for runner/network variation.
    # A merge job takes at most 16 reduction artifacts. All downloads are bounded
    # and discarded after ingestion; no whole-wave download is staged on disk.
    size = batch_size or (16 if phase == 'merge' else 12)
    if size < 1:
        raise ValueError('batch size must be positive')
    inputs = []
    if phase in ('node-union', 'combine'):
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
    parser.add_argument('--output', type=Path, default=Path('run.txt'))
    args = parser.parse_args()
    runs = args.runs
    if args.run_list:
        runs += [line.strip() for line in args.run_list.read_text().splitlines()
                 if line.strip() and not line.lstrip().startswith('#')]
    lines = commands(phase=args.phase, runs=runs, local=args.local,
                     node_run=args.node_run, workers=args.workers, batch_size=args.batch_size,
                     artifact_names=args.artifact_list.read_text().splitlines() if args.artifact_list else None)
    files = write_jobs(lines, args.output)
    print(f'{len(lines)} lines in {len(files)} run(s): ' + ', '.join(map(str, files)))
    print('Submit only this stage; use ALL resulting run IDs to generate the next stage.')


if __name__ == '__main__':
    main()
