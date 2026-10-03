"""Reuse question calculations without dropping chronological dependencies."""
from unittest.mock import patch

import numpy as np
from test_decision_snapshots import AWARD, case  # noqa: F401, F811
from test_notes_decision import ORIGIN, STEPS

from app.analysis.events import event_trace
from tools.question_refresh import (
    Calculations,
    HistoryIndex,
    RefreshPlan,
    Results,
    decode_classes,
    rebind_population,
    run_batch,
)


def test_descendants_share_one_immediate_calculation(case, tmp_path):  # noqa: F811
    fc, dispute = case
    histories = HistoryIndex()
    prefix = AWARD + (('judgment_response', 'entry', 'none'),)
    requests = Calculations(tmp_path / 'questions.sqlite')
    try:
        uses = []
        for i in range(100):
            histories.add(prefix + (('later_context', str(i), 'no'),))
            uses.append(requests.add('prefix', histories.add(prefix)))
        assert len(set(uses)) == 1
        assert len(histories.parents) == 103  # root, two shared steps, 100 children
        with patch('app.analysis.events.event_trace', wraps=event_trace) as compute:
            results = list(requests.evaluate(fc, dispute, histories))
        assert compute.call_count == 1
        assert len(results) == 1
        assert not results[0][1]['sit']['offering_pending'].any()
    finally:
        requests.close()


def test_requests_rejoin_shared_prefixes_and_collapse_unused_identity(tmp_path):
    histories = HistoryIndex()
    a = histories.add((('first', '', ''), ('a', '', '')))
    b = histories.add((('second', '', ''), ('b', '', '')))
    c = histories.add((('first', '', ''), ('c', '', '')))
    requests = Calculations(tmp_path / 'questions.sqlite')
    try:
        ids = [requests.add('complete', h) for h in (a, b, c)]
        assert requests.add('complete', a, 7, 'unused') == ids[0]
        order = [r[2] for r in requests.requests(histories)]
        assert abs(order.index(a) - order.index(c)) == 1
        assert set(order) == {a, b, c}
    finally:
        requests.close()


def test_population_rebinding_preserves_nonlive_codes_and_other_decisions():
    from app.disputes.forecast import DisputePath, pack_mask, path_mask
    mask = np.array([True, False, True, True])
    entry = ('question', ('old',), np.array([0, -1, 0], np.int8).tobytes())
    other = ('other', ('a', 'b'), np.array([1, -1, 0], np.int8).tobytes())
    path = DisputePath('d', (('event', '', 'answer'),), 'done', (('question', 'answer'),),
                       mask=pack_mask(mask), classes=(entry, other))
    cls = decode_classes(entry, mask, 4)
    assert cls.tolist() == ['old', '', '', 'old']
    cls[0] = 'corrected'
    valid = np.array([True, False, True, False])
    got = rebind_population(path, {'question': cls}, valid, 4)
    assert got.steps == path.steps and got.edges == path.edges and got.outcome == path.outcome
    np.testing.assert_array_equal(path_mask(got, 4), valid)
    assert decode_classes(got.classes[0], valid, 4).tolist() == ['corrected', '', '', '']
    assert decode_classes(got.classes[1], valid, 4).tolist() == ['b', '', '', '']


def test_complete_histories_keep_later_traversed_earlier_events(case, tmp_path):  # noqa: F811
    fc, dispute = case
    histories = HistoryIndex()
    # The existing failure: a later-traversed settlement changes whether holders
    # can file at the earlier notes decision. The two requests must stay distinct.
    a = histories.add(STEPS[:ORIGIN + 1])
    b = histories.add(STEPS)
    requests = Calculations(tmp_path / 'questions.sqlite')
    try:
        early = requests.add('prefix', a)
        first = requests.add_notes(histories, a, ORIGIN, 'holders')
        second = requests.add_notes(histories, b, ORIGIN, 'holders')
        assert len({early, first, second}) == 3
        assert requests.add_notes(histories, b, ORIGIN, 'holders') == second
        node, context, _ = STEPS[ORIGIN]
        sibling = histories.replace_step(b, ORIGIN, (node, context, 'holders_file'))
        assert requests.add_notes(histories, sibling, ORIGIN, 'holders') == second
        results = dict(requests.evaluate(fc, dispute, histories))
        assert 0 <= results[first]['petition'][7] <= results[first]['day'][7]
        assert results[second]['day'][7] == 168
        assert results[second]['petition'][7] == -1
    finally:
        requests.close()


def test_saved_consumers_share_streamed_calculation_and_keep_before_answer_facts(case, tmp_path):  # noqa: F811
    from app.disputes.forecast import DisputePath, unpack_row

    fc, dispute = case
    key = fc.node(dispute, 'judgment_response', 'entry', branches=('pay', 'initiate_offering', 'file', 'none'))
    plan = RefreshPlan(tmp_path / 'plan.sqlite', dispute.instance_id)
    paths = [DisputePath(dispute.instance_id,
                        AWARD + (('judgment_response', 'entry', answer), ('listing', '', listing)),
                        'unresolved', ((key, 'none'),), classes=((key, ('old',), None),))
             for answer, listing in (('@0=none', 'compliant'), ('@2=none', 'suspended'))]
    try:
        for i, path in enumerate(paths):
            plan.add(fc, path, 'part', i)
        assert plan.write_batches(tmp_path / 'inputs', 1) == {'batches': 1, 'calculations': 1}
        with patch('app.analysis.events.event_trace', wraps=event_trace) as compute:
            run_batch(fc, tmp_path / 'inputs/0.pkl.gz', tmp_path / 'output.gz')
        assert compute.call_count == 1
        result = Results(plan.db)
        assert result.ingest(tmp_path / 'output.gz', dispute.instance_id) == 1
        assert result.ingest(tmp_path / 'output.gz', dispute.instance_id) == 1  # retry is idempotent
        for i, path in enumerate(paths):
            refreshed = result.rebind(fc, path, 'part', i)
            assert refreshed.edges == path.edges and refreshed.steps == path.steps
        facts = list(result.facts(fc))
        assert facts
        for _, _, blob in facts:
            row = unpack_row(blob)
            live = row['day'] < fc.days
            assert not row['sit']['offering_pending'][live].any()
            assert (row['sit']['listing'][live] == 'listed').all()
        # Assembly reads one shared registry without writing it, preserves the
        # saved financial identity, and returns only its own population support.
        import pickle

        from tools import question_refresh_assemble as assembly

        source = tmp_path / 'part0.pkl'
        source.write_bytes(pickle.dumps(paths))
        meta = [(i, 'equal-financial-arrays', source.name, i) for i in range(len(paths))]
        source.with_suffix('.meta').write_bytes(pickle.dumps(('original', (0, meta, set(), len(paths), 0))))
        # The source identities are authoritative, so bind these actual filenames.
        plan.db.execute("UPDATE consumers SET part='part0.pkl'")
        plan.db.commit()
        out = tmp_path / 'assembled'
        (out / 'walked').mkdir(parents=True)
        assembly._CONTEXT = fc, {dispute.instance_id: tmp_path / 'plan.sqlite'}, out
        try:
            checkpoint = assembly._part(source)
            report = pickle.loads(__import__('pathlib').Path(checkpoint).read_bytes())
            assert report['source_count'] == 2
            assert report['wanted'][dispute.instance_id]
            assert all(m[1][0] == 'equal-financial-arrays' for m in report['meta'])
        finally:
            assembly._CONTEXT = None
    finally:
        plan.close()


def test_shared_request_computes_union_of_saved_draws(case, tmp_path):  # noqa: F811
    import gzip
    import json
    import pickle

    from app.analysis.events import _same_state
    from app.disputes.forecast import DisputePath, pack_mask, unpack_row
    from tools.question_refresh import restrict_populations

    fc, dispute = case
    key = fc.node(dispute, 'judgment_response', 'entry', branches=('pay', 'initiate_offering', 'file', 'none'))
    directory = tmp_path / 'd'
    directory.mkdir()
    plan = RefreshPlan(directory / 'plan.sqlite', dispute.instance_id)
    masks = [np.arange(fc.draws.n) < 5, (np.arange(fc.draws.n) >= 3) & (np.arange(fc.draws.n) < 9)]
    paths = [DisputePath(dispute.instance_id, AWARD + (('judgment_response', 'entry', 'none'),),
                        'unresolved', ((key, 'none'),), mask=pack_mask(mask),
                        classes=((key, ('old',), None),)) for mask in masks]
    try:
        for i, path in enumerate(paths):
            plan.add(fc, path, 'part0.pkl', i)
        plan.db.commit()
        plan.write_batches(directory / 'inputs')
        source = directory / 'inputs/0.pkl.gz'
        baseline = directory / 'baseline.gz'
        run_batch(fc, source, baseline)
        walked = tmp_path / 'walked'
        walked.mkdir()
        (walked / 'part0.pkl').write_bytes(pickle.dumps(paths))
        control = tmp_path / 'control.pkl'
        control.write_bytes(pickle.dumps({'walked': len(paths)}))
        (tmp_path / 'prepared.json').write_text(json.dumps(
            {'histories': len(paths), 'instances': {dispute.instance_id: {'folder': 'd'}}}))
        report = restrict_populations(control, walked, tmp_path)
        assert report['requests'] == 1 and report['requested_draws'] == 9
        union = masks[0] | masks[1]
        output = directory / 'masked.gz'
        with patch('app.analysis.events.event_trace', wraps=event_trace) as compute:
            run_batch(fc, source, output)
        assert compute.call_count == 1
        assert all(np.array_equal(mask, union) for mask in compute.call_args.kwargs['rows'])

        def records(path):
            with gzip.open(path, 'rb') as fh:
                pickle.load(fh)
                return pickle.load(fh)[1]

        full, restricted = records(baseline), records(output)
        for a, b in zip(full, restricted, strict=True):
            assert a[:3] == b[:3]
            # Compact storage of the masked result equals the same population
            # selected from the full computation, including every situation field.
            from app.analysis.events import BIG
            from app.disputes.forecast import pack_row
            row = unpack_row(a[3])
            expected = unpack_row(pack_row({**row, 'day': np.where(union, row['day'], BIG)}))
            assert _same_state(expected, unpack_row(b[3]))
            np.testing.assert_array_equal(decode_classes(a[4], None, fc.draws.n)[union],
                                          decode_classes(b[4], None, fc.draws.n)[union])
    finally:
        plan.close()
