"""The parties named by their roles in what the judgment model reads (app/agent/roles.py): no declared name or
identifier is left anywhere in the judge's state, in any case or position; a role that opens a sentence is capitalised;
a possessive stays one; a case that declares no parties cannot be asked; and the page's state keeps the names."""
import asyncio

import pytest

from app.agent import roles
from app.agent.jev_profiles import DisputeProfile
from app.disputes.rules import load_model

PARTIES = load_model("akoustis_20240514")["case_parties"]


def test_no_declared_name_is_left_anywhere():
    state = {"case": {"company": "Akoustis Technologies, Inc.", "counterparty": "Qorvo, Inc.",
                      "obligation": "an award of damages, D. Del. 1:21-cv-01417"},
             "question": {"text": "Does Akoustis Technologies, Inc. pay Qorvo, Inc.?",
                          "answers": {"yes": "Akoustis pays."}},
             "historical_evidence": [{"text": "AKOUSTIS reported; Qorvo US, Inc. sued Akoustis' subsidiary "
                                              "Akoustis, Inc. (QRVO v. AKTS); see akoustis.com",
                                      "url": "https://www.sec.gov/Archives/akoustis/10q.htm"}]}
    out = roles.apply(state, PARTIES)
    assert roles.residue(out, PARTIES) == []
    assert out["question"]["text"] == "Does the Company pay the Claimant?"
    assert out["question"]["answers"]["yes"] == "The Company pays."
    assert "the Company's subsidiary" in out["historical_evidence"][0]["text"]
    assert out["case"]["obligation"] == "an award of damages, D. Del. [docket number]"


def test_names_inside_words_are_kept():
    assert roles.apply({"t": "Qorvonia and Akoustisian"}, PARTIES) == {"t": "Qorvonia and Akoustisian"}


def test_a_case_without_parties_cannot_be_asked():
    with pytest.raises(roles.NoParties):
        roles.apply({"t": "x"}, {})

    class Jev:
        async def judge(self, **k):
            raise AssertionError("asked")

    prof = DisputeProfile(Jev(), lambda kind, obj: None)
    with pytest.raises(roles.NoParties):
        asyncio.run(prof.forecast("forecast_claim_settlement_offer", {"case": {}}, ("d",)))


def test_the_judge_reads_roles_and_the_caller_keeps_names():
    seen = {}

    class Jev:
        async def judge(self, **k):
            seen.update(k)
            return type("C", (), {})(), [object()]

    state = {"case": {"company": "Akoustis Technologies, Inc."}, "question": {"text": "Does Akoustis file?"}}
    prof = DisputeProfile(Jev(), lambda kind, obj: None, parties=PARTIES)
    asyncio.run(prof.forecast("forecast_claim_settlement_offer", state, ("d",)))
    assert seen["state"]["question"]["text"] == "Does the Company file?"
    assert state["question"]["text"] == "Does Akoustis file?"
