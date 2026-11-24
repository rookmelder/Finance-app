import unittest

from src import balance, db


class TestBalance(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.amount = lambda row: row["actual_cents"] if row["actual_cents"] is not None else 100000

    def set_balance(self, cents=10000, today="2026-11-01"):
        return balance.set_balance(self.conn, cents, today, self.amount)

    def settle(self, today):
        balance.settle(self.conn, today, self.amount)

    def events(self, kind):
        return [e for e in db.list_balance_events(self.conn) if e["kind"] == kind]

    def test_without_set(self):
        row = db.add_recurring(self.conn, "Rent", 1000, next_due="2026-11-01")
        db.save_income(self.conn, "2026-10", 10)
        self.settle("2026-12-31")
        self.assertEqual(db.list_balance_events(self.conn), [])
        self.assertEqual(db.get_recurring(self.conn, row["id"])["next_due"], "2026-11-01")
        self.assertIsNone(balance.current(self.conn, "2026-12-31"))
        self.assertEqual(balance.history(self.conn, "2026-12-31"), [])

    def test_set_basic_and_negative(self):
        self.set_balance()
        self.assertEqual(balance.current(self.conn, "2026-11-01"), 10000)
        self.set_balance(-500, "2026-11-02")
        self.assertEqual(balance.current(self.conn, "2026-11-02"), -500)

    def test_expense_boundaries_and_future(self):
        old = db.add_expense(self.conn, "2026-11-01", 100, "needs")
        self.set_balance()
        self.assertEqual(self.events("set")[0]["meta"], old["id"])
        db.add_expense(self.conn, "2026-11-01", 200, "free")
        db.add_expense(self.conn, "2026-10-31", 300, "needs")
        db.add_expense(self.conn, "2026-11-02", 400, "needs", "Food")
        self.assertEqual(balance.current(self.conn, "2026-11-01"), 9800)
        self.assertEqual(balance.current(self.conn, "2026-11-02"), 9400)
        points = balance.history(self.conn, "2026-11-02")
        self.assertEqual([p["label"] for p in points], ["Balance set", "free", "Food"])

    def test_recurring_before_and_on_set_only_advance(self):
        before = db.add_recurring(self.conn, "Before", 100, next_due="2026-10-01")
        today = db.add_recurring(self.conn, "Today", 200, next_due="2026-11-01")
        self.set_balance()
        self.settle("2026-11-01")
        self.assertEqual(self.events("recurring"), [])
        for row in (before, today):
            self.assertEqual(db.get_recurring(self.conn, row["id"])["next_due"], "2026-12-01")

    def test_monthly_clamp_overdue_and_idempotent(self):
        self.set_balance(today="2026-01-01")
        row = db.add_recurring(self.conn, "Monthly", 100, next_due="2026-01-31")
        self.settle("2026-03-31")
        self.assertEqual([e["date"] for e in self.events("recurring")],
                         ["2026-01-31", "2026-02-28", "2026-03-31"])
        self.assertEqual(db.get_recurring(self.conn, row["id"])["next_due"], "2026-04-30")
        self.settle("2026-03-31")
        self.assertEqual(len(self.events("recurring")), 3)
        self.assertEqual(balance.current(self.conn, "2026-03-31"), 9700)
        self.settle("2026-05-31")
        self.assertEqual(self.events("recurring")[-1]["date"], "2026-05-31")

    def test_quarterly_and_ignored_recurring(self):
        self.set_balance(today="2026-01-01")
        row = db.add_recurring(self.conn, "Quarterly", 200, every_months=3, next_due="2026-01-15")
        inactive = db.add_recurring(self.conn, "Inactive", 300, active=0, next_due="2026-01-01")
        db.add_recurring(self.conn, "No date", 400)
        self.settle("2026-07-15")
        self.assertEqual([e["date"] for e in self.events("recurring")],
                         ["2026-01-15", "2026-04-15", "2026-07-15"])
        self.assertEqual(db.get_recurring(self.conn, row["id"])["next_due"], "2026-10-15")
        self.assertEqual(db.get_recurring(self.conn, inactive["id"])["next_due"], "2026-01-01")

    def test_pay_date_and_idempotence(self):
        self.set_balance()
        db.save_income(self.conn, "2026-11", 10)
        self.settle("2026-12-04")
        self.assertEqual(self.events("pay"), [])
        self.settle("2026-12-05")
        self.settle("2026-12-06")
        self.assertEqual(len(self.events("pay")), 1)
        self.assertEqual(self.events("pay")[0]["date"], "2026-12-05")
        self.assertEqual(self.events("pay")[0]["amount_cents"], 100000)

    def test_pay_on_or_before_set_and_zero(self):
        self.set_balance(today="2026-12-05")
        for work_month in ("2026-10", "2026-11", "2026-12"):
            db.save_income(self.conn, work_month, 10)
        db.set_actual(self.conn, "2027-01", 0)
        self.settle("2027-01-05")
        self.assertEqual(self.events("pay"), [])
        self.assertEqual(self.events("pay_adjust"), [])

    def test_pay_adjustments_positive_negative_and_equal(self):
        self.set_balance()
        db.save_income(self.conn, "2026-11", 10)
        self.settle("2026-12-05")
        db.set_actual(self.conn, "2026-12", 110000)
        self.settle("2026-12-06")
        self.settle("2026-12-06")
        db.set_actual(self.conn, "2026-12", 90000)
        self.settle("2026-12-07")
        self.settle("2026-12-08")
        adjustments = self.events("pay_adjust")
        self.assertEqual([e["amount_cents"] for e in adjustments], [10000, -20000])
        self.assertEqual([e["ref"] for e in adjustments], ["2026-12:1", "2026-12:2"])
        self.assertEqual(balance.current(self.conn, "2026-12-08"), 100000)

    def test_equal_actual_and_missing_pay_do_not_adjust(self):
        self.set_balance()
        db.save_income(self.conn, "2026-10", 10)
        db.save_income(self.conn, "2026-11", 10)
        self.settle("2026-11-05")
        db.set_actual(self.conn, "2026-11", 100000)
        db.set_actual(self.conn, "2026-12", 120000)
        self.settle("2026-11-06")
        self.assertEqual(self.events("pay_adjust"), [])

    def test_second_set_segments_history_and_same_day_order(self):
        self.set_balance()
        db.add_expense(self.conn, "2026-11-02", 1000, "needs", "Food")
        db.add_recurring(self.conn, "Bill", 500, next_due="2026-11-02")
        self.settle("2026-11-02")
        self.set_balance(7000, "2026-11-02")
        db.add_expense(self.conn, "2026-11-02", 200, "free")
        self.settle("2026-11-03")
        points = balance.history(self.conn, "2026-11-03")
        self.assertEqual([p["kind"] for p in points],
                         ["set", "recurring", "expense", "set", "expense"])
        self.assertEqual([p["balance_cents"] for p in points], [10000, 9500, 8500, 7000, 6800])
        self.assertEqual([p["delta_cents"] for p in points], [10000, -500, -1000, -1500, -200])
        self.assertEqual(balance.current(self.conn, "2026-11-03"), 6800)

    def test_set_settles_before_recording_absolute_amount(self):
        self.set_balance()
        db.add_recurring(self.conn, "Bill", 500, next_due="2026-11-03")
        self.set_balance(6000, "2026-11-03")
        events = db.list_balance_events(self.conn)
        self.assertEqual([e["kind"] for e in events], ["set", "recurring", "set"])
        self.assertEqual(balance.current(self.conn, "2026-11-03"), 6000)

    def test_upcoming_window_and_total_without_writes(self):
        db.add_recurring(self.conn, "Monthly", 1000, next_due="2026-11-05")
        db.add_recurring(self.conn, "Edge", 200, next_due="2026-12-05")
        db.add_recurring(self.conn, "Outside", 300, next_due="2026-12-06")
        db.add_recurring(self.conn, "Inactive", 400, active=0, next_due="2026-12-01")
        db.add_recurring(self.conn, "No date", 500)
        db.save_income(self.conn, "2026-10", 10)
        db.save_income(self.conn, "2026-11", 10)
        result = balance.upcoming(self.conn, "2026-11-05", "2026-12-05", self.amount)
        self.assertEqual(len(result["events"]), 3)
        self.assertEqual({e["date"] for e in result["events"]}, {"2026-12-05"})
        self.assertEqual(result["total_cents"], 98800)
        self.assertEqual(db.list_balance_events(self.conn), [])
        self.assertEqual(db.list_recurring(self.conn)[2]["next_due"], "2026-11-05")

    def test_last_day_of_month(self):
        for month, expected in (("2024-02", "2024-02-29"),
                                ("2026-02", "2026-02-28"),
                                ("2026-12", "2026-12-31")):
            with self.subTest(month=month):
                self.assertEqual(balance.last_day_of_month(month), expected)


if __name__ == "__main__":
    unittest.main()
