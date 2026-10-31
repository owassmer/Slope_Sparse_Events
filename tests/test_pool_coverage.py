"""Missing pooled situations must not silently become an incomplete forecast."""
import gzip
import json
import pickle
from types import SimpleNamespace

import pytest

from app.disputes.pool import judge


def write_inputs(tmp_path, states, dead=()):
    control = tmp_path / 'control.pkl'
    control.write_bytes(pickle.dumps({
        'nodes': {k: SimpleNamespace(node='forecast') for k in ('parent', 'live', 'never')},
        'classed': {'parent'},
    }))
    with gzip.open(tmp_path / 'states0.json.gz', 'wt') as fh:
        json.dump({'states': states, 'errors': {}, 'never_live': list(dead)}, fh)
    return str(control)


def test_complete_pool_excludes_classed_parents_and_accepts_dead_classes(tmp_path):
    control = write_inputs(tmp_path, {'live': {}}, dead=['never'])
    judge('fixture', str(tmp_path), control, count_only=True)


@pytest.mark.parametrize(('states', 'dead', 'reason'), [
    ({'live': {}}, [], '1 missing'),
    ({'live': {}, 'parent': {}}, ['never'], '1 unexpected'),
    ({'live': {}}, ['live', 'never'], '1 both live and dead'),
])
def test_invalid_coverage_stops_before_provider(tmp_path, states, dead, reason):
    control = write_inputs(tmp_path, states, dead)
    with pytest.raises(SystemExit, match=reason):
        judge('fixture', str(tmp_path), control)


def test_conflicting_duplicate_state_stops_before_provider(tmp_path):
    control = write_inputs(tmp_path, {'live': {'state': 'first'}}, dead=['never'])
    with gzip.open(tmp_path / 'states1.json.gz', 'wt') as fh:
        json.dump({'states': {'live': {'state': 'different'}}, 'errors': {}}, fh)
    with pytest.raises(SystemExit, match='conflicting question state'):
        judge('fixture', str(tmp_path), control)
