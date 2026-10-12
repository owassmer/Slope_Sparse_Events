"""Exercise generated commands through the CLI with GitHub's upload roots."""
import json
import shlex
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_tree_pool import path, piece
from typer.testing import CliRunner

from app.analysis.tree_pool_jobs import commands, write_jobs
from app.cli import cli


def test_generated_stages_with_real_upload_layout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    original = piece(tmp_path, 'walk-f34-0001', [path('yes'), path('no')])
    store = tmp_path / 'artifacts'
    manifest = store / 'walk/out-0/var/walk-population/walk-f34-0001/piece.json'
    manifest.parent.mkdir(parents=True)
    shutil.copyfile(original / 'piece.json', manifest)
    log = store / 'walk/out-0/out/log.txt'
    log.parent.mkdir()
    log.write_text('real upload roots')
    events = store / 'walk/heavy-0/walk-f34-0001/events.pkl.gz'
    events.parent.mkdir(parents=True)
    shutil.copyfile(original / 'events.pkl.gz', events)

    def gh(args, **kwargs):
        if args[1] == 'api':
            run = args[2].split('/')[-2]
            return SimpleNamespace(stdout='\n'.join(p.name for p in (store / run).iterdir()))
        run, name, dest = args[3], args[5], Path(args[-1])
        shutil.copytree(store / run / name, dest, dirs_exist_ok=True)
        return SimpleNamespace()

    monkeypatch.setattr('subprocess.run', gh)
    runner = CliRunner()
    for phase, run, prior in [('nodes', 'nodes', 'walk'), ('node-union', 'union', 'nodes'),
                              ('parts', 'parts', 'walk'), ('merge', 'merge', 'parts'),
                              ('combine', 'final', 'merge')]:
        work = tmp_path / run
        work.mkdir()
        monkeypatch.chdir(work)
        lines = commands(phase=phase, runs=[prior], node_run='union', workers=1)
        assert len(lines) == 1
        args = shlex.split(lines[0])
        result = runner.invoke(cli, args[args.index('pool-tree'):])
        assert result.exit_code == 0, result.output
        # Only workflow-kept paths survive into the next stage. Heavy's common
        # root may be var/ when both walk and pool are configured; rglob handles it.
        shutil.copytree(work / 'var/diag', store / run / 'out-0/var/diag')
        if (work / 'var/pool').exists():
            shutil.copytree(work / 'var/pool', store / run / 'heavy-0/pool')
        if phase == 'parts':
            import sqlite3
            with sqlite3.connect(work / 'var/pool/part-00000/partial.sqlite') as db:
                assert db.execute('SELECT COUNT(*) FROM questions').fetchone()[0] == 0
        if phase == 'combine':
            verdict = json.loads((work / 'var/diag/pool-00000/gates.json').read_text())
            assert all(verdict[k]['pass'] for k in ('coverage', 'answers', 'probability'))
            assert verdict['catalog_facts'] == 'sample_only'


@pytest.mark.parametrize('failure', ['missing_events', 'missing_manifest', 'duplicate_stream',
                                    'duplicate_manifest', 'truncated', 'counts'])
def test_real_upload_layout_rejects_broken_pairs(tmp_path, monkeypatch, failure):
    from app.analysis.tree_pool import pool

    original = piece(tmp_path, 'walk-f34-0001', [path('yes'), path('no')])

    def gh(args, **kwargs):
        if args[1] == 'api':
            return SimpleNamespace(stdout='heavy-0\nout-0\n')
        root = Path(args[-1])
        heavy = args[5] == 'heavy-0'
        if heavy and failure != 'missing_events':
            folder = root / original.name
            folder.mkdir()
            stream = folder / 'events.pkl.gz'
            stream.write_bytes((original / stream.name).read_bytes())
            if failure == 'truncated':
                stream.write_bytes(stream.read_bytes()[:-8])
            if failure == 'duplicate_stream':
                import gzip
                (folder / 'events.pkl').write_bytes(gzip.decompress(stream.read_bytes()))
        elif not heavy and failure != 'missing_manifest':
            folder = root / 'var/walk-population' / original.name
            folder.mkdir(parents=True)
            meta = json.loads((original / 'piece.json').read_text())
            if failure == 'counts':
                meta['histories'] += 1
            (folder / 'piece.json').write_text(json.dumps(meta))
            if failure == 'duplicate_manifest':
                other = root / 'duplicate' / original.name
                other.mkdir(parents=True)
                (other / 'piece.json').write_text(json.dumps(meta))
        return SimpleNamespace()

    monkeypatch.setattr('subprocess.run', gh)
    with pytest.raises((ValueError, EOFError)):
        pool(database=tmp_path / 'scratch.sqlite', output=tmp_path / 'out', runs=['walk'])


def test_interrupted_merge_is_not_a_consumable_partial(tmp_path):
    from app.analysis.tree_parts import combine, config, connect, merge_part
    from app.analysis.tree_pool import pool

    source = piece(tmp_path, 'piece', [path('yes'), path('no')])
    pool(database=tmp_path / 'scratch.sqlite', output=tmp_path / 'one', local=[source])
    target = tmp_path / 'partial.sqlite'
    with connect(target) as db:
        merge_part(db, tmp_path / 'one/partial.sqlite')
        assert not config(db)['complete']
    with pytest.raises(ValueError, match='incomplete'):
        combine(database=tmp_path / 'failed.sqlite', output=tmp_path / 'bad', local=[target])


def test_matrix_limit_and_bounded_groups(tmp_path):
    lines = commands(phase='parts', runs=['walk'], node_run='union',
                     artifact_names=[f'heavy-{i}' for i in range(1500)])
    assert len(lines) == 125
    assert all(line.count('--artifact') == 12 for line in lines)
    larger = commands(phase='nodes', runs=['walk'],
                      artifact_names=[f'heavy-{i}' for i in range(6200)])
    files = write_jobs(larger, tmp_path / 'run.txt')
    assert [len(p.read_text().splitlines()) for p in files] == [256, 256, 5]
    merges = commands(phase='merge', runs=['parts'],
                      artifact_names=[f'heavy-{i}' for i in range(256)])
    assert len(merges) == 16
    assert all('--merge-only' in line and '--output var/pool/' in line for line in merges)
