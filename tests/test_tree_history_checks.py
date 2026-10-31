"""Replay failures survive reduction, including failures outside the first part."""
import json
from dataclasses import replace

import numpy as np
import pytest
from question_history import question_records
from test_question_futures import sample

from app.analysis.piece_store import PieceWriter
from app.analysis.tree_history_checks import HistoryChecks
from app.analysis.tree_parts import combine, summarize
from app.analysis.tree_pool import connect, ingest
from app.disputes.forecast import _S, _Walk, pack_mask, pack_row, unpack_row


@pytest.mark.parametrize('gate', ['no_future', 'no_skip'])
def test_later_part_replay_failure_reaches_combined_verdict(tmp_path, monkeypatch, gate):
    fc, d, _ = sample(row=None)
    walk = _Walk(fc, d)
    state = _S()
    with question_records(walk) as records:
        key = walk.node('settlement_offer', 'I0', s=state, probe=('settle', 'I0', 'no'))
        walk.emit(walk.take(state, ('settle', 'I0', 'yes'), (key, 'yes'), (key,)), 'settled')
    path = replace(walk.out[0], mask=pack_mask(np.arange(512) == 0))
    # This truncated history omits the later listing decision: an actual no-skip violation.
    # The future-fact variant additionally plants post-answer cash in the earlier question.
    if gate == 'no_future':
        for ident, blobs in records.items():
            changed = []
            for blob in blobs:
                row = unpack_row(blob)
                row['cash'] = row['cash'] + 123456789
                changed.append(pack_row(row))
            records[ident] = changed
    monkeypatch.setattr(HistoryChecks, 'forecaster', staticmethod(lambda: fc))
    for i in range(2):
        folder = tmp_path / f'input-{i}'
        folder.mkdir()
        writer = PieceWriter(folder, 512)
        writer.put('nodes', fc.nodes)
        if i:
            for (key, prefix, completed), blobs in records.items():
                assert completed is None
                writer.put('questions', (prefix, [(key, blob) for blob in blobs]))
            writer.put('path', (path, None))
        writer.put('classification', {})
        writer.close()
        (folder / 'piece.json').write_text(json.dumps(dict(
            partition=i, partitions=2, depth=6, input_routes=[[]], remaining=[], complete=True,
            histories=i, history_draws=i)))
        with connect(tmp_path / f'input-{i}.sqlite') as db:
            ingest(db, folder, str(folder))
            summarize(db, tmp_path / f'part-{i}/partial.sqlite', gates_only=True)
    first = combine(database=tmp_path / 'first.sqlite', output=tmp_path / 'first',
                    local=[tmp_path / 'part-0'])
    assert first[gate]['pass']
    result = combine(database=tmp_path / 'combined.sqlite', output=tmp_path / 'combined',
                     local=[tmp_path / 'part-0', tmp_path / 'part-1'])
    assert not result['pass']
    assert not result[gate]['pass']
    assert result[gate]['histories'] == result['histories'] == 1
    assert result[gate]['history_draws'] == 1
    assert result[gate]['failed_histories'] == 1
    assert result[gate]['examples']
    assert result['independent_replay']['histories'] == 1
