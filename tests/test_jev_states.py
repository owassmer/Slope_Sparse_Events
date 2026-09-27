"""What Jev reads: each question states the event the engine books, under the terms that govern it. No network: states
are built without any Jev call."""

import json

import pytest
from akoustis_fixture import REVIEW, SETUP, basis, judgment

from app.analysis import events
from app.disputes.forecast import TRIGGER_PHRASES, Forecaster

BORROWER = "Akoustis Technologies, Inc."


@pytest.fixture(scope="module")
def base():
    return basis()


def forecaster(base, disputes=()):
    return Forecaster(list(disputes), {}, borrower=BORROWER, review=REVIEW, horizon=SETUP.horizon,
                      hydrate=lambda f: {}, setup=SETUP, basis=base[1])


def test_the_holders_petition_is_asked_under_section_7_06_and_7_07(base):
    d = judgment()
    fc = forecaster(base, [d])
    st, _, _ = fc.state(fc.nodes[fc.node(d, "holders_involuntary", "judgment_I1")])
    law = " ".join(st["standard"])
    for quote in ("(a) the Holder of a Note gives to the Trustee written notice of a continuing Event of Default",
                  "(b) the Holders of at least 25% in Principal Amount of the then outstanding Notes make a written "
                  "request to the Trustee to pursue the remedy",
                  "indemnity reasonably satisfactory to the Trustee",
                  "(d) the Trustee does not comply with the request within 60 days",
                  "(e) during such 60-day period the Majority Holders do not give the Trustee a direction inconsistent",
                  "or to bring suit for the enforcement of any such payment", "11 U.S.C. §303(b)(1)"):
        assert quote in law, quote
    assert "§7.06(d)" in st["question"]["timing"] and "60 days" in st["question"]["timing"]


def test_every_dated_trigger_the_engine_computes_reads_in_plain_words():
    names = set(events.TRIGGERS) | {"holders_petition_earliest"}
    assert names <= set(TRIGGER_PHRASES)
    assert "§7.06" in TRIGGER_PHRASES["holders_petition_earliest"]
    assert not any(k in json.dumps(list(TRIGGER_PHRASES.values())) for k in ("_", "BIG"))
