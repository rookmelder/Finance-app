import datetime
import unittest

from src.calc import (
    budget_chain,
    default_work_month,
    estimate_cents,
    format_hours,
    format_money,
    month_range,
    monthly_set_aside,
    parse_hours,
    parse_money,
    pay_month,
    split_income,
)


class TestCalc(unittest.TestCase):
    # --- Golden Examples from SPEC.md ---
    def test_golden_example_1(self):
        # 1. rate 1400, 450 min/day, 18 days, allowance 400 -> estimate_cents == 196200
        # (hours 189000 + allowance 7200). split_income(196200,50,20,30) == (98100, 39240, 58860).
        est = estimate_cents(rate_cents=1400, minutes_per_day=450, days=18, allowance_cents=400)
        self.assertEqual(est, 196200)
        split = split_income(196200, 50, 20, 30)
        self.assertEqual(split, (98100, 39240, 58860))

    def test_golden_example_2(self):
        # 2. rounding: rate 1337, 450 min/day, 3 days, allowance 333 -> 30083 + 999 = 31082.
        # split_income(31082,50,20,30) == (15541, 6216, 9325).
        est = estimate_cents(rate_cents=1337, minutes_per_day=450, days=3, allowance_cents=333)
        self.assertEqual(est, 31082)
        split = split_income(31082, 50, 20, 30)
        self.assertEqual(split, (15541, 6216, 9325))

    def test_golden_example_3(self):
        # 3. override: rate 1400, minutes_override 8040, days 18, allowance 400 -> 187600 + 7200 = 194800.
        est = estimate_cents(
            rate_cents=1400,
            minutes_per_day=450,
            days=18,
            allowance_cents=400,
            minutes_override=8040,
        )
        self.assertEqual(est, 194800)

    def test_golden_example_4(self):
        # 4. monthly_set_aside(2999, 3) == 1000; monthly_set_aside(1200, 12) == 100; monthly_set_aside(999, 1) == 999.
        self.assertEqual(monthly_set_aside(2999, 3), 1000)
        self.assertEqual(monthly_set_aside(1200, 12), 100)
        self.assertEqual(monthly_set_aside(999, 1), 999)

    def test_golden_example_5(self):
        # 5. carry-over chain: months ["2026-12","2027-01"],
        # budgets {"2026-12": (98100,39240,58860), "2027-01": (0,40000,0)},
        # spent free {"2026-12": 10000, "2027-01": 5000},
        # carry_over True -> Dec free_available 29240; Jan carry_in 29240, free_available 64240.
        # With carry_over False -> Jan carry_in 0, free_available 35000.
        # Negative: spent 50000 in Dec -> Dec free_available -10760, Jan carry_in -10760.
        months = ["2026-12", "2027-01"]
        budgets = {"2026-12": (98100, 39240, 58860), "2027-01": (0, 40000, 0)}
        spent_free = {"2026-12": 10000, "2027-01": 5000}

        chain_true = budget_chain(budgets, spent_free, True, months)
        self.assertEqual(chain_true["2026-12"]["carry_in"], 0)
        self.assertEqual(chain_true["2026-12"]["free"], 39240)
        self.assertEqual(chain_true["2026-12"]["spent_free"], 10000)
        self.assertEqual(chain_true["2026-12"]["free_available"], 29240)
        self.assertEqual(chain_true["2027-01"]["carry_in"], 29240)
        self.assertEqual(chain_true["2027-01"]["free"], 40000)
        self.assertEqual(chain_true["2027-01"]["spent_free"], 5000)
        self.assertEqual(chain_true["2027-01"]["free_available"], 64240)

        chain_false = budget_chain(budgets, spent_free, False, months)
        self.assertEqual(chain_false["2027-01"]["carry_in"], 0)
        self.assertEqual(chain_false["2027-01"]["free_available"], 35000)

        # Negative carry
        spent_neg = {"2026-12": 50000, "2027-01": 5000}
        chain_neg = budget_chain(budgets, spent_neg, True, months)
        self.assertEqual(chain_neg["2026-12"]["free_available"], -10760)
        self.assertEqual(chain_neg["2027-01"]["carry_in"], -10760)

    def test_golden_example_6(self):
        # 6. pay_month("2026-12") == "2027-01"; month_range("2026-11","2027-01") == ["2026-11","2026-12","2027-01"].
        self.assertEqual(pay_month("2026-12"), "2027-01")
        self.assertEqual(
            month_range("2026-11", "2027-01"), ["2026-11", "2026-12", "2027-01"]
        )

    # --- Additional pay_month and default_work_month tests ---
    def test_pay_month_rollover_and_validation(self):
        self.assertEqual(pay_month("2026-11"), "2026-12")
        self.assertEqual(pay_month("2026-12"), "2027-01")
        self.assertEqual(pay_month("2027-01"), "2027-02")
        with self.assertRaises(ValueError):
            pay_month("2026-13")
        with self.assertRaises(ValueError):
            pay_month("2026-00")
        with self.assertRaises(ValueError):
            pay_month("2026-5")
        with self.assertRaises(ValueError):
            pay_month("invalid")

    def test_default_work_month_boundaries_and_rollover(self):
        # Day 14 vs 15 boundary
        self.assertEqual(default_work_month(datetime.date(2026, 11, 14)), "2026-11")
        self.assertEqual(default_work_month(datetime.date(2026, 11, 15)), "2026-12")
        self.assertEqual(default_work_month(datetime.date(2026, 11, 10)), "2026-11")
        self.assertEqual(default_work_month(datetime.date(2026, 11, 20)), "2026-12")
        # December rollover: day 14 is Dec, day 15 is Jan next year
        self.assertEqual(default_work_month(datetime.date(2026, 12, 14)), "2026-12")
        self.assertEqual(default_work_month(datetime.date(2026, 12, 15)), "2027-01")
        self.assertEqual(default_work_month(datetime.date(2026, 12, 20)), "2027-01")

    def test_month_range(self):
        self.assertEqual(month_range("2026-05", "2026-05"), ["2026-05"])
        self.assertEqual(
            month_range("2026-10", "2027-02"),
            ["2026-10", "2026-11", "2026-12", "2027-01", "2027-02"],
        )
        with self.assertRaises(ValueError):
            month_range("2027-01", "2026-12")
        with self.assertRaises(ValueError):
            month_range("2026-13", "2027-01")
        with self.assertRaises(ValueError):
            month_range("bad", "2027-01")

    # --- estimate_cents edge cases ---
    def test_estimate_cents_days_zero(self):
        est = estimate_cents(rate_cents=1400, minutes_per_day=450, days=0, allowance_cents=400, other_cents=50)
        self.assertEqual(est, 50)
        est_no_other = estimate_cents(rate_cents=1400, minutes_per_day=450, days=0, allowance_cents=400)
        self.assertEqual(est_no_other, 0)

    # --- split_income tests ---
    def test_split_income(self):
        # pct not summing to 100
        with self.assertRaises(ValueError):
            split_income(1000, 50, 20, 20)
        with self.assertRaises(ValueError):
            split_income(1000, 50, 30, 30)
        # negative pct
        with self.assertRaises(ValueError):
            split_income(1000, -10, 60, 50)
        # income 0
        n, f, s = split_income(0, 50, 20, 30)
        self.assertEqual((n, f, s), (0, 0, 0))
        self.assertEqual(n + f + s, 0)
        # income 1 cent
        n1, f1, s1 = split_income(1, 50, 20, 30)
        self.assertEqual((n1, f1, s1), (0, 0, 1))
        self.assertEqual(n1 + f1 + s1, 1)

    # --- monthly_set_aside invalid args ---
    def test_monthly_set_aside_invalid(self):
        with self.assertRaises(ValueError):
            monthly_set_aside(100, 0)
        with self.assertRaises(ValueError):
            monthly_set_aside(100, -2)
        with self.assertRaises(ValueError):
            monthly_set_aside(-1, 5)

    # --- parse_money tests ---
    def test_parse_money(self):
        self.assertEqual(parse_money("14"), 1400)
        self.assertEqual(parse_money("14.5"), 1450)
        self.assertEqual(parse_money("14,50"), 1450)
        self.assertEqual(parse_money("  €14.50  "), 1450)
        self.assertEqual(parse_money("€ 14,5 "), 1450)
        self.assertEqual(parse_money("0"), 0)
        self.assertEqual(parse_money("0.05"), 5)

        # Invalid inputs
        with self.assertRaises(ValueError):
            parse_money("")
        with self.assertRaises(ValueError):
            parse_money("   ")
        with self.assertRaises(ValueError):
            parse_money("€")
        with self.assertRaises(ValueError):
            parse_money("-14")
        with self.assertRaises(ValueError):
            parse_money("14.500")
        with self.assertRaises(ValueError):
            parse_money("abc")
        with self.assertRaises(ValueError):
            parse_money("14.5.5")

    # --- parse_hours tests ---
    def test_parse_hours(self):
        self.assertEqual(parse_hours("7.5"), 450)
        self.assertEqual(parse_hours("7,5"), 450)
        self.assertEqual(parse_hours("8"), 480)
        self.assertEqual(parse_hours(" 8.0 "), 480)
        self.assertEqual(parse_hours("7.83"), 470)  # 7.83 * 60 = 469.8 -> 470

        # Invalid inputs
        with self.assertRaises(ValueError):
            parse_hours("")
        with self.assertRaises(ValueError):
            parse_hours("  ")
        with self.assertRaises(ValueError):
            parse_hours("-7.5")
        with self.assertRaises(ValueError):
            parse_hours("abc")
        with self.assertRaises(ValueError):
            parse_hours("7.555")

    # --- format_money tests ---
    def test_format_money(self):
        self.assertEqual(format_money(196200), "€1,962.00")
        self.assertEqual(format_money(-5), "-€0.05")
        self.assertEqual(format_money(0), "€0.00")
        self.assertEqual(format_money(100), "€1.00")
        self.assertEqual(format_money(-100000), "-€1,000.00")

    # --- format_hours tests ---
    def test_format_hours(self):
        self.assertEqual(format_hours(450), "7.5")
        self.assertEqual(format_hours(480), "8")
        self.assertEqual(format_hours(465), "7.75")
        self.assertEqual(format_hours(470), "7.83")
        self.assertEqual(format_hours(0), "0")

    # --- budget_chain edge cases ---
    def test_budget_chain_edge_cases(self):
        # Single month
        res_single = budget_chain({"2026-11": (100, 50, 20)}, {"2026-11": 30}, True, ["2026-11"])
        self.assertEqual(
            res_single["2026-11"],
            {"free": 50, "carry_in": 0, "spent_free": 30, "free_available": 20},
        )

        # Missing month in budget_by_month -> treated as (0, 0, 0)
        res_missing = budget_chain({}, {}, True, ["2026-11"])
        self.assertEqual(
            res_missing["2026-11"],
            {"free": 0, "carry_in": 0, "spent_free": 0, "free_available": 0},
        )

        # Missing month sequence
        res_seq = budget_chain(
            {"2026-11": (0, 100, 0)},
            {},
            True,
            ["2026-11", "2026-12", "2027-01"],
        )
        self.assertEqual(res_seq["2026-11"]["free_available"], 100)
        self.assertEqual(res_seq["2026-12"]["carry_in"], 100)
        self.assertEqual(res_seq["2026-12"]["free_available"], 100)
        self.assertEqual(res_seq["2027-01"]["carry_in"], 100)
        self.assertEqual(res_seq["2027-01"]["free_available"], 100)


class UnicodeDigitTests(unittest.TestCase):
    def test_non_ascii_digits_rejected(self):
        for bad in ("１４", "1٤"):
            with self.assertRaises(ValueError):
                parse_money(bad)
            with self.assertRaises(ValueError):
                parse_hours(bad)
            with self.assertRaises(ValueError):
                pay_month(bad + "-01")


if __name__ == "__main__":
    unittest.main()
