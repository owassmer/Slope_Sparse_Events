"""The 14 May 2024 pending dispute and notes as the agent would instantiate them (cases/akoustis_20240514/
fixture_dispute.json; fixed readings, no Jev), the case's setup and model, and the operating basis with the line."""

from __future__ import annotations

import json
from datetime import date
from functools import cache

from app.analysis.build import basis_for, setup_from_inputs
from app.config import CASES_DIR
from app.disputes.rules import load_model
from app.domain.investigation import Claim, Component, Decisive, DisputeInstance, FinancingInstrument
from app.domain.values import Basis as VBasis
from app.domain.values import EvidenceValue, Provenance, Status, Unit
from app.finance.bank import load_feed

SNAP = "akoustis_20240514"
REVIEW = date(2024, 5, 14)
RAW = json.loads((CASES_DIR / SNAP / "fixture_dispute.json").read_text())


def model() -> dict:
    return load_model(SNAP)


@cache
def setup():
    inputs = json.loads((CASES_DIR / SNAP / "run_inputs.json").read_text())
    return setup_from_inputs(inputs, REVIEW)


@cache
def basis():
    return basis_for(load_feed(SNAP), setup())


def notes() -> FinancingInstrument:
    n = RAW["notes"]
    return FinancingInstrument(**{**n, "interest_dates": tuple(date.fromisoformat(x) for x in n["interest_dates"]),
                                  "listing_deadline": date.fromisoformat(n["listing_deadline"]),
                                  "repurchase_business_days": tuple(n["repurchase_business_days"])},
                               dependency_id="dep", kind="convertible_notes", finding_ids=("f_notes",),
                               dispute_ids=(RAW["dispute"]["instance_id"],))


def pending(**kw) -> DisputeInstance:
    r = RAW["dispute"]
    dec = Decisive(finding_id="f", source_date="2024-05-14", quote="q")
    base = dict(
        instance_id=r["instance_id"], dependency_id="dep", model_id="post_judgment_money_dispute", model_version="4.1.0",
        title=r["title"], order_reference=r["order_reference"], nature=r["nature"], counterparty=r["counterparty"],
        finding_ids=("f",), amount=EvidenceValue(status=Status.EXACT, unit=Unit.CENTS, value=r["amount_cents"],
                                                 provenance=Provenance(basis=VBasis.DOCUMENTED)),
        judgment_date=None, commenced=date.fromisoformat(r["commenced"]),
        trial_started=date.fromisoformat(r["trial_started"]),
        claims=tuple(Claim(**c) for c in r["claims"]),
        components=tuple(Component(status="requested", **c) for c in r["components"]),
        financing=(notes(),), borrower_role="debtor", amount_status="sought", stage="liability_pending",
        established={"trial_pending": dec})
    return DisputeInstance(**{**base, **kw})
