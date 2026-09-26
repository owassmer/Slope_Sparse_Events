"""Whitelist projection of the mission: the only mission view the investigating agent receives.

get_mission builds a fresh object from `mission_projection.allowed_*_fields`. It never serializes
the mission dictionary or agent configuration wholesale; extra keys are denied by default.
"""

from __future__ import annotations

from typing import Any

from app.config import ConfigurationError, agent_config

MISSION_KEYS = {"akoustis_qorvo_2024": "lead_mission", "synergy_chc_2024": "default_mission",
                "barfresh_schreiber_2024": "transfer_mission"}


def project_mission(case_id: str, run_inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = agent_config()
    projection = cfg["mission_projection"]
    if case_id not in MISSION_KEYS:
        raise ConfigurationError(f"Unknown case {case_id!r}")
    template = cfg[MISSION_KEYS[case_id]]

    mission = {k: template[k] for k in projection["allowed_case_fields"] if k in template}
    run_inputs = run_inputs or {}
    extra = set(run_inputs) - set(projection["allowed_run_input_fields"])
    if extra:
        raise ConfigurationError(f"Run inputs outside the mission whitelist: {sorted(extra)}")
    if "mission_id" in run_inputs and run_inputs["mission_id"] != mission.get("mission_id"):
        raise ConfigurationError("Run input mission_id does not match the case template")
    # Missing run inputs stay explicitly unknown rather than absent or defaulted.
    for field in projection["allowed_run_input_fields"]:
        if field in run_inputs:
            mission[field] = run_inputs[field]
        else:
            mission.setdefault(field, {"status": "unknown", "reason": "not supplied in locked run inputs"})
    return mission
