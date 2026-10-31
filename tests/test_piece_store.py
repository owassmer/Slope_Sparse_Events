import numpy as np

from app.analysis.events import EventCash, Trace
from app.analysis.piece_store import PieceWriter, financial_rows, read_events, stored_events
from app.disputes.forecast import DisputePath, pack_mask
from app.disputes.state_graph import freeze


def test_compact_roundtrip_keeps_edges_support_and_distinct_question_situations(tmp_path):
    writer = PieceWriter(tmp_path, 4, full=True)
    mask = np.array([False, True, False, True])
    p = DisputePath('d', (('q', '', 'yes'),), 'paid', (('q', 'yes'),), mask=pack_mask(mask),
                    classes=(('q', ('a', 'b'), np.array([0, 1], dtype=np.int8).tobytes()),))
    records = [('questions', ((), [('q', b'first'), ('q', b'first')])),
               ('dated_question', ('q', (), p.steps, b'second')),
               ('dated_question', ('q', (), p.steps, b'first')),
               ('path', (p, ('financial-key',))), ('classification', {'classed': {'q'}})]
    for kind, payload in records:
        writer.put(kind, payload)
    writer.close()
    assert list(read_events(tmp_path / 'events.pkl')) == records
    assert list(read_events(tmp_path / 'events.pkl.gz')) == records
    stored = list(stored_events(tmp_path / 'events.pkl.gz'))
    assert sum(k == 'question_record' for k, _ in stored) == 2
    compact = next(v[0] for k, v in stored if k == 'path')
    assert len(compact.classes[0][2]) == 2


def test_financial_rows_preserve_every_event_array_and_native_draw_ids():
    ev = EventCash.zeros(4, 3, kinds=True)
    ev.cash[:] = np.arange(12).reshape(4, 3)
    ev.proceeds = {'offering_proceeds': np.arange(4)}
    mask = np.array([False, True, False, True])
    tr = Trace(ev, cause=np.arange(4), marks={'paid': np.arange(4) + 10})
    got = financial_rows(tr, mask, 4)
    assert got['rows'].tolist() == [1, 3]
    assert np.array_equal(got['events'].cash, ev.cash[mask])
    assert np.array_equal(got['events'].proceeds['offering_proceeds'], ev.proceeds['offering_proceeds'][mask])
    assert got['cause'].tolist() == [1, 3]
    assert got['marks']['paid'].tolist() == [11, 13]
    # The chronological leaf has row-scoped events but widened metadata.
    scoped = Trace(got['events'], cause=tr.cause, marks=tr.marks, rows=got['rows'])
    assert freeze(financial_rows(scoped, mask, 4)) == freeze(got)


def test_terminal_financial_support_mismatch_is_not_silently_filled():
    import pytest

    tr = Trace(EventCash.zeros(2, 3), rows=np.array([0, 1]))
    with pytest.raises(ValueError, match='support'):
        financial_rows(tr, np.array([False, True, False, True]), 4)
