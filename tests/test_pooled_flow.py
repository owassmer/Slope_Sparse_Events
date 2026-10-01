"""A resumed forecast must use all its reductions, once, under the same judgments."""
import io
import json
import pickle
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.analysis import pooled, reduce, tables_page
from app.disputes import pool


@pytest.fixture
def saved_pool(tmp_path, monkeypatch):
    directory = tmp_path / 'pool'
    (directory / 'ctl').mkdir(parents=True)
    (directory / 'ctl/control.pkl').write_bytes(pickle.dumps({'part_cost': {'part0.pkl': (1, 1)}}))
    (directory / 'ctl/paths').mkdir()
    (directory / 'ctl/paths/part0.pkl').write_bytes(b'path')
    (directory / 'states0.json.gz').write_bytes(b'states')
    pooled.save(directory / 'binding.json', {'run': 'r'})
    root = tmp_path / 'recorded'
    out = root / 'r'
    out.mkdir(parents=True)
    monkeypatch.delenv('SLOPE_VARIANT', raising=False)
    monkeypatch.setattr(pooled, 'binding', lambda *args: {'run': 'r'})
    calls = {'judge': 0, 'jobs': [], 'merge': 0}

    def judge(*args, count_only=False, **kwargs):
        if not count_only:
            calls['judge'] += 1
            (out / 'tree_answers.json').write_text('{"answers": {}}')
            (out / 'tree_judgments.json.gz').write_bytes(b'judgments')

    def job(run, ctl, answers, j, jobs, procs, folder, **kwargs):
        calls['jobs'].append(j)
        target = Path(folder)
        target.mkdir(parents=True, exist_ok=True)
        for k in range(j * procs, (j + 1) * procs):
            for kind in ('tab', 'stress'):
                (target / f'{kind}{k}.pkl').write_bytes(f'{kind}{k}'.encode())

    def merge(folder, target):
        calls['merge'] += 1
        assert len(list(Path(folder).glob('tab*.pkl'))) == 16
        assert len(list(Path(folder).glob('stress*.pkl'))) == 16
        Path(target).mkdir()
        for name in ('tables.pkl', 'stress.pkl'):
            (Path(target) / name).write_bytes(b'merged')

    monkeypatch.setattr(pool, 'judge', judge)
    monkeypatch.setattr(reduce, 'job', job)
    monkeypatch.setattr(reduce, 'merge', merge)
    monkeypatch.setattr(tables_page, 'build', lambda *args, **kwargs: {
        'dates': ['2024-05-15'], 'bank': {'daily': {'collected_mean': [20]}},
        'event': {'daily': {'collected_mean': [10]}}})
    return directory, root, calls, job


def test_interrupted_reduction_resumes_only_missing_batches(saved_pool, monkeypatch):
    directory, root, calls, job = saved_pool

    def interrupted(*args, **kwargs):
        if args[3] == 2:
            raise RuntimeError('interrupted')
        job(*args, **kwargs)

    monkeypatch.setattr(reduce, 'job', interrupted)
    with pytest.raises(RuntimeError, match='interrupted'):
        pooled.build('r', root, directory, processes=1, progress=lambda _: None)
    assert calls['jobs'] == [0, 1]
    assert calls['merge'] == 0
    monkeypatch.setattr(reduce, 'job', job)
    artifacts = pooled.build('r', root, directory, processes=1, progress=lambda _: None)
    assert calls['judge'] == 1
    assert calls['jobs'] == list(range(16))
    assert all((root / 'r' / name).is_file() for name in artifacts)
    assert '2024-05-15,event,10' in (root / 'r/daily.csv').read_text()


def test_wrong_input_binding_stops_before_forecast(saved_pool):
    directory, root, calls, _ = saved_pool
    pooled.save(directory / 'binding.json', {'run': 'another'})
    with pytest.raises(ValueError, match='does not match'):
        pooled.build('r', root, directory, processes=1, progress=lambda _: None)
    assert calls['judge'] == 0 and not calls['jobs']


def test_changed_situations_do_not_reuse_reductions(saved_pool):
    directory, root, calls, _ = saved_pool
    pooled.build('r', root, directory, processes=1, progress=lambda _: None)
    (directory / 'states0.json.gz').write_bytes(b'changed states')
    pooled.build('r', root, directory, processes=1, progress=lambda _: None)
    assert calls['judge'] == 2
    assert calls['jobs'] == list(range(16)) * 2
    assert json.loads((directory / 'analysis-stages.json').read_text())['inputs']['states']


def test_s3_pool_extracts_real_archives_and_reuses_completed_downloads(tmp_path, monkeypatch):
    downloads = []

    class S3:
        def head_object(self, **kwargs):
            return {'ETag': kwargs['Key'], 'ContentLength': 100}

        def get_object(self, **kwargs):
            return {'Body': io.BytesIO(b'{"run": "r"}')}

        def download_file(self, bucket, key, dest):
            downloads.append(key)
            if key.endswith('.pkl'):
                Path(dest).write_bytes(b'control')
                return
            name = 'part0.pkl' if key.endswith('paths.tgz') else f'{Path(key).stem}.json.gz'
            with tarfile.open(dest, 'w:gz') as archive:
                member = tarfile.TarInfo(name)
                member.size = 4
                archive.addfile(member, io.BytesIO(b'data'))

    monkeypatch.setitem(sys.modules, 'boto3', SimpleNamespace(client=lambda _: S3()))
    target = tmp_path / 'pool'
    for _ in range(2):
        pooled.fetch('s3://bucket/prefix/pool', target, {'run': 'r'})
    assert len(downloads) == 18
    (target / 'ctl/paths/part0.pkl').unlink()
    pooled.fetch('s3://bucket/prefix/pool', target, {'run': 'r'})
    assert len(downloads) == 19
    assert (target / 'ctl/paths/part0.pkl').read_bytes() == b'data'
    assert len(list(target.rglob('states*.json.gz'))) == 16
    with pytest.raises(ValueError, match='does not match'):
        pooled.fetch('s3://bucket/prefix/pool', target, {'run': 'another'})
    assert len(downloads) == 19


def test_changed_paths_invalidate_reductions_and_missing_paths_fail(saved_pool):
    directory, root, calls, _ = saved_pool
    pooled.build('r', root, directory, processes=1, progress=lambda _: None)
    path = directory / 'ctl/paths/part0.pkl'
    path.write_bytes(b'changed path')
    pooled.build('r', root, directory, processes=1, progress=lambda _: None)
    assert calls['jobs'] == list(range(16)) * 2
    path.unlink()
    with pytest.raises(ValueError, match='path file is missing'):
        pooled.build('r', root, directory, processes=1, progress=lambda _: None)


def test_zero_path_parts_do_not_require_path_files(saved_pool):
    directory, root, calls, _ = saved_pool
    ctl = directory / 'ctl/control.pkl'
    ctl.write_bytes(pickle.dumps({'part_cost': {'part0.pkl': (1, 1), 'part1.pkl': (0, 1)}}))
    pooled.build('r', root, directory, processes=1, progress=lambda _: None)
    assert calls['judge'] == 1
