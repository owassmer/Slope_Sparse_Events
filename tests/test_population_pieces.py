"""A bounded piece must account for every unfinished branch, never a DFS truncation."""
import pytest

from app.analysis.population_walk import PieceWalk, share_routes, walk_piece
from app.disputes.chronological import ChronologicalWalk


@pytest.mark.parametrize('share', ['0/3', '4/3', '1/0', '1', 'a/2', '1/2/3'])
def test_invalid_share(share):
    with pytest.raises(ValueError, match='share must'):
        share_routes([()], share)


def test_share_requires_resume(tmp_path):
    with pytest.raises(ValueError, match='share requires resume'):
        walk_piece(output=tmp_path / 'unused', share='1/2')
    assert not (tmp_path / 'unused').exists()


def test_shares_cover_uneven_antichain_and_allow_empty_shares():
    routes = [(i,) for i in range(5)]
    for k in (1, 3, 8):
        shares = [share_routes(routes, f'{i}/{k}') for i in range(1, k + 1)]
        assert sorted(r for share in shares for r in share) == routes
        assert max(map(len, shares)) - min(map(len, shares)) <= 1


def test_pieces_cover_tree_once(monkeypatch):
    def tree(self, path):
        if len(path) == 4 or path == (1,):
            if self.owns(self.stack[-1][0]):
                emitted.append(path)
        else:
            for i in range(3):
                self._loop(path + (i,))

    monkeypatch.setattr(ChronologicalWalk, '_loop', tree)
    expected = [(1,)] + [(a, b, c, d) for a in (0, 2) for b in range(3) for c in range(3) for d in range(3)]
    emitted = []
    for partition in range(7):
        routes = [()]
        while routes:
            w = object.__new__(PieceWalk)
            w._watch = []
            # Each slice can replay its ancestors and make at least one new step.
            w.configure(routes=routes, partition=partition, partitions=7, depth=2,
                        stop=lambda w=w: w.visited >= 9)
            w._loop(())
            routes = w.remaining
    assert sorted(emitted) == sorted(expected)


def test_stop_above_resume_routes_does_not_reopen_completed_siblings():
    w = object.__new__(PieceWalk)
    w._watch = []
    w.configure(routes=[(1, 2), (2, 1)], stop=lambda: True)
    w._loop(None)
    assert w.remaining == [(1, 2), (2, 1)]


def test_replayed_ancestor_does_not_emit_its_completed_draws(monkeypatch):
    w = object.__new__(PieceWalk)
    w.configure(routes=[(1, 2)])
    got = []
    monkeypatch.setattr(ChronologicalWalk, 'emit', lambda *args: got.append(args))
    w.stack = [[(1,), 3]]
    w.emit(None, 'finished on other draws')
    assert not got
    w.stack = [[(1, 2), 3]]
    w.emit(None, 'owned')
    assert len(got) == 1


def test_native_restart_preserves_histories_and_financial_keys():
    import numpy as np
    from test_chronological_walk import walker

    from app.disputes.state_graph import freeze

    def run(routes=((),), cut=None, watches=None):
        base = walker()
        base.fc.draws = base.fc.draws.sub(np.isin(np.arange(512), [145, 146]))
        base.fc.draws.prefixes = {}
        w = PieceWalk(base.fc, base.d)

        def stop():
            route = w.stack[-1][0] + (w.stack[-1][1] - 1,) if w.stack else ()
            return (w.visited >= 100 and bool(w.watch_results)) if cut is None else route in cut

        w.configure(routes=routes, stop=stop, watch_results=watches)
        w.run()
        return w.remaining, [freeze((p, k)) for p, k in zip(w.out, w.keys, strict=True)], w.watch_results

    pending, first, watches = run()
    assert watches  # exercise a completed no-event subtree, not only settlement prefixes
    remaining, second, _ = run(pending, watches=watches)
    _, combined, _ = run(cut=set(remaining))
    assert first and second
    assert not set(first).intersection(second)
    from collections import Counter

    assert Counter(first + second) == Counter(combined)

    shared = []
    for i in range(1, 4):
        rest, paths, _ = run(share_routes(pending, f'{i}/3'), cut=set(remaining), watches=watches)
        assert sorted(rest) == sorted(r for r in remaining
                                     if any(r[:len(a)] == a for a in share_routes(pending, f'{i}/3')))
        shared.extend(paths)
    assert Counter(shared) == Counter(second)
