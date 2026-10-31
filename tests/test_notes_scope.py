from types import SimpleNamespace

from app.disputes.forecast import composite
from tools.notes_scope import reason


def test_changed_continuations_include_locally_merged_alternatives():
    def path(steps, edges=()):
        return SimpleNamespace(steps=steps, edges=edges)

    assert reason(path((('delisting_notes', 'delisted_suspension', 'petition_delist'),))) == 'delisting_filing'
    assert reason(path((('judgment_default', 'I1', 'holders_file'),))) == 'notes_filing'
    assert reason(path((('judgment_response', 'ripe', '@2=file'),))) == 'ripe_filing'
    key = 'dispute_002:holders_involuntary|judgment_I1|motions_pending'
    merged = composite([[(key, 'yes')], [(key, 'no')]])
    steps = (('judgment_default', 'I1', 'accelerated'),)
    assert reason(path(steps, ((merged, 'yes'),))) == 'merged_filing'
    unrelated = composite([[(key.replace('judgment_I1', 'judgment_ruling'), 'yes')]])
    assert reason(path(steps, ((unrelated, 'yes'),))) is None
    assert reason(path((('nonpayment', '', 'yes'),))) is None
    assert reason(path((('judgment_response', 'entry', '@2=file'),))) is None


def test_adoption_covers_corrected_tree_and_rejects_incomplete_scope():
    import pytest

    from tools.notes_adoption import plan

    reports = [{'job': i, 'flags': [], 'counts': []} for i in range(100)]
    reports[0].update(flags=[((2, 0, 1), 'notes_filing')], counts=[((2, 0, 1), 1)])
    old = [((1, 0, 0), ('safe',)), ((2, 0, 1), ('changed',)), ((3, 0, 2), ('removed',))]
    new = [((9, 0, 0), ('safe',)), ((10, 0, 1), ('changed',)), ((11, 0, 2), ('new',))]
    p = plan(old, new, reports, [(10, 0, 1)])
    assert p['reuse'] == [((1, 0, 0), (9, 0, 0))]
    assert p['replace'] == [(10, 0, 1), (11, 0, 2)]
    assert p['remaining'] == [(11, 0, 2)]
    assert p['top_events'] == 'corrected_only'
    with pytest.raises(ValueError, match='100-source'):
        plan(old, new, reports[:-1], [])
