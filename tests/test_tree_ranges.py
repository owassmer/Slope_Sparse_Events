"""Canonical arithmetic, sampling and coverage survive split/merged reductions."""
import gzip

import pytest
from test_tree_pool import piece

from app.analysis.tree_parts import combine, finalize, summarize
from app.analysis.tree_pool import connect, ingest
from app.disputes.forecast import DisputePath


def test_ranges_equal_whole_and_reject_missing_duplicate(tmp_path):
    paths = [DisputePath('d', (('choice', str(i), 'yes'),), 'done', (('q', 'yes'),))
             for i in range(193)]
    source = piece(tmp_path, 'large', paths)
    with connect(tmp_path / 'input.sqlite') as db:
        ingest(db, source, str(source))
        summarize(db, tmp_path / 'whole/partial.sqlite', gates_only=True)
        for i, (lo, hi) in enumerate([(0, 64), (64, 128), (128, 193)]):
            summarize(db, tmp_path / f'part-{i}/partial.sqlite', gates_only=True,
                      history_ranges=[f'large:{lo}:{hi}'])
        with pytest.raises(ValueError, match='duplicate history range'):
            summarize(db, tmp_path / 'overlap/partial.sqlite', gates_only=True,
                      history_ranges=['large:0:128', 'large:64:193'])
        with pytest.raises(ValueError, match='boundaries'):
            summarize(db, tmp_path / 'unaligned/partial.sqlite', history_ranges=['large:1:193'])
    whole = finalize(tmp_path / 'whole/partial.sqlite', tmp_path / 'whole')
    parts = [tmp_path / f'part-{i}' for i in (2, 0, 1)]
    combine(database=tmp_path / 'tier.sqlite', output=tmp_path / 'tier', local=parts[:2], merge_only=True)
    split = combine(database=tmp_path / 'combined.sqlite', output=tmp_path / 'combined',
                    local=[tmp_path / 'tier', parts[2]])
    assert split == whole
    for name in ('histories.jsonl.gz', 'catalog.jsonl.gz'):
        assert gzip.decompress((tmp_path / 'whole' / name).read_bytes()) == gzip.decompress(
            (tmp_path / 'combined' / name).read_bytes())
    with pytest.raises(ValueError, match='duplicate piece range'):
        combine(database=tmp_path / 'duplicate.sqlite', output=tmp_path / 'duplicate', local=parts + parts[:1])
    with pytest.raises(ValueError, match='missing or invalid piece range'):
        combine(database=tmp_path / 'missing.sqlite', output=tmp_path / 'missing', local=parts[:2])
