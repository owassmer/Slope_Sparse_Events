"""Chronological question records survive later answers without terminal replay."""
import numpy as np
import pytest
from benchmark_chronological import root
from test_chronological_walk import walker

from app.disputes.forecast import _S, _Walk, pack_row


def test_saved_questions_match_without_terminal_reconstruction(monkeypatch):
    w = walker()
    steps, native, cls = root("saved")
    w.fc.draws = w.fc.draws.sub(np.arange(w.fc.draws.n) == native)
    w.fc.draws.prefixes = {}
    terminal = w.terminal_questions
    count = 0

    class Enough(Exception):
        pass

    def compare(s, mask, tr):
        nonlocal count
        expected, actual = [], []
        with monkeypatch.context() as patch:
            patch.setattr(w.fc, "_keep_late", lambda k, p, r: expected.append((k, p, pack_row(r))))
            reference = _Walk.terminal_questions(w, s, mask, tr)

        def forbidden(*args, **kwargs):
            raise AssertionError("question reconstruction during finishing")

        with monkeypatch.context() as patch:
            patch.setattr(w.fc, "record_late", forbidden)
            patch.setattr("app.disputes.notes.record", forbidden)
            patch.setattr(w.fc, "_keep_late", lambda k, p, r: actual.append((k, p, pack_row(r))))
            got = terminal(s, mask, tr)
        assert set(actual) == set(expected)
        assert got.keys() == reference.keys()
        for key in got:
            np.testing.assert_array_equal(got[key], reference[key])
        count += 1
        if count == 12:
            raise Enough
        return got

    monkeypatch.setattr(w, "terminal_questions", compare)
    with pytest.raises(Enough):
        w.run_from(_S(steps=steps, cls=cls, a4="seek", stayed=True, early=True), np.ones(1, dtype=bool))
    assert count == 12
    assert w._dated_records == ()
    assert w._question_queue == ()


def test_registration_record_excludes_later_listing(monkeypatch):
    from pathlib import Path

    from app.analysis.build import basis_for, run_context
    from app.analysis.walk_measurement import LEAD_RUN
    from app.disputes.chronological import ChronologicalWalk
    from app.disputes.forecast import Forecaster
    from app.disputes.parallel import _variant

    ctx = run_context(LEAD_RUN, Path("runs/recorded"))
    setup, sens = _variant(ctx)

    def forbidden(*args, **kwargs):
        raise AssertionError("no judgment calls")

    fc = Forecaster(ctx["live"], ctx["findings"], borrower=ctx["borrower"], review=ctx["review"],
                    horizon=setup.horizon, hydrate=forbidden, setup=setup, basis=basis_for(ctx["feed"], setup),
                    slots=ctx["slots"], model=ctx["m"], sens=sens)
    d = next(d for d, _ in fc.ordered() if d.stage == "liability_pending" and d.borrower_role == "debtor")
    fc.draws = fc.draws.sub(np.arange(fc.draws.n) == 146)
    fc.draws.prefixes = {}
    w = ChronologicalWalk(fc, d)
    emit = w.emit
    seen, snapshots, listing_answers = [], [], set()

    class Enough(Exception):
        pass

    def emitted(s, outcome):
        if 85 <= len(w.out) <= 89:
            records = [(k, r) for k, _, r in w._dated_records if fc.nodes[k].node == "registration_early"]
            assert len(records) == 1
            key, row = records[0]
            snapshots.append(pack_row(row))
            listing_answers.add(next(step[2] for step in s.steps if step[0] == "listing"))
            assert row["day"].tolist() == [123]
            assert row["collateral"].tolist() == [544489152]
            # Rebuild only for the assertion, excluding the later listing answer.
            index = next(i for i, step in enumerate(s.steps) if step[0] == "registration_early")
            steps = tuple(step for step in s.steps if step[0] != "listing")
            fc.record_late(d, steps, ((key.split('|#', 1)[0], index),),
                           keep=lambda k, p, r: seen.append(r))
            assert seen[0]["collateral"].tolist() == row["collateral"].tolist()
            if len(w.out) == 89:
                raise Enough
        emit(s, outcome)

    monkeypatch.setattr(w, "emit", emitted)
    with pytest.raises(Enough):
        w.run()
    assert seen
    assert len(set(snapshots)) == 1
    assert listing_answers == {"compliant", "hearing", "suspended"}
