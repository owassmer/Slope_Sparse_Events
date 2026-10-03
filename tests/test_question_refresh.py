"""Reuse question calculations without dropping chronological dependencies."""
from unittest.mock import patch

import numpy as np
from test_decision_snapshots import AWARD, case  # noqa: F401, F811
from test_notes_decision import ORIGIN, STEPS

from app.analysis.events import event_questions, event_trace
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
    import pytest

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
        with patch('app.analysis.events.event_questions', wraps=event_questions) as compute:
            run_batch(fc, tmp_path / 'inputs/0.pkl.gz', tmp_path / 'output.gz')
        assert compute.call_count == 1
        result = Results(plan.db)
        import gzip
        import pickle

        with gzip.open(tmp_path / 'output.gz', 'rb') as original:
            header, records = pickle.load(original), pickle.load(original)
        with gzip.open(tmp_path / 'truncated.gz', 'wb') as truncated:
            pickle.dump(header, truncated)
            pickle.dump(records, truncated)
        with pytest.raises(EOFError):
            result.ingest(tmp_path / 'truncated.gz', dispute.instance_id)
        assert plan.db.execute('SELECT COUNT(*) FROM results').fetchone()[0] == 0
        with pytest.raises(ValueError, match='Incomplete refresh'):
            result.require_complete(1)
        assert result.ingest(tmp_path / 'output.gz', dispute.instance_id) == 1
        assert result.ingest(tmp_path / 'output.gz', dispute.instance_id) == 1  # retry is idempotent
        result.require_complete(1)
        # Equal counts cannot conceal an unassigned completion replacing a missing one.
        plan.db.execute('UPDATE finished_requests SET request=-1')
        with pytest.raises(ValueError, match='Incomplete refresh'):
            result.require_complete(1)
        plan.db.rollback()
        result.require_complete(1)
        for i, path in enumerate(paths):
            refreshed = result.rebind(fc, path, 'part', i)
            assert refreshed.edges == path.edges and refreshed.steps == path.steps
        from app.analysis.events import BIG
        from app.disputes.forecast import lazy_row, pack_row

        get = result.get

        def inactive(binding, n):
            key, row, cls = get(binding, n)
            row = {**row, 'day': np.full(n, BIG)}
            row = lazy_row(pack_row(row))
            assert row['groups'] is None
            return key, row, cls

        with patch.object(result, 'get', side_effect=inactive):
            unchanged = result.rebind(fc, paths[0], 'part', 0)
        assert unchanged.steps == paths[0].steps
        assert unchanged.edges == paths[0].edges
        assert unchanged.classes == paths[0].classes
        facts = list(result.facts(fc))
        from tools.question_refresh_assemble import parallel_facts
        plan.db.commit()
        assert list(parallel_facts(fc, {dispute.instance_id: result}, tmp_path, 2)) == facts
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
            # Subdivision must preserve histories and OR the same shared supports.
            from tools.question_assembly_fleet import bundles, rebind_bundle
            parts = list(bundles(source, {dispute.instance_id: plan.db}, max_histories=1))
            assert [b['start'] for b in parts] == [0, 1]
            union, rebuilt = {}, []
            for tag, bundle in enumerate(parts):
                directory = tmp_path / f'bundle{tag}'
                directory.mkdir()
                from tools.question_assembly_stream import describe
                bundle.update(tag=str(tag), identity='stream-test')
                expected = describe(bundle, str(tag), 'input-digest', 'stream-test')
                assert expected['start'] == tag
                assert expected['count'] == 1
                bundle['input_digest'] = expected['input_digest']
                saved = rebind_bundle(fc, bundle, str(tag), directory)
                detail = pickle.loads(saved.read_bytes())
                assert detail['identity'] == 'stream-test'
                assert detail['input_digest'] == 'input-digest'
                rebuilt.extend(pickle.loads(saved.with_suffix('.pkl').read_bytes()))
                for binding, mask in detail['wanted'][dispute.instance_id]:
                    union[binding] = union.get(binding, 0) | int.from_bytes(mask, 'little')
            assert rebuilt == pickle.loads((out / 'walked' / source.name).read_bytes())
            assert union == {b: int.from_bytes(m, 'little') for b, m in report['wanted'][dispute.instance_id]}
            import tarfile

            from tools.question_assembly_fleet import validate_output
            detail.update(identity='preparation', input_digest='exact-input')
            saved.write_bytes(pickle.dumps(detail))
            archive = tmp_path / 'bundle-output.tgz'
            with tarfile.open(archive, 'w:gz') as tar:
                tar.add(saved, arcname=saved.name)
                tar.add(saved.with_suffix('.pkl'), arcname=saved.with_suffix('.pkl').name)
            task = {'tag': str(tag), 'source': source.name, 'start': 1,
                    'count': 1, 'input_digest': 'exact-input'}
            validate_output(archive, task, 'preparation')
            with pytest.raises(ValueError, match='preparation or coverage'):
                validate_output(archive, task, 'different-preparation')


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
        with patch('app.analysis.events.event_questions', wraps=event_questions) as compute:
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


def test_selected_question_records_equal_full_trace_without_expanding_history(case):  # noqa: F811
    from app.analysis.events import _same_state
    from app.disputes.forecast import DisputePath

    fc, dispute = case
    mask = np.arange(fc.draws.n) % 7 == 0
    steps = STEPS + (('notes_due_date', 'holders', ''),)
    path = DisputePath(dispute.instance_id, steps, '', ())
    rows = tuple(mask for _ in steps)
    full = event_trace(dispute, path, fc.setup, fc.m, fc.draws, fc.sens, day_only=True, rows=rows)
    with patch('app.analysis.events._trace_rows', side_effect=AssertionError('widened the entire trace')):
        selected, dates = event_questions(dispute, path, fc.setup, fc.m, fc.draws, fc.sens,
                                          indices=(-1, ORIGIN), day_only=True, rows=rows)
    for index in (-1, ORIGIN):
        actual = index if index >= 0 else len(steps) + index
        assert _same_state(selected[index], full.questions[actual])
        np.testing.assert_array_equal(dates[index], full.day[actual])


def test_reused_classification_preserves_each_question_identity(case, tmp_path):  # noqa: F811
    import gzip
    import pickle

    from app.disputes.forecast import DisputePath, class_entry, pack_row
    from tools.question_refresh import classify_row

    fc, dispute = case
    steps = AWARD + (('judgment_response', 'entry', 'none'),)
    keys = [fc.node(dispute, 'judgment_response', 'entry', context,
                    branches=('pay', 'initiate_offering', 'file', 'none')) for context in ('final', 'appealed')]
    row = event_trace(dispute, DisputePath(dispute.instance_id, steps, '', ()),
                      fc.setup, fc.m, fc.draws, fc.sens, day_only=True).questions[len(steps) - 1]
    expected = []
    for key in keys:
        enriched = []
        cls = classify_row(fc, dispute, key, steps, row,
                           lambda _k, r, enriched=enriched: enriched.append({**r, 'day': row['day']}))
        expected.append((pack_row(enriched[0] if enriched else row), class_entry(key, cls, None)))
    source, output = tmp_path / 'in.gz', tmp_path / 'out.gz'
    with gzip.open(source, 'wb') as fh:
        pickle.dump(dispute.instance_id, fh)
        pickle.dump((1, 'prefix', steps, -1, '', [(i, key, -1, i) for i, key in enumerate(keys)]), fh)
    with patch('tools.question_refresh.classify_row', wraps=classify_row) as classify:
        run_batch(fc, source, output)
    assert classify.call_count == 1
    with gzip.open(output, 'rb') as fh:
        pickle.load(fh)
        _, records = pickle.load(fh)
    assert [(r[3], r[4]) for r in records] == expected


def test_balance_uses_draw_work_and_preserves_shared_prefix_order(tmp_path):
    import gzip
    import json
    import pickle

    from tools.question_refresh import balance_batches

    root = tmp_path / 'd'
    (root / 'inputs').mkdir(parents=True)
    requests = [(i, 'complete', (('event', '', ''),) * length, 0, '', [], bytes([mask]))
                for i, length, mask in ((1, 2, 1), (2, 2, 1), (3, 4, 255), (4, 1, 1))]
    with gzip.open(root / 'inputs/0.pkl.gz', 'wb') as fh:
        pickle.dump('d', fh)
        for request in requests:
            pickle.dump(request, fh)
    (tmp_path / 'prepared.json').write_text(json.dumps({'instances': {'d': {'folder': 'd'}}}))
    (tmp_path / 'populations.json').write_text('{}')
    costs = balance_batches(tmp_path, max_requests=3, work_limit=10)
    assert list(costs.values()) == [4, 32, 1]  # indivisible heavy request stays alone
    got = []
    for source in sorted((root / 'balanced').glob('*.pkl.gz')):
        with gzip.open(source, 'rb') as fh:
            assert pickle.load(fh) == 'd'
            while True:
                try:
                    got.append(pickle.load(fh))
                except EOFError:
                    break
    assert got == requests


def test_prefix_cache_reuses_superset_draws_without_replaying_events(case):  # noqa: F811
    from app.analysis.events import Chain, _same_state
    from app.disputes.forecast import DisputePath, pack_row

    fc, dispute = case
    path = DisputePath(dispute.instance_id, STEPS, '', ())
    masks = [np.ones(fc.draws.n, dtype=bool), np.arange(fc.draws.n) % 2 == 0,
             np.arange(fc.draws.n) % 2 == 1]
    expected = []
    for mask in masks:
        fc.draws.prefixes = None
        expected.append(event_trace(dispute, path, fc.setup, fc.m, fc.draws, fc.sens,
                                    rows=tuple(mask for _ in STEPS)))
    fc.draws.prefixes = {}
    calls = []
    advance = Chain.advance

    def counted(self, *args):
        calls.append(args)
        return advance(self, *args)

    with patch.object(Chain, 'advance', counted):
        for i, mask in enumerate(masks):
            before = len(calls)
            actual = event_trace(dispute, path, fc.setup, fc.m, fc.draws, fc.sens,
                                 rows=tuple(mask for _ in STEPS))
            if i:
                assert len(calls) == before  # both disjoint subsets reuse the full cached prefix
            # The existing row writer removes offerings absent on every retained
            # draw. A sliced superset may still contain those empty array slots.
            assert actual.questions.keys() == expected[i].questions.keys()
            for index, row in actual.questions.items():
                assert pack_row(row) == pack_row(expected[i].questions[index])
            for field in ('day', 'cash', 'owed', 'collateral', 'groups'):
                assert _same_state(getattr(actual, field), getattr(expected[i], field)), field
            for field in ('cash', 'lock', 'capacity', 'petition', 'kinds', 'incurred', 'proceeds'):
                assert _same_state(getattr(actual.events, field), getattr(expected[i].events, field)), field


def test_prefix_cache_does_not_reuse_missing_draws(case):  # noqa: F811
    from app.analysis.events import Chain, _same_state
    from app.disputes.forecast import DisputePath

    fc, dispute = case
    path = DisputePath(dispute.instance_id, STEPS, '', ())
    left = np.arange(fc.draws.n) % 2 == 0
    right = ~left
    fc.draws.prefixes = {}
    event_trace(dispute, path, fc.setup, fc.m, fc.draws, fc.sens, rows=tuple(left for _ in STEPS))
    calls = []
    advance = Chain.advance

    def counted(self, *args):
        calls.append(args)
        return advance(self, *args)

    with patch.object(Chain, 'advance', counted):
        actual = event_trace(dispute, path, fc.setup, fc.m, fc.draws, fc.sens,
                             rows=tuple(right for _ in STEPS))
    assert len(calls) == len(STEPS)
    fc.draws.prefixes = None
    expected = event_trace(dispute, path, fc.setup, fc.m, fc.draws, fc.sens,
                           rows=tuple(right for _ in STEPS))
    assert _same_state(actual.questions, expected.questions)
    np.testing.assert_array_equal(actual.events.cash, expected.events.cash)


def test_superset_prefix_can_continue_on_new_answer(case):  # noqa: F811
    from app.analysis.events import Chain, _same_state
    from app.disputes.forecast import DisputePath, pack_row

    fc, dispute = case
    prefix = AWARD + (('post_trial_motions', '', 'yes'),)
    steps = prefix + (('stay', 'I1', 'yes'),)
    mask = np.arange(fc.draws.n) % 3 == 0
    fc.draws.prefixes = None
    expected = event_trace(dispute, DisputePath(dispute.instance_id, steps, '', ()),
                           fc.setup, fc.m, fc.draws, fc.sens, rows=tuple(mask for _ in steps))
    fc.draws.prefixes = {}
    event_trace(dispute, DisputePath(dispute.instance_id, prefix, '', ()),
                fc.setup, fc.m, fc.draws, fc.sens)
    calls = []
    advance = Chain.advance

    def counted(self, *args):
        calls.append(args)
        return advance(self, *args)

    with patch.object(Chain, 'advance', counted):
        actual = event_trace(dispute, DisputePath(dispute.instance_id, steps, '', ()),
                             fc.setup, fc.m, fc.draws, fc.sens, rows=tuple(mask for _ in steps))
    assert len(calls) == 1
    assert {i: pack_row(r) for i, r in actual.questions.items()} == {
        i: pack_row(r) for i, r in expected.questions.items()}
    for field in ('cash', 'lock', 'capacity', 'petition', 'kinds', 'incurred', 'proceeds'):
        assert _same_state(getattr(actual.events, field), getattr(expected.events, field)), field


def test_selected_capture_does_not_build_unrequested_snapshots(case):  # noqa: F811
    from app.analysis.events import Chain
    from app.disputes.forecast import DisputePath, pack_row

    fc, dispute = case
    steps = AWARD + (('post_trial_motions', '', 'yes'), ('stay', 'I1', 'yes'))
    path = DisputePath(dispute.instance_id, steps, '', ())
    expected = event_trace(dispute, path, fc.setup, fc.m, fc.draws, fc.sens).questions[0]
    calls = []
    question_row = Chain.question_row

    def counted(self, *args):
        calls.append(args)
        return question_row(self, *args)

    fc.draws.prefixes = {}
    with patch.object(Chain, 'question_row', counted):
        actual, _ = event_questions(dispute, path, fc.setup, fc.m, fc.draws, fc.sens, indices=(0,))
    assert len(calls) == 1
    assert pack_row(actual[0]) == pack_row(expected)


def test_selected_capture_cache_recovers_newly_requested_earlier_questions(case):  # noqa: F811
    from app.disputes.forecast import DisputePath, pack_row

    fc, dispute = case
    path = DisputePath(dispute.instance_id, STEPS, '', ())
    mask = np.arange(fc.draws.n) % 8 == 0
    rows = tuple(mask for _ in STEPS)
    fc.draws.prefixes = None
    expected = event_trace(dispute, path, fc.setup, fc.m, fc.draws, fc.sens, rows=rows)
    fc.draws.prefixes = {}
    for selected in ((13,), (0,), (20,), (13, 20), (0, 13, 20)):
        actual, _ = event_questions(dispute, path, fc.setup, fc.m, fc.draws, fc.sens,
                                    indices=selected, rows=rows)
        assert {i: pack_row(row) for i, row in actual.items()} == {
            i: pack_row(expected.questions[i]) for i in selected if i in expected.questions}
