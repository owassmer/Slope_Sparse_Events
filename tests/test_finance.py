"""Financial correctness: the kit's reference arithmetic and the spec §10 invariants for step 3."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.domain.values import Basis, Unit, UnknownInput, assumed, documented, unknown
from app.finance import ledger, settlements
from app.finance.calendar import add_months, previous_business_day
from app.finance.fixed_installment import FixedInstallmentOffer
from app.finance.ledger import BorrowerCashBase, CashStream, DoubleCountError, LedgerError
from app.finance.merchant import (
    AccountCredits,
    ConsolidatedRevenue,
    MerchantLoanState,
    MerchantTerms,
    project,
)
from app.finance.reference_check import checks
from app.finance.streams import new_draw_streams, settlement_stream
from app.finance.thresholds import horizon_residual, remaining_period_required_net_cash
from app.finance.valuation import present_value_cents, principal_dollar_days

AUG12, AUG13, JUN30 = date(2024, 8, 12), date(2024, 8, 13), date(2024, 6, 30)


def cents(usd: str) -> int:
    return int(Decimal(usd) * 100)


def a(value, name="A_test", unit=Unit.CENTS, **kw):
    return assumed(value, unit, name, "test fixture assumption", **kw)


def paid(value):
    """Settlement payments since June 30, observed through the day before the forecast starts."""
    return assumed(value, Unit.CENTS, "A_paid_since_june30", "test fixture assumption", observed_on=AUG12)


def window_due(k):
    return previous_business_day(add_months(FUNDING, 6 * k) - timedelta(days=1))


# --- reference arithmetic --------------------------------------------------------------------

@pytest.mark.parametrize("name,engine,reference", checks(), ids=lambda x: x if isinstance(x, str) else "")
def test_engine_reproduces_kit_reference(name, engine, reference):
    assert engine == reference, name


def test_window_rules_match_reference_cases():
    t = MerchantTerms()
    assert t.window_top_up_cents(2, cents("41810"), cents("10000"), cents("260860")) == cents("73620")
    assert t.window_top_up_cents(2, cents("41810"), cents("83620"), cents("334480")) == 0
    assert t.window_top_up_cents(2, cents("10000"), 0, cents("410000")) == cents("8100")  # capped at remaining
    assert t.window_top_up_cents(2, cents("10000"), 0, cents("418100")) == 0
    assert t.cumulative_shortfall_only_cents(6, cents("125429.99"), 0) == 1


# --- actual merchant contract ----------------------------------------------------------------

FUNDING = date(2024, 5, 6)  # test assumption; the actual effective funding date is unknown


def seasoned_state(as_of: date, received: int, w1_daily: int, w1_manual: int = 0) -> MerchantLoanState:
    return MerchantLoanState(
        as_of=as_of, funding_date=a(FUNDING, "A_funding", Unit.DATE),
        received_to_date_cents=a(received), window_daily_cents={1: a(w1_daily), 2: a(0)},
        window_manual_credit_cents={1: a(w1_manual), 2: a(0)}, unmet_prior_obligation_cents=a(0))


def flat_credits(start: date, end: date, daily_cents: int) -> AccountCredits:
    d, out = start, {}
    while d <= end:
        out[d] = daily_cents
        d += timedelta(days=1)
    return AccountCredits(store_scope="test store", covers=(start, end), daily_gross_cents=out, provenance_note="test")


def test_first_window_excess_does_not_cover_the_second_window():
    t = MerchantTerms()
    # 50% already paid in window 1 by the analysis date; low credits afterwards.
    state = seasoned_state(AUG12, received=cents("209050"), w1_daily=cents("209050"))
    horizon = add_months(FUNDING, 12) + timedelta(days=10)
    p = project(t, state, flat_credits(AUG13, horizon, 0), horizon=horizon)
    top_ups = [r for r in p.rows if r.kind == "window_top_up"]
    assert [r.window for r in top_ups] == [2]  # window 1 already met; its excess does not carry over
    assert top_ups[0].on == window_due(2) < t.window_bounds(FUNDING, 2)[1]  # due inside its own window
    assert top_ups[0].amount_cents == t.minimum_payment_cents


def test_seasoned_clocks_run_from_funding_not_the_analysis_date():
    t = MerchantTerms()
    horizon = date(2025, 12, 31)
    for as_of in (date(2024, 6, 30), AUG12):
        p = project(t, seasoned_state(as_of, 0, 0), flat_credits(as_of + timedelta(1), horizon, 0), horizon=horizon)
        top_ups = [r.on for r in p.rows if r.kind == "window_top_up"]
        assert top_ups == [window_due(1), window_due(2)]
        assert p.paid_off_on is not None and p.paid_off_on < add_months(FUNDING, 18)
        assert sum(r.amount_cents for r in p.rows) == t.total_payment_cents  # never beyond the total


def test_daily_payments_use_account_credits_and_next_business_day():
    t = MerchantTerms()
    sat = date(2024, 8, 17)
    credits = AccountCredits(store_scope="test", covers=(AUG13, date(2024, 8, 31)), daily_gross_cents={sat: 400_00},
                             provenance_note="test")
    p = project(t, seasoned_state(AUG12, 0, 0), credits, horizon=date(2024, 8, 31))
    assert [(r.on, r.amount_cents) for r in p.rows] == [(date(2024, 8, 19), 100_00)]
    with pytest.raises(TypeError):
        project(t, seasoned_state(AUG12, 0, 0), ConsolidatedRevenue(daily_cents={sat: 1}), horizon=date(2024, 8, 31))


def test_missing_account_credits_are_unknown_not_zero():
    with pytest.raises(UnknownInput, match="Account Credits"):
        project(MerchantTerms(), seasoned_state(AUG12, 0, 0), flat_credits(AUG13, date(2024, 9, 30), 500_00),
                horizon=date(2025, 6, 30))


def test_unknown_funding_date_blocks_the_dated_schedule():
    state = seasoned_state(AUG12, 0, 0).model_copy(update={"funding_date": unknown(Unit.DATE, "not disclosed")})
    with pytest.raises(UnknownInput):
        project(MerchantTerms(), state, flat_credits(AUG13, AUG13, 0), horizon=AUG13)


# --- settlement calendar ---------------------------------------------------------------------

HVL = settlements.SettlementObligation(
    obligation_id="hvl_atrium_settlement", label="HVL / Atrium settlement loan", measured_on=JUN30,
    remaining_by_year_cents={2024: 200_000_000, 2025: 200_000_000, 2026: 80_244_500},
    source_fact_ids=("synergy_hvl_h2_2024_payment", "synergy_hvl_2025_payment", "synergy_hvl_2026_payment"))


def test_june_schedule_needs_an_explicit_bridge_and_counts_payments_once():
    with pytest.raises(UnknownInput):
        settlements.calendar(HVL, unknown(Unit.CENTS, "July-August payments not observed"), start=AUG13,
                             timing="early", through=date(2026, 12, 31))
    pays = settlements.calendar(HVL, paid(40_000_000), start=AUG13, timing="monthly_even", through=date(2026, 12, 31))
    assert sum(p.amount_cents for p in pays) == HVL.total_remaining_cents - 40_000_000
    assert sum(p.amount_cents for p in pays if p.bucket_year == 2024) == 160_000_000
    assert all(date(p.bucket_year, 1, 1) <= p.on <= date(p.bucket_year, 12, 31) and p.on >= AUG13 for p in pays)
    with pytest.raises(ValueError):
        settlements.remaining_after_bridge(HVL, a(200_000_001))


def test_timing_scenarios_place_buckets_without_inventing_amounts():
    early = settlements.calendar(HVL, paid(0), start=AUG13, timing="early", through=date(2025, 12, 31))
    late = settlements.calendar(HVL, paid(0), start=AUG13, timing="late", through=date(2025, 12, 31))
    with pytest.raises(ValueError, match="observed through"):  # a June-dated bridge cannot open an August forecast
        settlements.calendar(HVL, a(0, observed_on=JUN30), start=AUG13, timing="early", through=date(2025, 12, 31))
    assert [(p.on, p.amount_cents) for p in early] == [(date(2024, 8, 13), 200_000_000), (date(2025, 1, 2), 200_000_000)]
    assert [(p.on, p.amount_cents) for p in late] == [(date(2024, 12, 31), 200_000_000), (date(2025, 12, 31), 200_000_000)]


# --- borrower cash ledger --------------------------------------------------------------------

def base(streams, *, opening=None, unavailable=None, reserve=None, start=AUG13, end=date(2025, 3, 31)):
    return BorrowerCashBase(
        borrower_id="synergy_chc_corp", forecast_start=start,
        opening_cash=opening or documented(200_000_000, Unit.CENTS, observed_on=AUG12, approximate=True,
                                           facts=("synergy_cash_20240812_approx",)),
        unavailable_opening_cash=unavailable or a(0), required_reserve=reserve or a(20_000_000),
        end=end, streams=tuple(streams))


COVER = (AUG13, date(2025, 12, 31))


def receipts(rows, sid="receipts"):
    return CashStream(stream_id=sid, kind="operating_receipts", rows=tuple(rows), basis=Basis.OPERATOR, note="test",
                      covers=COVER)


def test_no_fake_opening_balance():
    r = ledger.run(base([], unavailable=unknown(Unit.CENTS, "restricted share not reported")))
    assert r.status == "not_computable" and r.missing_inputs == ["unavailable_opening_cash"]
    june_cash = documented(8_729_300, Unit.CENTS, observed_on=JUN30)
    with pytest.raises(LedgerError, match="cannot open a forecast"):
        ledger.run(base([], opening=june_cash))
    with pytest.raises(LedgerError, match="already reflects"):
        ledger.run(base([receipts([(AUG12, 1)])]))


def test_each_obligation_is_counted_once():
    pays = settlements.calendar(HVL, paid(0), start=AUG13, timing="late", through=date(2025, 3, 31))
    s = settlement_stream(HVL.obligation_id, pays)
    tagged = CashStream(stream_id="opex_incl_hvl", kind="operating_outflow", rows=((date(2024, 9, 30), 1),),
                        basis=Basis.OPERATOR, note="", obligation_id=HVL.obligation_id, covers=COVER)
    with pytest.raises(DoubleCountError):  # the same obligation through a non-debt stream
        ledger.run(base([s, tagged]))
    with pytest.raises(DoubleCountError):
        ledger.run(base([s, s.model_copy(update={"stream_id": "dup"})]))
    aggregate = CashStream(stream_id="other_debt", kind="other_debt_service", rows=((date(2024, 9, 30), 1),),
                           basis=Basis.OPERATOR, note="all notes payable service", covers=COVER)
    with pytest.raises(DoubleCountError, match="excludes"):
        ledger.run(base([s, aggregate]))
    ok = aggregate.model_copy(update={"excludes_obligations": (HVL.obligation_id,)})
    assert ledger.run(base([s, ok])).status == "computed"
    with pytest.raises(DoubleCountError, match="Only one aggregate"):
        ledger.run(base([s, ok, ok.model_copy(update={"stream_id": "other_debt_2"})]))


def test_recurring_streams_must_cover_the_forecast():
    short = receipts([(date(2024, 9, 1), 1)]).model_copy(update={"covers": (AUG13, date(2024, 9, 30))})
    with pytest.raises(UnknownInput, match="coverage"):
        ledger.run(base([short]))


def test_opening_day_breach_is_reported():
    r = ledger.run(base([receipts([(date(2024, 9, 1), 50_000_000)])], opening=documented(
        10_000_000, Unit.CENTS, observed_on=AUG12, approximate=True)))
    assert r.first_breach_on == AUG13 and r.min_headroom_cents == -10_000_000 and r.min_headroom_on == AUG13


def test_noncash_normalization_never_becomes_cash():
    gain = CashStream(stream_id="fy2023_settlement_gain", kind="operating_outflow", rows=((date(2024, 9, 1), 223_598_600),),
                      basis=Basis.DOCUMENTED, note="accounting gain normalization", is_cash=False)
    with pytest.raises(LedgerError, match="non-cash"):
        ledger.run(base([gain]))


def test_direct_vendor_and_bank_routes_do_not_double_count():
    offer = FixedInstallmentOffer(proposal_id="analysis_100k_6m", advance_cents=10_000_000, term_months=6,
                                  fee_fraction=Decimal("0.06"))
    common = dict(funding_date=date(2024, 8, 20), purchase_cost_cents=10_000_000, purchase_date=date(2024, 8, 21),
                  purchase_receipts=[(date(2024, 10, 15), 16_000_000)])
    bank = ledger.run(base(new_draw_streams(offer, route="bank", **common)))
    direct = ledger.run(base(new_draw_streams(offer, route="direct_to_vendor", **common)))
    for d in (date(2024, 8, 21), date(2024, 12, 31), date(2025, 3, 31)):
        assert bank.closing_on(d) == direct.closing_on(d)
    bad = new_draw_streams(offer, route="direct_to_vendor", **common) + [
        CashStream(stream_id="x", kind="new_loan_disbursement", rows=((date(2024, 8, 20), 10_000_000),),
                   basis=Basis.OPERATOR, note="", route="direct_to_vendor", loan_ref="analysis_100k_6m")]
    with pytest.raises(DoubleCountError):
        ledger.run(base(bad))
    with pytest.raises(ValueError, match="remainder"):  # $60k purchase from a $100k direct-to-vendor advance
        new_draw_streams(offer, route="direct_to_vendor", **{**common, "purchase_cost_cents": 6_000_000})
    with pytest.raises(ValueError, match="precede funding"):
        new_draw_streams(offer, route="bank", **{**common, "purchase_date": date(2024, 8, 19)})


def test_dated_test_is_stricter_than_the_cumulative_threshold():
    # Cumulative: $2.0m opening + $0.85m receipts covers $2.6m settlements + $0.2m reserve by year end,
    # but early placement pays before the receipts arrive.
    supplier = settlements.SettlementObligation(obligation_id="march_supplier_settlement", label="March 2024 supplier",
                                                measured_on=JUN30, remaining_by_year_cents={2024: 60_000_000})
    rec = receipts([(date(2024, 12, 15), 85_000_000)])
    end = date(2024, 12, 31)

    def run(timing):
        s = [settlement_stream(o.obligation_id, settlements.calendar(o, paid(0), start=AUG13, timing=timing, through=end))
             for o in (HVL, supplier)]
        return ledger.run(base([*s, rec], end=end))

    late, early = run("late"), run("early")
    assert late.first_breach_on is None and late.closing_cents == 200_000_000 + 85_000_000 - 260_000_000
    assert early.first_breach_on == AUG13 and early.min_headroom_cents == 200_000_000 - 260_000_000 - 20_000_000
    assert early.deficit_dates  # negative cash is reported as a financing deficit, not netted away


def test_smaller_or_declined_draw_changes_the_purchase_and_its_receipts():
    small = FixedInstallmentOffer(proposal_id="analysis_100k_6m", advance_cents=10_000_000, term_months=6,
                                  fee_fraction=Decimal("0.06"))
    s = new_draw_streams(small, funding_date=date(2024, 8, 20), route="bank", purchase_cost_cents=10_000_000,
                         purchase_date=date(2024, 8, 21), purchase_receipts=[(date(2024, 10, 15), 16_000_000)])
    with_draw, declined = ledger.run(base(s)), ledger.run(base([]))
    assert with_draw.totals_by_kind["financed_purchase_receipts"] == 16_000_000
    assert "financed_purchase_receipts" not in declined.totals_by_kind


# --- thresholds and valuation ------------------------------------------------------------------

def test_threshold_moves_dollar_for_dollar_and_unknown_stays_unknown():
    def thr(paid_hvl, unavailable=0):
        return remaining_period_required_net_cash(
            [("hvl", 200_000_000, a(paid_hvl)), ("supplier", 60_000_000, a(0))],
            opening_cash=documented(200_000_000, Unit.CENTS, approximate=True), unavailable_opening_cash=a(unavailable),
            reserve=a(20_000_000))
    assert thr(0).value == 80_000_000 and thr(40_000_000).value == 40_000_000
    assert thr(0, unavailable=10_000_000).value == 90_000_000
    res = remaining_period_required_net_cash(
        [("hvl", 200_000_000, unknown(Unit.CENTS, "not observed"))],
        opening_cash=documented(200_000_000, Unit.CENTS, approximate=True), unavailable_opening_cash=a(0),
        reserve=a(20_000_000))
    assert res.value is None and "hvl_paid_since_measurement" in res.provenance.note


def test_horizon_residual_reference_case():
    r = horizon_residual(
        opening_cash=documented(200_000_000, Unit.CENTS, approximate=True), unavailable_opening_cash=a(0),
        settlements_remaining=[("h2_settlements", 260_000_000, a(60_000_000))],
        customer_collections=a(80_000_000), committed_financing=a(0), other_confirmed_inflows=a(0),
        operating_outflows_excl_settlements_and_debt=a(40_000_000), other_debt_service_excl_settlements=a(20_000_000),
        reserve=a(20_000_000))
    assert r.value == 0


def test_delay_conserves_principal_extends_exposure_and_lowers_pv():
    original, delayed = [(30, 2_500_000), (90, 7_500_000)], [(60, 2_500_000), (120, 7_500_000)]
    assert present_value_cents(delayed, "0.12") < present_value_cents(original, "0.12")
    assert present_value_cents(delayed, "0") == present_value_cents(original, "0")
    extra = principal_dollar_days([(0, 10_000_000)], delayed) - principal_dollar_days([(0, 10_000_000)], original)
    assert extra == 3_000_000 * 100
