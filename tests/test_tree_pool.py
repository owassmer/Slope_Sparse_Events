import gzip
import json
from pathlib import Path

import numpy as np
import pytest

from app.analysis.piece_store import PieceWriter
from app.analysis.tree_pool import connect, coverage, export, ingest, pool
from app.disputes.forecast import DisputePath, Node, composite, pack_mask, pack_row


def piece(root, name, paths, *, input_routes=((),), remaining=(), partition=0, partitions=1):
    directory = root / name
    directory.mkdir()
    w = PieceWriter(directory, 512)
    node = Node('q', 'd', 'choice', '', '', 'test', 'Choose', (), 'today', ('yes', 'no'))
    w.put('nodes', {'q': node})
    w.put('questions', ((), [('q', pack_row({'day': np.zeros(512, dtype=int)}))]))
    for p in paths:
        w.put('path', (p, None))
    w.put('classification', {})
    w.close()
    meta = dict(partition=partition, partitions=partitions, depth=6, input_routes=input_routes,
                remaining=remaining, complete=not remaining, histories=len(paths),
                history_draws=sum(512 if p.mask is None else int(np.unpackbits(
                    np.frombuffer(p.mask, np.uint8)).sum()) for p in paths))
    (directory / 'piece.json').write_text(json.dumps(meta))
    return directory


def path(answer, mask=None, *, edges=None):
    return DisputePath('d', (('choice', '', answer),), 'done',
                       edges or (('q', answer),), mask=pack_mask(mask))


def test_complete_continuations_order_independent_and_probability_all_draws(tmp_path):
    a = piece(tmp_path, 'first', [path('yes')], remaining=((1,),))
    b = piece(tmp_path, 'next', [path('no')], input_routes=((1,),))
    with connect(tmp_path / 'scratch.sqlite') as db:
        ingest(db, b, 'b')
        ingest(db, a, 'a')
        report = export(db, tmp_path / 'output', sample_size=2)
        assert all(report[k]['pass'] for k in ('coverage', 'probability', 'answers'))
        assert np.allclose(report['probability']['sums'], 1)
        assert report['no_skip']['status'] == 'sample_ready_for_review'
        assert report['no_future']['status'] == 'sample_ready_for_review'
        with gzip.open(tmp_path / 'output/catalog.jsonl.gz', 'rt') as f:
            q = json.loads(next(f))
        assert (q['histories'], q['draws'], q['history_draws']) == (2, 512, 1024)


@pytest.mark.parametrize('failure', [None, 'gap', 'overlap'])
def test_shared_continuations_and_nested_restarts(tmp_path, failure):
    a = piece(tmp_path, 'root', [], remaining=((0,), (1,), (2,)))
    b = piece(tmp_path, 'share1', [], input_routes=((0,), (2,)), remaining=((0, 1),))
    c = piece(tmp_path, 'share2', [], input_routes=((1,),))
    d = piece(tmp_path, 'restart', [], input_routes=((0, 1),))
    empty = piece(tmp_path, 'empty', [], input_routes=())
    with connect(tmp_path / 'shares.sqlite') as db:
        for directory in [d, empty, b, a] + ([] if failure == 'gap' else [c]):
            ingest(db, directory, directory.name)
        if failure == 'overlap':
            extra = piece(tmp_path, 'extra', [], input_routes=((2,),))
            ingest(db, extra, 'extra')
        assert coverage(db)['pass'] == (failure is None)


def test_gap_overlap_and_missing_top_partition(tmp_path):
    a = piece(tmp_path, 'first', [path('yes')], remaining=((1,),), partitions=2)
    with connect(tmp_path / 'scratch.sqlite') as db:
        ingest(db, a, 'a')
        report = export(db, tmp_path / 'output')
        assert report['coverage']['missing_partitions'] == [1]
        assert not report['coverage']['pass']
        assert not report['probability']['pass']
        with pytest.raises(ValueError, match='duplicate piece'):
            ingest(db, a, 'retry')
    b = piece(tmp_path, 'overlap', [path('yes'), path('yes'), path('no')])
    with connect(tmp_path / 'overlap.sqlite') as db:
        ingest(db, b, 'b')
        assert coverage(db)['duplicate_history_draws'] == 512


def test_bad_answer_is_failure_not_zero_or_renormalized(tmp_path):
    a = piece(tmp_path, 'bad', [path('unoffered')])
    with connect(tmp_path / 'scratch.sqlite') as db:
        ingest(db, a, 'a')
        report = export(db, tmp_path / 'output')
        assert not report['answers']['pass']
        assert report['answers']['failed_histories'] == 1
        assert not report['probability']['pass']


def test_complement_probability_and_disjoint_draw_support(tmp_path):
    mask = np.arange(512) % 2 == 0
    yes = ((composite([[('q', 'yes')]]), 'yes'),)
    no = ((composite([[('q', 'yes')]]), 'no'),)
    a = piece(tmp_path, 'all', [path('yes', mask, edges=yes), path('yes', ~mask, edges=yes),
                                path('no', edges=no)])
    with connect(tmp_path / 'scratch.sqlite') as db:
        ingest(db, a, 'a')
        report = export(db, tmp_path / 'output')
        assert report['probability']['pass']
        assert report['coverage']['pass']


def test_legacy_routes_unknown_until_explicitly_supplied(tmp_path):
    a = piece(tmp_path, 'legacy', [path('yes'), path('no')])
    meta = json.loads((a / 'piece.json').read_text())
    del meta['input_routes']
    (a / 'piece.json').write_text(json.dumps(meta))
    with connect(tmp_path / 'scratch.sqlite') as db:
        ingest(db, a, 'a')
        assert not coverage(db)['pass']
    with connect(tmp_path / 'linked.sqlite') as db:
        ingest(db, a, 'a', {'a': {'input_routes': [[]]}})
        assert coverage(db)['pass']


def test_dated_class_requires_live_facts_on_its_native_draws(tmp_path):
    from dataclasses import replace

    directory = tmp_path / 'dated'
    directory.mkdir()
    w = PieceWriter(directory, 512)
    node = Node('q', 'd', 'choice', '', '', 'test', 'Choose', (), 'today', ('yes', 'no'))
    w.put('nodes', {'q': node, 'q|#live': replace(node, key='q|#live')})
    # The class claims all 512 draws, but its dated facts only offer it to 256.
    day = np.where(np.arange(512) < 256, 0, 1_000_000)
    w.put('questions', ((), [('q|#live', pack_row({'day': day}))]))
    for answer in ('yes', 'no'):
        p = replace(path(answer), classes=(('q', ('#live',), None),))
        w.put('path', (p, None))
    w.put('classification', {})
    w.close()
    (directory / 'piece.json').write_text(json.dumps(dict(
        partition=0, partitions=1, depth=6, input_routes=[[]], remaining=[],
        complete=True, histories=2, history_draws=1024)))
    with connect(tmp_path / 'scratch.sqlite') as db:
        ingest(db, directory, 'dated')
        report = export(db, tmp_path / 'output')
        assert report['answers']['failed_histories'] == 2
        assert 'no dated offered question' in report['answers']['examples'][0]['error']


def test_export_checkpoint_resumes_without_counting_paths_twice(tmp_path):
    paths = [DisputePath('d', (('choice', str(i), 'yes'),), 'done', (('q', 'yes'),))
             for i in range(1001)]
    a = piece(tmp_path, 'many', paths)
    with connect(tmp_path / 'scratch.sqlite') as db:
        ingest(db, a, 'a')
        partial = export(db, tmp_path / 'output', seconds=0)
        assert partial['status'] == 'unfinished_export'
        resumed = export(db, tmp_path / 'output')
        assert resumed['histories'] == 1001
        whole = export(db, tmp_path / 'other', sample_size=31)  # distinct cache signature
        assert resumed['probability']['sums'] == whole['probability']['sums']


def test_remote_downloads_one_artifact_at_a_time_and_only_deletes_temporary_copy(tmp_path, monkeypatch):
    import shutil
    from types import SimpleNamespace

    a = piece(tmp_path, 'first', [path('yes')], remaining=((1,),))
    b = piece(tmp_path, 'second', [path('no')], input_routes=((1,),))
    downloads = []

    def run(args, **kwargs):
        if args[1] == 'api':
            return SimpleNamespace(stdout='heavy-0\n')
        if downloads:
            assert not downloads[-1].exists()
        dest = Path(args[-1])
        downloads.append(dest)
        shutil.copytree(a if args[3] == '101' else b, dest / 'piece')
        return SimpleNamespace()

    monkeypatch.setattr('app.analysis.tree_pool.subprocess.run', run)
    report = pool(database=tmp_path / 'scratch.sqlite', output=tmp_path / 'output', runs=['101', '102'])
    assert report['coverage']['pass']
    assert all(not d.exists() for d in downloads)
    assert (a / 'events.pkl.gz').exists()
    # Restart skips previously downloaded sources.
    pool(database=tmp_path / 'scratch.sqlite', output=tmp_path / 'output', runs=['101', '102'])
    assert len(downloads) == 2


def test_resume_legacy_piece_commit_without_source_marker(tmp_path):
    a = piece(tmp_path, 'piece', [path('yes'), path('no')])
    database = tmp_path / 'scratch.sqlite'
    with connect(database) as db:
        ingest(db, a, str(a))
    report = pool(database=database, output=tmp_path / 'out', local=[a])
    assert report['histories'] == 2
    assert report['coverage']['pass']


def test_parallel_pool_matches_serial_across_ranges_and_recursive_roots(tmp_path):
    root = tmp_path / 'pieces'
    root.mkdir()
    paths = [DisputePath('d', (('choice', str(i), 'yes'),), 'done', (('q', 'yes'),))
             for i in range(4200)]
    piece(root, 'a', paths[:2100], remaining=((1,),))
    piece(root, 'b', paths[2100:], input_routes=((1,),))
    reports = []
    for workers in (1, 2):
        reports.append(pool(database=tmp_path / f'{workers}.sqlite',
                            output=tmp_path / f'out-{workers}', local=[root], workers=workers))
    assert reports[0] == reports[1]
    assert reports[0]['coverage']['pass']
    assert reports[0]['histories'] == 4200
    for file in ('catalog.jsonl.gz', 'histories.jsonl.gz'):
        with gzip.open(tmp_path / 'out-1' / file, 'rt') as a, gzip.open(tmp_path / 'out-2' / file, 'rt') as b:
            assert a.read() == b.read()
    resumed = pool(database=tmp_path / '2.sqlite', output=tmp_path / 'out-2', local=[root], workers=2)
    assert resumed == reports[1]
    # A checkpoint made with multiple workers can finish with one worker without
    # repeating a range or changing the reservoir sample.
    with connect(tmp_path / 'checkpoint.sqlite') as db:
        for manifest in sorted(root.rglob('piece.json')):
            ingest(db, manifest.parent, str(manifest.parent))
        assert export(db, tmp_path / 'checkpoint', workers=2, seconds=0)['status'] == 'unfinished_export'
        assert export(db, tmp_path / 'checkpoint', workers=1) == reports[0]


@pytest.mark.parametrize('failure', ['duplicate', 'truncated', 'draws'])
def test_parallel_ingestion_rejects_invalid_pieces(tmp_path, failure):
    import shutil

    root = tmp_path / 'pieces'
    root.mkdir()
    a = piece(root, 'a', [path('yes')], remaining=((1,),))
    if failure == 'duplicate':
        shutil.copytree(a, root / 'b')
    else:
        b = piece(root, 'b', [path('no')], input_routes=((1,),))
        meta = json.loads((b / 'piece.json').read_text())
        meta['histories' if failure == 'truncated' else 'history_draws'] += 1
        (b / 'piece.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError, match='duplicate piece|truncated or inconsistent'):
        pool(database=tmp_path / 'scratch.sqlite', output=tmp_path / 'out', local=[root], workers=2)


@pytest.mark.parametrize('orphan', [False, True])
def test_github_split_artifacts_and_local_wave_together(tmp_path, monkeypatch, orphan):
    import shutil
    from types import SimpleNamespace

    a = piece(tmp_path, 'first', [path('yes')], remaining=((1,),))
    local = tmp_path / 'aws'
    local.mkdir()
    piece(local, 'second', [path('no')], input_routes=((1,),))
    downloads = []

    def run(args, **kwargs):
        if args[1] == 'api':
            return SimpleNamespace(stdout='out-7\nheavy-7\n')
        downloads.append(args[5])
        dest = Path(args[-1]) / 'var' / 'walk-population' / 'first'
        dest.mkdir(parents=True, exist_ok=True)
        for file in (['events.pkl.gz'] if args[5] == 'heavy-7' else ['piece.json']):
            shutil.copyfile(a / file, dest / file)
        if orphan and args[5] == 'heavy-7':
            other = dest.parent / 'missing-manifest'
            other.mkdir()
            shutil.copyfile(a / 'events.pkl.gz', other / 'events.pkl.gz')
        return SimpleNamespace()

    monkeypatch.setattr('app.analysis.tree_pool.subprocess.run', run)
    if orphan:
        with pytest.raises(ValueError, match='directories do not match'):
            pool(database=tmp_path / 'scratch.sqlite', output=tmp_path / 'out', runs=['101'], local=[local])
        return
    result = pool(database=tmp_path / 'scratch.sqlite', output=tmp_path / 'out', runs=['101'], local=[local])
    assert downloads == ['heavy-7', 'out-7']
    assert all(result[k]['pass'] for k in ('coverage', 'probability', 'answers'))
