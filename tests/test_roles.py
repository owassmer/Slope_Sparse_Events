"""The parties named by their roles in what the judgment model reads (app/agent/roles.py): no declared name is left in
the judge's text, in any position or escaping; keys, numbers and other parties' names are kept; distinct parties keep
distinct roles, the longest name first across roles; a party's own defined term stays its own; the grammar around a
role holds; the question's own text is filled with roles; and a case that declares no parties cannot be asked, while
the caller keeps the names."""
import asyncio

import pytest

from app.agent import roles
from app.agent.jev import build_question, registry_question
from app.agent.jev_profiles import DisputeProfile
from app.disputes.rules import load_model

PARTIES = load_model("akoustis_20240514")["case_parties"]


def test_no_declared_name_is_left_anywhere():
    state = {"case": {"company": "Akoustis Technologies, Inc.", "counterparty": "Qorvo, Inc.",
                      "obligation": "an award of damages, D. Del. 1:21-cv-01417"},
             "question": {"text": "Does Akoustis Technologies, Inc. pay Qorvo, Inc.?",
                          "answers": {"yes": "Akoustis pays."}},
             "historical_evidence": [{"text": "\nAKOUSTIS reported;\n\"Akoustis\" said Corvo's claims fail. "
                                              "Qorvo US, Inc. sued the Akoustis defendants (QRVO v. AKTS); "
                                              "Jeff Shealy testified about XBAW and Cornell."}]}
    out = roles.apply(state, PARTIES)
    assert roles.residue(out, PARTIES) == []
    assert out["question"]["text"] == "Does the Company pay the Claimant?"
    assert out["question"]["answers"]["yes"] == "The Company pays."
    text = out["historical_evidence"][0]["text"]
    assert "the Company defendants" in text and "the the" not in text and '"The Company" said' in text
    assert out["case"]["obligation"] == "an award of damages, D. Del. [docket number]"


def test_only_text_changes():
    parties = [{"as": "the Company", "names": ["Cash", "1417"]}]
    assert roles.apply({"cash": 100, "Cash": 200, "amount": 1417, "flag": True}, parties) == \
        {"cash": 100, "Cash": 200, "amount": 1417, "flag": True}


def test_names_inside_words_are_kept_and_names_match_as_declared():
    parties = [{"as": "the Company", "names": ["Akoustis"]}]
    assert roles.apply({"t": "Akoustisian"}, parties) == {"t": "Akoustisian"}
    assert roles.apply("see https://www.akoustis.com", PARTIES) == "see https://www.the Company.com"


def test_distinct_parties_keep_distinct_roles():
    parties = [{"as": "the Company", "names": ["Alpha"]}, {"as": "the Claimant", "names": ["Alpha Holdings"]}]
    assert roles.apply("Alpha Holdings sued Alpha.", parties) == "The Claimant sued the Company."
    out = roles.apply("Akoustis' subsidiary Akoustis, Inc. was sued.", PARTIES)
    assert out == "The Company’s subsidiary the Company's subsidiary was sued."
    with pytest.raises(ValueError, match="two roles"):
        roles.Parties([{"as": "A", "names": ["X"]}, {"as": "B", "names": ["X"]}])


def test_a_partys_own_defined_term_stays_its_own():
    item = {"author": "Qorvo, Inc.", "quote": "The Company sued Akoustis. The Company expects to prevail."}
    assert roles.apply(item, PARTIES)["quote"] == "The Claimant sued the Company. The Claimant expects to prevail."
    titled = {"source": "Qorvo, Inc. Form 10-K", "quotes": ["The Company sued Akoustis."]}
    assert roles.apply(titled, PARTIES)["quotes"] == ["The Claimant sued the Company."]
    own = {"author": "Akoustis Technologies", "quote": "The Company had cash of $15.2 million."}
    assert roles.apply(own, PARTIES)["quote"] == "The Company had cash of $15.2 million."
    # a person speaking is not a party whose document defines the term
    said = {"author": "Jeff Shealy", "quotes": ["The Company had cash of $15.2 million."]}
    assert roles.apply(said, PARTIES)["quotes"] == ["The Company had cash of $15.2 million."]


def test_the_grammar_around_a_role():
    assert roles.apply("The borrower is Akoustis Technologies, Inc. It has no cash.", PARTIES) == \
        "The borrower is the Company. It has no cash."
    assert roles.apply("“Akoustis files.”", PARTIES) == "“The Company files.”"
    assert roles.apply("'Akoustis' files.", PARTIES) == "'The Company' files."
    assert roles.apply("Akoustis' U.S. subsidiary", PARTIES) == "The Company’s U.S. subsidiary"
    assert roles.apply("Akoustis,\nInc. was sued.", PARTIES) == "The Company's subsidiary was sued."
    assert roles.fill("{company} pays {claimant}.", PARTIES) == "The Company pays the Claimant."


def test_the_questions_own_text_is_filled_with_roles():
    q = build_question(registry_question("forecast_claim_registration_early"),
                       lambda t: roles.fill(t, PARTIES)).model_dump(mode="json")
    assert "{claimant}" not in q["instructions"] and "the Claimant" in q["instructions"]


def test_a_case_without_parties_cannot_be_asked():
    with pytest.raises(roles.NoParties):
        roles.apply({"t": "x"}, [])

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
    assert seen["fill"]("{company} and {claimant}") == "The Company and the Claimant"
    assert state["question"]["text"] == "Does Akoustis file?"


def test_every_shipped_case_declares_its_parties():
    import pathlib

    for case in sorted(p.name for p in pathlib.Path("cases").iterdir() if (p / "snapshot.json").exists()):
        roles.Parties(load_model(case)["case_parties"])
