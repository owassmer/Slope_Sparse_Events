"""The dated construction (QUESTIONS §1, What a question is conditioned on): a question's facts are the state on its
own date. On whole draws, every question occurrence's record and class are unchanged when every answer dated after
its day is withdrawn (the waiting answers booked after it, the court's answers given after it): nothing after a
question's date reaches it, on any path of the draw."""
import json
from pathlib import Path

import numpy as np
import pytest

from app.exact import case, pass1


@pytest.mark.parametrize("draw", [145, 300, 0])
def test_no_question_reads_an_answer_dated_after_it(draw):
    fc_all, d = case.forecaster()
    fc, _ = case.on_draws((draw,))
    ch0 = case.chain(fc, d)
    tk = pass1.case_tables(fc_all, d, ch0)
    forced_file = Path("var/waves/range1/forced.json")
    forced = set(json.loads(forced_file.read_text())) if forced_file.exists() else set()
    check = np.zeros(2, np.int64)
    pass1.explore(ch0, 0, tk, forced=forced, check=check)
    assert check[0] > 0
    assert check[1] == 0, f"{check[1]} of {check[0]} question records read an answer dated after the question"
