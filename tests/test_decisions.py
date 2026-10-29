"""The Decisions API transport (app/agent/decisions.py): the judge's typed questions become Decisions questions with
the same options, and its answers come back in the shape the Jev adapter reads; a refusal is an unknown, never a
probability of zero."""
import pytest
from typesafe_sdk import Choice, Noul

from app.agent.decisions import _answer, _question
from app.disputes.forecast import answer_distribution
from app.domain.investigation import SemanticObservation


def test_questions_keep_their_options():
    c = _question("q", Choice(instructions="Which?", criteria={"pay": "Pays.", "file": "Files."}).model_dump(mode="json"))
    assert c == {"type": "choice", "name": "q", "instructions": "Which?",
                 "choices": [{"value": "pay", "description": "Pays."}, {"value": "file", "description": "Files."}]}
    p = _question("n", Noul(instructions="Does it?", criteria={"true": "It does.", "false": "It does not."})
                  .model_dump(mode="json"))
    assert p["type"] == "predicate" and "True: It does." in p["instructions"] and "False: It does not." in p["instructions"]


def test_answers_in_the_adapters_shape():
    assert _answer({"type": "choice", "choice": "pay", "confidence": 0.4,
                    "probabilities": [{"value": "pay", "probability": 0.7}, {"value": "file", "probability": 0.3}]}) \
        == {"choice": "pay", "confidence": 0.4, "probabilities": {"pay": 0.7, "file": 0.3}}
    assert _answer({"type": "predicate", "probability": 0.07}) == {"noul": 0.07}


def test_a_refusal_is_unknown_not_zero():
    ans = _answer({"type": "refusal", "name": "q"})
    o = SemanticObservation(observation_id="o", call_id="c", profile="event_forecast", question_id="q",
                            question_version="1", primitive="noul", answer=None, noul_value=ans.get("noul"))
    with pytest.raises(RuntimeError, match="no probability"):
        answer_distribution("k", ("yes", "no"), o)
