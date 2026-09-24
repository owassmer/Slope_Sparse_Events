"""Isolation: get_mission exposes only whitelisted fields and nothing evaluator-only or future."""

import json

import pytest

from app.agent.mission import MISSION_KEYS, project_mission
from app.config import CONTRACTS, ConfigurationError, agent_config


@pytest.mark.parametrize("case_id", sorted(MISSION_KEYS))
def test_projection_is_whitelist_only(case_id):
    projection = agent_config()["mission_projection"]
    mission = project_mission(case_id)
    allowed = set(projection["allowed_case_fields"]) | set(projection["allowed_run_input_fields"])
    assert set(mission) <= allowed
    assert not set(mission) & set(projection["excluded_fields"])

    text = json.dumps(mission)
    private = json.loads((CONTRACTS / "case_eval_private.json").read_text())
    # Later counterparty identity and answer-bearing source IDs must not leak.
    for forbidden in ["Vitabest", "case_eval_private", *private["cases"].get(case_id, {}).get("outcome_source_ids", [])]:
        assert forbidden not in text


def test_unknown_run_inputs_stay_typed_unknown_and_extras_rejected():
    mission = project_mission("synergy_chc_2024")
    assert mission["requested_amount"]["status"] == "unknown"
    assert mission["mission_id"] == "synergy_chc_2024_working_capital_advance"
    with pytest.raises(ConfigurationError):
        project_mission("synergy_chc_2024", {"expected_findings_and_error_checks": ["x"]})
    with pytest.raises(ConfigurationError):
        project_mission("synergy_chc_2024", {"mission_id": "barfresh_2024_inventory_advance"})
