"""Capture real callbacks across prerequisite alternatives without forcing ancestors."""
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from app.disputes.forecast import _S, _Walk
from tools.notes_resume import capture, capture_many


def test_multiple_boundaries_keep_state_support_and_earlier_terminals():
    walk = _Walk.__new__(_Walk)
    walk.fc = SimpleNamespace(_qcanon={}, _qcls={}, _rec_at=None)
    walk._watch = []
    support = np.array([True, False, True])
    walk.mask_of = lambda steps: support.copy()
    called, terminals = [], []

    def boundary(self, s) -> None:
        called.append(s.steps)

    def run(self):
        for answer in ('compliant', 'suspended'):
            self.fc._qcanon['listing'] = answer
            self.tail(_S(steps=(('listing', '', answer),)))
        self.tail(_S(steps=(('verdict', '', 'other'),)))
        self.emit(_S(steps=()), 'early')

    walk.emit = lambda state, outcome: terminals.append((state, outcome))
    with patch.object(_Walk, 'tail', boundary), patch.object(_Walk, 'run', run):
        found = capture_many(walk, 'tail',
                             compatible=lambda steps: all(s[0] == 'listing' for s in steps),
                             target=lambda steps: len(steps) == 1)
    assert not called
    assert len(found) == 2 and len(terminals) == 1
    assert [c.canonical['listing'] for c in found] == ['compliant', 'suspended']
    for c in found:
        np.testing.assert_array_equal(c.incoming_mask, support)
        c.function(walk, c.state, *c.args, **c.kwargs)
    assert called == [c.state.steps for c in found]


def test_exact_capture_retains_single_prefix_contract():
    walk = _Walk.__new__(_Walk)
    walk.fc = SimpleNamespace(_qcanon={}, _qcls={}, _rec_at=None)
    walk._watch = []
    walk.mask_of = lambda steps: None
    prefix = (('verdict', '', 'fixed'),)

    def boundary(self, s) -> None:
        raise AssertionError('Capture must stop before executing the boundary')

    def run(self):
        self.tail(_S(steps=prefix))
        self.tail(_S(steps=(('verdict', '', 'other'),)))

    with patch.object(_Walk, 'tail', boundary), patch.object(_Walk, 'run', run):
        found = capture(walk, prefix, 'tail')
    assert found.state.steps == prefix and found.incoming_mask is None
