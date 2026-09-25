"""Step 5b financial correctness: Slope products, the connected-bank baseline and the scenario engine."""

from datetime import date

from app.decisions.scenarios import Request, compare, simulate, summarize
from app.domain.investigation import BranchCash, DisputeBranch, DisputeNode
from app.domain.values import Basis, EvidenceValue, Provenance, Status, Unit
from app.finance.bank import load_feed, risk_features
from app.finance.slope_products import load_terms, menu, offer, tier_for

SNAP = "chromadex_20240819"
FUNDING = date(2024, 8, 20)
REQ = Request(amount_cents=200_000_000, invoice_due=date(2024, 9, 19), funding=FUNDING, requested_term_id="inst_120")


def ev(cents: int) -> EvidenceValue:
    return EvidenceValue(status=Status.EXACT, unit=Unit.CENTS, value=cents, provenance=Provenance(basis=Basis.DOCUMENTED))


def node(branches) -> DisputeNode:
    return DisputeNode(node_id="node_001", dependency_id="dep_001", decision_point="fee award", finding_ids=("fnd_001",),
                       branches=branches, weights_bps={b.branch_id: 10_000 // len(branches) + (i == 0) * (10_000 % len(branches))
                                                       for i, b in enumerate(branches)}, status="accepted")


def test_offers_conserve_totals_and_prorate_fees():
    terms = load_terms()
    for o in menu(terms, 200_000_000, "B"):
        sched = o.schedule(FUNDING)
        assert sum(p.amount_cents for p in sched) == o.total_cents
        assert sum(p.principal_cents for p in sched) == o.amount_cents
        assert all(p.due > FUNDING for p in sched)
        assert o.prorated_fee_cents(0) == 0 and o.prorated_fee_cents(o.days) == o.fee_cents
    net90 = offer(terms, "net_90", 200_000_000, "A")
    assert net90.fee_cents == 5_980_000  # 2.99% of USD 2.0M, the published best Net 90 rate
    assert 1_200 <= net90.apr_equivalent_bps(FUNDING) <= 1_300  # about 12-13% effective annual


def test_bank_feed_matches_public_anchors_and_stops_at_the_review_date():
    feed = load_feed(SNAP)
    assert feed.balances[date(2024, 3, 31)] == 2_756_500_000  # Q1 10-Q cash including restricted
    assert feed.balances[date(2024, 6, 30)] == 2_788_500_000  # Q2 10-Q cash including restricted
    assert feed.period_end == date(2024, 8, 19)
    assert all(t["date"] <= "2024-08-19" for t in feed.transactions)
    assert "Synthetic" in feed.provenance
    tier, _ = tier_for(risk_features(feed), load_terms())
    assert tier == "A"


def test_zero_cash_branches_reproduce_the_bank_only_view():
    empty = node((DisputeBranch(branch_id="br_1", label="a", description="a"),
                  DisputeBranch(branch_id="br_2", label="b", description="b")))
    comp = compare(load_feed(SNAP), load_terms(), REQ, [empty])
    for name in comp.structures:
        bank = [p for p in comp.paths if p.structure == name and p.view == "bank_only"]
        event = [p for p in comp.paths if p.structure == name and p.view == "event_adjusted"]
        assert event and {(p.min_cash_cents, tuple(p.collections)) for p in event} == \
            {(bank[0].min_cash_cents, tuple(bank[0].collections))}


def test_a_lock_lowers_cash_by_its_amount_and_collections_never_exceed_contract():
    feed, terms = load_feed(SNAP), load_terms()
    lock = BranchCash(kind="lock", label="bond", amount=ev(980_000_000), window_start=date(2024, 8, 20),
                      window_end=date(2024, 12, 31))
    s = offer(terms, "inst_120", 200_000_000, "A")
    horizon = date(2025, 2, 15)
    base, coll, owed, _ = simulate(feed, REQ, s, [], "stress", horizon)
    locked, coll2, _, _ = simulate(feed, REQ, s, [lock], "stress", horizon)
    assert all(base[d] - locked[d] == 980_000_000 for d in base if d >= date(2024, 8, 20))
    assert sum(c for _, c in coll) == s.total_cents and not owed
    assert sum(c for _, c in coll2) <= s.total_cents


def test_the_financed_invoice_is_paid_once():
    feed, terms = load_feed(SNAP), load_terms()
    horizon = date(2025, 2, 15)
    declined, _, _, _ = simulate(feed, REQ, None, [], "central", horizon)
    financed, _, _, _ = simulate(feed, REQ, offer(terms, "net_30", 200_000_000, "A"), [], "central", horizon)
    # Declining: the borrower pays the invoice itself. Financed: Slope pays it; the borrower repays Slope instead.
    fee = offer(terms, "net_30", 200_000_000, "A").fee_cents
    assert declined[horizon] - financed[horizon] == fee  # financing costs exactly the fee; the invoice is not paid twice


def test_weights_are_labelled_and_conditions_are_actions():
    lock = DisputeBranch(branch_id="br_1", label="appeal with bond", description="x", cash=(
        BranchCash(kind="lock", label="bond", amount=ev(980_000_000), window_start=date(2024, 8, 20),
                   window_end=date(2024, 12, 31)),))
    pay = DisputeBranch(branch_id="br_2", label="pay", description="y", cash=(
        BranchCash(kind="outflow", label="fee", amount=ev(980_000_000), window_start=date(2024, 8, 20),
                   window_end=date(2024, 12, 31)),))
    comp = compare(load_feed(SNAP), load_terms(), REQ, [node((lock, pay))])
    s = summarize(comp, load_terms())
    assert s["recommendation"]["bank_only"]["requested_passes"]
    assert s["recommendation"]["event_adjusted"]["limit_cents"] < s["recommendation"]["bank_only"]["limit_cents"]
    assert sum(b["weight_bps"] for b in s["nodes"][0]["branches"]) == 10_000
