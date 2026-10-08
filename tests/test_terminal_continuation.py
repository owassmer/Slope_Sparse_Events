"""Leaf completion preserves financial arrays and before-answer records, including sliced rows."""
import numpy as np
import pytest
from benchmark_chronological import root
from test_chronological_walk import walker

from app.analysis.shadow import same
from app.disputes.forecast import _S, _Walk, as_of


@pytest.mark.parametrize("rows", [(145,), (145, 146)])
def test_leaf_completion_matches_replay(monkeypatch, rows):
    w = walker()
    steps, _, cls = root("saved")
    finish = w.terminal_trace
    checked = 0

    class Enough(Exception):
        pass

    def compare(s):
        nonlocal checked
        leaf = w._leaf[0]
        history = leaf.term_history
        actual = finish(s)
        expected = _Walk.terminal_trace(w, s)
        assert leaf.term_history == history
        for field in ("events", "cause", "marks", "day", "cash", "owed", "collateral"):
            assert same(getattr(actual, field), getattr(expected, field)), field
        # Raw snapshots may retain different future marks. Only as_of records
        # reach questions: a future stay must not become a present fact.
        assert same({i: as_of(q) for i, q in actual.questions.items()},
                    {i: as_of(q) for i, q in expected.questions.items()})
        checked += 1
        return actual

    emit = w.emit

    def emitted(s, outcome):
        emit(s, outcome)
        if checked == 24:
            raise Enough

    monkeypatch.setattr(w, "terminal_trace", compare)
    monkeypatch.setattr(w, "emit", emitted)
    with pytest.raises(Enough):
        w.run_from(_S(steps=steps, cls=cls, a4="seek", stayed=True, early=True),
                   np.isin(np.arange(w.fc.draws.n), rows))
    assert len(w.out) == checked == 24
    assert not hasattr(w, "_leaf")
