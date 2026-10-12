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
        assert report['independent_replay']['status'] == 'sample_ready_for_review'
        assert report['no_skip']['histories'] == 2
        assert report['no_future']['histories'] == 2
        assert not report['pass']  # synthetic dispute cannot be reconstructed
        assert not report['no_future']['pass']
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
        root = Path(args[-1])
        if args[5] == 'heavy-7':
            dest = root / 'first'
        else:
            dest = root / 'var' / 'walk-population' / 'first'
            (root / 'out').mkdir()
            (root / 'out' / 'log.txt').write_text('walk log')
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


def test_parts_equal_whole_exactly_and_duplicate_across_parts_stops(tmp_path):
    from app.analysis.tree_parts import combine

    root = tmp_path / 'input'
    root.mkdir()
    mask = np.arange(512) < 200
    a = piece(root, 'a', [path('yes', mask)], remaining=((1,), (2,)))
    b = piece(root, 'b', [path('no')], input_routes=((1,),))
    c = piece(root, 'c', [path('yes', ~mask)], input_routes=((2,),))
    whole = pool(database=tmp_path / 'whole.sqlite', output=tmp_path / 'whole', local=[root])
    pool(database=tmp_path / 'nodes-scratch.sqlite', output=tmp_path / 'nodes', local=[root], nodes_only=True)
    catalog = tmp_path / 'nodes/nodes.sqlite'
    for i, inputs in enumerate(([b], [c, a])):
        result = pool(database=tmp_path / f'scratch-{i}.sqlite', output=tmp_path / f'part-{i}',
                      local=inputs, partial=True, node_catalog=catalog)
        assert result['status'] == 'partial_ready'
    result = combine(database=tmp_path / 'combine.sqlite', output=tmp_path / 'combined',
                     local=[tmp_path / 'part-1', tmp_path / 'part-0'])
    assert result == whole
    for name in ('catalog.jsonl.gz', 'histories.jsonl.gz'):
        with gzip.open(tmp_path / 'whole' / name, 'rt') as x, gzip.open(tmp_path / 'combined' / name, 'rt') as y:
            assert x.read() == y.read()
    assert (tmp_path / 'whole/pieces.json').read_bytes() == (tmp_path / 'combined/pieces.json').read_bytes()
    with pytest.raises(ValueError, match='duplicate piece range across parts'):
        combine(database=tmp_path / 'duplicate.sqlite', output=tmp_path / 'bad',
                local=[tmp_path / 'part-0', tmp_path / 'part-1', tmp_path / 'part-0'])


@pytest.mark.parametrize('failure', ['gap', 'route_overlap', 'terminal_overlap', 'bad_answer', 'dated'])
def test_partial_gate_failures_survive_combination(tmp_path, failure):
    from dataclasses import replace

    from app.analysis.tree_parts import combine

    a = piece(tmp_path, 'a', [path('yes')], remaining=((1,),))
    b = piece(tmp_path, 'b', [path('no' if failure != 'bad_answer' else 'bad')],
              input_routes=((2,),) if failure in ('gap', 'route_overlap') else ((1,),))
    if failure == 'terminal_overlap':
        b = piece(tmp_path, 'overlap', [path('yes'), path('no')], input_routes=((1,),))
    if failure == 'dated':
        (b / 'events.pkl.gz').unlink()
        w = PieceWriter(b, 512)
        node = Node('q', 'd', 'choice', '', '', 'test', 'Choose', (), 'today', ('yes', 'no'))
        w.put('nodes', {'q': node, 'q|#live': replace(node, key='q|#live')})
        w.put('path', (replace(path('no'), classes=(('q', ('#live',), None),)), None))
        w.put('classification', {})
        w.close()
    inputs = [a, b]
    if failure == 'route_overlap':
        inputs.append(piece(tmp_path, 'extra', [], input_routes=((2,),)))
    pool(database=tmp_path / 'n.sqlite', output=tmp_path / 'nodes', local=inputs, nodes_only=True)
    for i, p in enumerate(inputs):
        pool(database=tmp_path / f's{i}.sqlite', output=tmp_path / f'part{i}', local=[p],
             partial=True, node_catalog=tmp_path / 'nodes/nodes.sqlite')
    result = combine(database=tmp_path / 'c.sqlite', output=tmp_path / 'out',
                     local=[tmp_path / f'part{i}' for i in range(len(inputs))])
    gate = 'answers' if failure in ('bad_answer', 'dated') else 'coverage'
    assert not result[gate]['pass']


def test_frozen_node_universe_required_and_domain_checked(tmp_path):
    from dataclasses import replace

    from app.analysis.tree_parts import combine

    dirs = []
    for i, tag in enumerate(('z', 'a')):
        p = piece(tmp_path, tag, [path('yes')], partition=i, partitions=2)
        (p / 'events.pkl.gz').unlink()
        w = PieceWriter(p, 512)
        node = Node('q', 'd', 'choice', '', '', 'test', 'Choose', (), 'today', ('yes', 'no'))
        w.put('nodes', {'q': node, f'q|#{tag}': replace(node, key=f'q|#{tag}')})
        w.put('path', (replace(path('yes'), classes=(('q', (), None),)), None))
        w.put('classification', {})
        w.close()
        dirs.append(p)
        pool(database=tmp_path / f'ns{i}.sqlite', output=tmp_path / f'n{i}', local=[p], nodes_only=True)
    combine(database=tmp_path / 'nc.sqlite', output=tmp_path / 'nodes',
            local=[tmp_path / 'n0', tmp_path / 'n1'], nodes_only=True)
    for i, p in enumerate(dirs):
        pool(database=tmp_path / f's{i}.sqlite', output=tmp_path / f'p{i}', local=[p], partial=True,
             node_catalog=tmp_path / 'nodes/nodes.sqlite')
    combined = combine(database=tmp_path / 'c.sqlite', output=tmp_path / 'out',
                       local=[tmp_path / 'p0', tmp_path / 'p1'])
    whole = pool(database=tmp_path / 'whole.sqlite', output=tmp_path / 'whole', local=dirs)
    assert combined == whole
    with pytest.raises(ValueError, match='absent or changed'):
        pool(database=tmp_path / 'missing.sqlite', output=tmp_path / 'missing', local=[dirs[0]],
             partial=True, node_catalog=tmp_path / 'n1/nodes.sqlite')
    with connect(tmp_path / 'n0/nodes.sqlite') as db:
        blob = db.execute('SELECT blob FROM nodes WHERE key=?', ('q',)).fetchone()[0]
        import pickle
        with db:
            db.execute('UPDATE nodes SET blob=? WHERE key=?',
                       (pickle.dumps(replace(pickle.loads(blob), branches=('other',))), 'q'))
    with pytest.raises(ValueError, match='domain changed'):
        combine(database=tmp_path / 'bad.sqlite', output=tmp_path / 'bad',
                local=[tmp_path / 'n0', tmp_path / 'n1'], nodes_only=True)


def test_priority_sample_is_global_bottom_k_not_a_quota_per_part(tmp_path):
    from app.analysis.tree_parts import combine, summarize
    from app.analysis.tree_parts import connect as part_connect

    dirs = []
    for i, size in enumerate((3, 97)):
        dirs.append(piece(tmp_path, f'input{i}', [
            DisputePath('d', (('choice', str(n + i * 100), 'yes'),), 'done', (('q', 'yes'),))
            for n in range(size)], partition=i, partitions=2))
    with connect(tmp_path / 'whole.sqlite') as db:
        for p in dirs:
            ingest(db, p, str(p))
        export(db, tmp_path / 'whole', sample_size=10)
    for i, p in enumerate(dirs):
        with connect(tmp_path / f's{i}.sqlite') as db:
            ingest(db, p, str(p))
            summarize(db, tmp_path / f'p{i}/partial.sqlite', sample_size=10)
    combine(database=tmp_path / 'c.sqlite', output=tmp_path / 'out',
            local=[tmp_path / 'p1', tmp_path / 'p0'])
    with part_connect(tmp_path / 'whole/partial.sqlite') as a, part_connect(tmp_path / 'c.sqlite') as b:
        assert list(a.execute('SELECT * FROM samples ORDER BY priority')) == list(b.execute(
            'SELECT * FROM samples ORDER BY priority'))
    with gzip.open(tmp_path / 'out/histories.jsonl.gz', 'rt') as f:
        records = [json.loads(line) for line in f]
    assert len(records) == 10
    # No fixed half-sample quota: the smaller part has only three histories.
    assert sum(r['source'] == str(dirs[1]) for r in records) >= 7


def test_partial_keeps_last_finished_block_and_rejects_wrong_counts(tmp_path):
    from app.analysis.tree_parts import combine, summarize

    a = piece(tmp_path, 'a', [path('yes')])
    with connect(tmp_path / 's.sqlite') as db:
        ingest(db, a, 'a')
        result = summarize(db, tmp_path / 'p/partial.sqlite', seconds=0)
        assert result['status'] == 'partial_ready'
        assert result['selection_complete']
    combine(database=tmp_path / 'c.sqlite', output=tmp_path / 'out', local=[tmp_path / 'p'])
    meta = json.loads((a / 'piece.json').read_text())
    meta['history_draws'] += 1
    (a / 'piece.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError, match='truncated or inconsistent'):
        pool(database=tmp_path / 'bad.sqlite', output=tmp_path / 'bad', local=[a], nodes_only=True)


def test_part_planner_bounds_artifacts_and_uses_shared_nodes(monkeypatch):
    from types import SimpleNamespace

    from app.analysis.tree_pool_jobs import commands

    def gh(args, **kwargs):
        if args[1] == 'api':
            return SimpleNamespace(stdout='heavy-0\nout-0\nheavy-1\nout-1\n')
        folder = Path(args[-1]) / f'{args[3]}-{args[5]}'
        folder.mkdir()
        (folder / 'piece.json').write_text(json.dumps({'histories': 128, 'history_draws': 65536}))
        return SimpleNamespace()

    monkeypatch.setattr('app.analysis.tree_pool_jobs.subprocess.run', gh)
    lines = commands(phase='parts', runs=['101', '102'], node_run='200')
    assert len(lines) == 8
    assert all(line.count('--artifact') == 1 and '--node-run 200' in line for line in lines)
    assert all('--output var/pool/' in line and '--diagnostics var/diag/' in line for line in lines)
    assert '--runs 101 --artifact heavy-0' in lines[0]
    assert len(commands(phase='combine', runs=['201', '202'])) == 1


@pytest.mark.parametrize('compressed', [False, True])
def test_truncated_stream_after_classification_still_stops(tmp_path, compressed):
    import pickle

    a = piece(tmp_path, 'a', [path('yes')])
    if compressed:
        file = a / 'events.pkl.gz'
        file.write_bytes(file.read_bytes()[:-8])  # all records present, gzip trailer missing
    else:
        raw = gzip.decompress((a / 'events.pkl.gz').read_bytes())
        (a / 'events.pkl.gz').unlink()
        (a / 'events.pkl').write_bytes(raw + pickle.dumps(('ignored', {'x': 1}))[:-2])
    with connect(tmp_path / 's.sqlite') as db, pytest.raises((EOFError, pickle.UnpicklingError)):
        ingest(db, a, 'a')


def test_artifact_selector_only_downloads_requested_pair(tmp_path, monkeypatch):
    import shutil
    from types import SimpleNamespace

    a = piece(tmp_path, 'a', [path('yes'), path('no')])
    downloads = []

    def run(args, **kwargs):
        if args[1] == 'api':
            return SimpleNamespace(stdout='heavy-0\nout-0\nheavy-1\nout-1\n')
        downloads.append(args[5])
        dest = Path(args[-1]) / 'piece'
        dest.mkdir(exist_ok=True)
        file = 'events.pkl.gz' if args[5] == 'heavy-1' else 'piece.json'
        shutil.copyfile(a / file, dest / file)
        return SimpleNamespace()

    monkeypatch.setattr('app.analysis.tree_pool.subprocess.run', run)
    pool(database=tmp_path / 's.sqlite', output=tmp_path / 'nodes', runs=['101'],
         artifacts=['heavy-1'], nodes_only=True)
    assert downloads == ['heavy-1', 'out-1']
