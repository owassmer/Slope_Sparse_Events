"""Isolated correction for a later same-day appeal in reconstructed notes facts."""
from dataclasses import replace

import numpy as np

from app.analysis.events import BIG
from app.disputes import notes

original_decision_row = notes.decision_row


def decision_row(fc, d, steps, index, actor, mask=None):
    row, trace = original_decision_row(fc, d, steps, index, actor, mask)
    # The notes actor precedes this appeal in the recorded history. Filing stops
    # a later appeal on that same day; removing the filing for the question's
    # counterfactual must not make that later answer part of its conditioning.
    if any(step[0] == 'appeal' for step in steps[index + 1:]):
        marks = dict(row['marks'])
        marks['appealed'] = np.where(marks['appealed'] == row['day'], BIG, marks['appealed'])
        row = {**row, 'marks': marks}
        trace = replace(trace, marks=marks)
    return row, trace
