"""The Akoustis dispute and notes as the agent would instantiate them from the pre-D record (D = 20 Jun 2024).

Figures and dates are the record's (DECOMPOSITION §1, §5): the 20 May 2024 judgment (D.I. 602), the pending motions
(D.I. 607, 608, 611, 613, 615, 618) with briefing closing 8 Aug 2024 (D.I. 605), the requested fees (D.I. 618), the
remittitur scenario (D.I. 616-1), commencement on 4 Oct 2021, and the 6.0% notes' terms (indenture §§7.01, 10.01,
16.02; 10-Q of 13 May 2024). Nothing dated after D. Readings are fixed (no Jev): judgment entered, motions pending.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.analysis import operating
from app.analysis.engine import NEED_DAYS, prepare
from app.analysis.events import Basis
from app.analysis.setup import DRAWS, SEED, Setup
from app.domain.investigation import (
    Component,
    Decisive,
    DisputeInstance,
    FinancingInstrument,
    PendingMotion,
)
from app.domain.values import Basis as VBasis
from app.domain.values import EvidenceValue, Provenance, Status, Unit
from app.finance.bank import load_feed

SNAP = "akoustis_20240620"
REVIEW = date(2024, 6, 20)
SETUP = Setup(review=REVIEW, horizon=REVIEW + timedelta(days=180), funding=date(2024, 6, 21),
              invoice_due=date(2024, 7, 22), invoice_cents=200_000_000, amount_cents=200_000_000, fee_bps=370,
              installments=3, days=90, discount_rate_bps=800)
CLOSE = date(2024, 8, 8)  # D.I. 605: briefing on the post-trial motions closes

COMPONENTS = (
    Component(component_id="ue", label="Unjust enrichment", kind="compensatory", status="awarded",
              amount_cents=3_131_521_500, remittitur_cents=2_310_000_000, motion="D.I. 613"),
    Component(component_id="exemplary", label="Exemplary damages", kind="exemplary", status="awarded",
              amount_cents=700_000_000),
    Component(component_id="patent", label="Patent damages", kind="patent", status="awarded", amount_cents=27_980_800,
              motion="D.I. 607"),
    Component(component_id="trebling", label="Trebling (UDTPA)", kind="trebling", status="requested",
              amount_cents=9_394_564_500, motion="D.I. 611"),
    Component(component_id="fees", label="Attorneys' fees", kind="fees", status="requested",
              amount_cents=1_211_612_330, motion="D.I. 618"),
    Component(component_id="pji", label="Pre-judgment interest", kind="prejudgment_interest", status="requested",
              statutory="nc_24_5_b", motion="D.I. 615"),
)
MOTIONS = (
    PendingMotion(motion_id="D.I. 607", kind="rule_50b", briefing_close=CLOSE, decides=("liability", "patent")),
    PendingMotion(motion_id="D.I. 613", kind="rule_59a", briefing_close=CLOSE, decides=("ue",)),
    PendingMotion(motion_id="D.I. 611", kind="rule_59e", briefing_close=CLOSE, decides=("trebling",)),
    PendingMotion(motion_id="D.I. 615", kind="rule_59e", briefing_close=CLOSE, decides=("pji",)),
    PendingMotion(motion_id="D.I. 618", kind="rule_54_fees", briefing_close=CLOSE, decides=("fees",)),
    PendingMotion(motion_id="D.I. 608", kind="injunction", briefing_close=CLOSE, decides=("injunction",)),
)
NOTES = FinancingInstrument(
    instrument_id="notes", dependency_id="dep", kind="convertible_notes", title="6.0% convertible senior notes due 2027",
    issuer="Akoustis Technologies, Inc.", finding_ids=("f_notes",), principal_cents=4_400_000_000,
    coupon_cents=132_000_000, interest_dates=(date(2024, 12, 15),), judgment_default_threshold_cents=1_000_000_000,
    judgment_default_days=60, insured_cents=0, listing_deadline=date(2024, 10, 21), repurchase_notice_business_days=20,
    repurchase_business_days=(20, 35), dispute_ids=("judgment",))


def judgment(**kw) -> DisputeInstance:
    dec = Decisive(finding_id="f", source_date="2024-05-20", quote="q")
    base = dict(instance_id="judgment", dependency_id="dep", model_id="post_judgment_money_dispute",
                model_version="4.0.0", title="Judgment (D.I. 602)", order_reference="D. Del. 1:21-cv-01417",
                nature="money_judgment", counterparty="Qorvo, Inc.", finding_ids=("f",),
                amount=EvidenceValue(status=Status.EXACT, unit=Unit.CENTS, value=3_859_502_300,
                                     provenance=Provenance(basis=VBasis.DOCUMENTED)),
                judgment_date=date(2024, 5, 20), commenced=date(2021, 10, 4), components=COMPONENTS, motions=MOTIONS,
                financing=(NOTES,), borrower_role="debtor", amount_status="fixed", stage="post_trial",
                established={"judgment_entered": dec, "post_trial_motions_pending": dec})
    return DisputeInstance(**{**base, **kw})


def basis(setup: Setup = SETUP) -> tuple[object, Basis]:
    feed = load_feed(SNAP)
    days = (setup.horizon - setup.review).days
    ops = operating.simulate(feed, days + NEED_DAYS, DRAWS, SEED, setup.variability)
    line = prepare(setup, ops)
    return feed, Basis.of(ops, line.need, feed.available_cents)
