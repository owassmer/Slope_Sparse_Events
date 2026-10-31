import json
import sqlite3

import pytest

from app.analysis import tree_parts
from app.analysis.tree_pool import connect, ingest, pool
from app.analysis.wave_checks import load_parts, report
from tests.test_tree_pool import path, piece


def test_wave_ranges_resume_consume_and_reject_duplicate_or_missing(tmp_path, monkeypatch):
    directory = piece(tmp_path, 'wave', [path('yes'), path('no')])
    scratch = tmp_path / 'input.sqlite'
    with connect(scratch) as db:
        ingest(db, directory, str(directory))
        tree_parts.save_nodes(db, tmp_path / 'nodes.sqlite')
    checks = tmp_path / 'checks' / 'checks.sqlite'
    result = pool(database=scratch, output=checks.parent, checks_only=True)
    assert result['histories'] == 2
    assert report(checks)['coverage_complete']
    assert report(checks)['no_future']['history_draws'] == 1024
    # Same command resumes rather than checking again.
    result = pool(database=scratch, output=checks.parent, checks_only=True)
    assert result['histories'] == 2
    with pytest.raises(ValueError, match='duplicate checked range'):
        load_parts([checks, checks])

    def forbidden(*args, **kwargs):
        raise AssertionError('reduction must consume checks, not replay')

    monkeypatch.setattr('app.analysis.tree_history_checks.HistoryChecks', forbidden)
    out = tmp_path / 'reduced'
    pool(database=scratch, output=out, partial=True, node_catalog=tmp_path / 'nodes.sqlite',
         check_parts=[checks])
    gates = tree_parts.finalize(out / 'partial.sqlite', out)
    assert gates['no_future']['histories'] == 2
    assert gates['no_future']['failed_histories'] == 2  # unknown synthetic dispute stays a failure
    assert not gates['pass']
    with sqlite3.connect(scratch) as db:
        # A changed input range invalidates the check.
        db.execute('UPDATE paths SET identity=? WHERE id=1', ('changed',))
    with pytest.raises(ValueError, match='missing or changed checked range'):
        pool(database=scratch, output=tmp_path / 'changed', partial=True,
             node_catalog=tmp_path / 'nodes.sqlite', check_parts=[checks])
    with sqlite3.connect(checks) as db:
        db.execute('DELETE FROM ranges')
    with pytest.raises(ValueError, match='missing or changed checked range'):
        pool(database=scratch, output=tmp_path / 'missing', partial=True,
             node_catalog=tmp_path / 'nodes.sqlite', check_parts=[checks])


def test_wave_combine_requires_every_canonical_range(tmp_path):
    directory = piece(tmp_path, 'wave', [path('yes')] * 65)
    scratch = tmp_path / 'input.sqlite'
    with connect(scratch) as db:
        ingest(db, directory, str(directory))
    for name, selection in [('a', 'wave:0:64'), ('b', 'wave:64:65')]:
        pool(database=scratch, output=tmp_path / name, checks_only=True, history_ranges=[selection])
    with pytest.raises(ValueError, match='missing checked ranges'):
        tree_parts.combine(database=tmp_path / 'missing.sqlite', output=tmp_path / 'missing',
                           local=[tmp_path / 'a'], checks_only=True)
    result = tree_parts.combine(database=tmp_path / 'combined.sqlite', output=tmp_path / 'combined',
                                local=[tmp_path / 'a', tmp_path / 'b'], checks_only=True)
    assert result['coverage_complete']
    assert result['no_skip']['histories'] == 65
    assert result['no_future']['history_draws'] == 65 * 512
    with pytest.raises(ValueError, match='duplicate piece range'):
        tree_parts.combine(database=tmp_path / 'duplicate.sqlite', output=tmp_path / 'duplicate',
                           local=[tmp_path / 'a', tmp_path / 'a'], checks_only=True)
    assert json.loads((tmp_path / 'combined/checks.json').read_text()) == json.loads(json.dumps(result))


def test_check_stage_sizing_and_followup_need_no_node_union(tmp_path):
    from app.analysis.tree_pool_jobs import commands, read_remaining

    directory = piece(tmp_path, 'wave', [path('yes')] * 65)
    lines = commands(phase='checks', local=[directory], seconds_per_history=.3,
                     seconds_per_draw=.03)
    assert len(lines) == 1
    assert '--checks-only' in lines[0] and '--node-run' not in lines[0]
    scratch = tmp_path / 'input.sqlite'
    pool(database=scratch, output=tmp_path / 'checked', local=[directory], checks_only=True,
         history_ranges=['wave:0:64'])
    assert read_remaining(tmp_path / 'checked/checks.sqlite') == []
    followup = commands(phase='checks', local=[directory], remaining=['wave:64:65'])
    assert '--history-range wave:64:65' in followup[0]
    reductions = commands(phase='parts', local=[directory], node_run='nodes', check_runs=['checked'])
    assert '--check-run checked' in reductions[0]
