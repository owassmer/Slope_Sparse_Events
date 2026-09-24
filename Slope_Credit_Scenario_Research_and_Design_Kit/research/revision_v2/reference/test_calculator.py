"""Boundary and conservation tests for the small reference calculator."""

import unittest
from decimal import Decimal as D
from calculator import (
    MerchantTerms, ConditionalBudget, CashReceipt, delay_receipts,
    present_value, principal_exposure_dollar_days, decimal,
)


class ContractTests(unittest.TestCase):
    def test_contract_floors_and_credit_thresholds(self):
        terms = MerchantTerms()
        self.assertEqual(terms.advance + terms.fixed_cost, terms.total_payment)
        for month, floor, credits in [
            (6, "125430", "501720"),
            (12, "250860", "1003440"),
            (18, "418100", "1672400"),
        ]:
            self.assertEqual(terms.cumulative_floor(month), D(floor))
            self.assertEqual(terms.qualifying_credit_threshold(month), D(credits))

    def test_cumulative_floor_counts_previous_manual_payment(self):
        terms = MerchantTerms()
        self.assertEqual(terms.cumulative_shortfall_only(12, "200000", "50000"), D("860"))
        self.assertEqual(terms.cumulative_shortfall_only(12, "200000", "50860"), D("0"))
        self.assertEqual(terms.cumulative_shortfall_only(6, "125429.99", "0"), D("0.01"))
        self.assertEqual(terms.cumulative_shortfall_only(6, "130000", "0"), D("0"))

    def test_first_window_excess_does_not_cover_second_window(self):
        terms = MerchantTerms()
        # 50% in months 1-6; 10% in months 7-12. Cumulative 60% is insufficient.
        self.assertEqual(terms.cumulative_shortfall_only(12, "250860", "0"), D("0"))
        self.assertEqual(terms.window_top_up(12, "41810", "0", "250860"), D("83620"))

    def test_confirmed_manual_credit_and_outstanding_cap(self):
        terms = MerchantTerms()
        self.assertEqual(terms.window_top_up(12, "41810", "10000", "260860"), D("73620"))
        self.assertEqual(terms.window_top_up(12, "41810", "83620", "334480"), D("0"))
        # A lender cannot collect a full window minimum beyond the remaining total.
        self.assertEqual(terms.window_top_up(12, "10000", "0", "410000"), D("8100"))
        self.assertEqual(terms.window_top_up(12, "10000", "0", "418100"), D("0"))


class BudgetTests(unittest.TestCase):
    def test_unknown_is_not_zero(self):
        budget = ConditionalBudget()
        self.assertIsNone(budget.remaining_settlement_cash())
        self.assertIsNone(budget.horizon_residual_before_new_facility())
        self.assertIn("settlements_paid_july1_through_august12", budget.unknown_fields())

    def test_pre_cutoff_payment_is_not_counted_twice(self):
        # Test fixture only: these are supplied arithmetic values, not borrower facts.
        budget = ConditionalBudget(
            unavailable_portion_of_opening_cash=D("0"),  # Explicit fixture assumption.
            settlements_paid_july1_through_august12=D("600000"),
            future_customer_cash_collections=D("800000"),
            future_committed_financing_draws=D("0"),
            future_other_confirmed_cash_inflows=D("0"),
            future_operating_cash_outflows_excluding_settlements_and_debt=D("400000"),
            future_other_debt_service_excluding_settlements=D("200000"),
            required_cash_reserve=D("200000"),
        )
        self.assertEqual(budget.remaining_settlement_cash(), D("2000000"))
        self.assertEqual(budget.horizon_residual_before_new_facility(), D("0"))

    def test_opening_cash_availability_is_required(self):
        self.assertIn("unavailable_portion_of_opening_cash", ConditionalBudget().unknown_fields())
        with self.assertRaises(ValueError):
            ConditionalBudget(unavailable_portion_of_opening_cash="2000000.01")

    def test_invalid_paid_amount_rejected(self):
        with self.assertRaises(ValueError):
            ConditionalBudget(settlements_paid_july1_through_august12="2600000.01")


class TimingTests(unittest.TestCase):
    def test_delay_conserves_principal_reduces_pv_and_extends_exposure(self):
        original = (CashReceipt(30, D("25000")), CashReceipt(90, D("75000")))
        delayed = delay_receipts(original, 30)
        self.assertEqual(sum(r.amount for r in original), sum(r.amount for r in delayed))
        self.assertLess(present_value(delayed, "0.12"), present_value(original, "0.12"))
        self.assertEqual(
            principal_exposure_dollar_days(delayed) - principal_exposure_dollar_days(original),
            D("3000000"),
        )

    def test_zero_discount_rate_removes_pv_penalty(self):
        original = (CashReceipt(90, D("100000")),)
        self.assertEqual(present_value(original, "0"), present_value(delay_receipts(original, 30), "0"))

    def test_invalid_numeric_inputs_rejected(self):
        for value in (1.1, True):
            with self.assertRaises(TypeError):
                decimal(value)
        for value in ("NaN", "Infinity"):
            with self.assertRaises(ValueError):
                decimal(value)
        with self.assertRaises(ValueError):
            delay_receipts((CashReceipt(90, D("1")),), -1)


if __name__ == "__main__":
    unittest.main()
