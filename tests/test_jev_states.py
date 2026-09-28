"""What Jev reads: each question states the event the engine books, under the terms that govern it. No network: states
are built without any Jev call."""

import json

import pytest
from akoustis_fixture import REVIEW, SETUP, basis, judgment

from app.analysis import events
from app.disputes.forecast import TRIGGER_PHRASES, Forecaster, bank_state
from app.domain.investigation import Decisive, FactorResult

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


def test_the_bank_and_research_cash_floor_questions_differ_only_in_research_facts(base):
    d = judgment()
    fc = forecaster(base, [d])
    fc.bank_paths()
    probe = (("execute_pre_ruling", "I1", "no"), ("cash_floor", "", "no"))
    k = fc.node(d, "petition_cash_floor")
    fc.record((k,), fc.trace(d, probe))
    research, _, _ = fc.state(fc.nodes[k])
    bank = bank_state(fc, next(n for n in fc.bank_nodes.values() if n.node == "petition_cash_floor"))
    assert bank["standard"] == research["standard"] and "§362(a)" in bank["standard"][0]
    assert bank["question"]["timing"] == research["question"]["timing"]
    assert bank["case"]["analysis_period_ends"] == research["case"]["analysis_period_ends"] == "17 Dec 2024"
    coupon = "the notes' interest payment date"
    assert bank["path_facts"]["contract_dates"] == {coupon: research["path_facts"]["contract_dates"][coupon]}
    assert bank["path_facts"]["contract_dates"][coupon] == (
        "16 Dec 2024: $1,320,000.00 due, $750,000.00 of it paid in cash and $570,000.00 in shares")
    assert not bank["evidence"] and not bank["record_items"] and not bank["readings"]


def test_a_reading_travels_with_the_passage_it_was_taken_from(base):
    settle = FactorResult(factor_id="settlement_signals", label="Settlement signals", kind="graded", aggregate="latest",
                          distribution={"A party expresses general willingness to settle": 0.6,
                                        "The passage describes no settlement discussion": 0.4},
                          decisive=Decisive(finding_id="fnd_009", source_date="2024-05-23", quote="…settlement…"),
                          finding_ids=("fnd_009",))
    barred = FactorResult(factor_id="appeal_barred", label="An appeal of this obligation is waived or barred",
                          kind="present", aggregate="max", probability=0.07)  # taken from no passage
    d = judgment().model_copy(update={"factors": (settle, barred)})
    fc = Forecaster([d], {"fnd_009": "424B5"}, borrower=BORROWER, review=REVIEW, horizon=SETUP.horizon,
                    hydrate=lambda f: {"source": f"{f} prospectus supplement", "quotes": ["…settlement…"]},
                    setup=SETUP, basis=base[1])
    for node in ("settlement_offer", "settlement_accept"):
        st, fids, readings = fc.state(fc.nodes[fc.node(d, node, "I1", "entered")])
        assert "fnd_009" in fids and readings["Settlement signals"]["source"] == "424B5 prospectus supplement"
        ev = next(e for e in st["evidence"] if e["source"] == "424B5 prospectus supplement")
        item = "the company's statements on reaching a settlement with the judgment creditor"
        assert ev["supplies"] == [item] and ev["readings_taken_from_it"] == ["Settlement signals"]
        assert {"item": item, "in_the_record": True} in st["record_items"]
    st, _, readings = fc.state(fc.nodes[fc.node(d, "appeal", "post")])
    assert "An appeal of this obligation is waived or barred" not in readings and not st["evidence"]


def test_jev_reads_the_clocks_and_the_remittitur_scenario_as_what_they_are(base):
    from app.agent.jev import registry_question

    d = judgment()
    fc = forecaster(base, [d])
    k = fc.node(d, "settlement_offer", "I1", "entered")
    fc.record((k,), fc.trace(d, (("settle", "I1", "no"),)))
    dates = fc.state(fc.nodes[k])[0]["path_facts"]["contract_dates"]
    entered = ("the notes' judgment default (§7.01(i)) on the judgment as entered: 60 days after execution became "
               "available on 20 Jun 2024")
    assert dates[entered] == "19 Aug 2024"
    assert any(x.endswith("60 days after the court's order on the last pending post-trial motion") for x in dates)
    st = fc.state(fc.nodes[fc.node(d, "ts_damages_ruling")])[0]
    comp = next(c for c in st["path_facts"]["components"] if "remittitur_scenario" in c)
    assert comp["remittitur_scenario"].startswith("$23,100,000.00 (the model's remitted-amount scenario; basis: "
                                                  "Bennis's method with Irwin's revenue corrections")
    assert "D.I. 616-1" in comp["remittitur_scenario"] and "the most the evidence supports" not in comp["remittitur_scenario"]
    for q in ("forecast_ts_liability_jmol", "forecast_ts_damages_ruling", "forecast_trebling"):
        assert "merits or the remedy" not in registry_question(q)["prompt"]["instructions"]


def test_the_case_terms_and_drill_down_read_as_the_record_states_them(base):
    from app.analysis.page import case_terms, drill_down, label_head
    from app.disputes.rules import load_model

    d, m = judgment(), load_model()
    t = case_terms(d, REVIEW, SETUP.horizon, m, {})
    award = next(c for c in t["components"] if c["amount"] == "$31,315,215.00")
    assert award["source"] == d.order_reference != next(c.motion for c in d.components if c.status == "awarded"
                                                        and c.motion)  # the judgment, not a party's motion
    fees = "Attorneys' fees requested by Qorvo (DTSA / NCTSPA; alternatively UDTPA)"
    assert label_head(fees) == fees and label_head(fees + "; D.I. 612") == fees  # not cut inside the parentheses
    default = dict(next(n for n in t["notes"])["terms"])["Judgment default"]
    assert ('"remain undischarged, unpaid or unstayed for a period (during which execution shall not be effectively '
            'stayed) of 60 days"') in default
    fc = forecaster(base, [d])
    k = fc.node(d, "settlement_offer", "I1", "entered")
    fc.record((k,), fc.trace(d, (("settle", "I1", "no"),)))
    facts = fc.state(fc.nodes[k])[0]["path_facts"]
    assert "(during which execution shall not be effectively stayed) of 60 days" in facts["notes"]["judgment_default"]
    spec = next(t["nodes"]["settlement_offer"] for t in m["templates"].values()
                if "settlement_offer" in t.get("nodes", {}))
    text = json.dumps(drill_down(spec, m, "q", facts, None, True, {}, "settlement_offer", d, setup=fc.setup))
    for internal in ("by code", "trajector", "ruling class", "simulated operating"):
        assert internal not in text, internal
