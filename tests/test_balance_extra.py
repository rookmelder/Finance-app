import unittest

from src import balance, db


class TestBalanceExtra(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.amount = lambda row: row["actual_cents"] if row["actual_cents"] is not None else 100000

    def events(self, kind=None):
        all_events = db.list_balance_events(self.conn)
        if kind is not None:
            return [e for e in all_events if e["kind"] == kind]
        return all_events

    def test_recurring_edit_next_due_not_clamped(self):
        # 1. Recurring edit case: a subscription (every 1 month) posted on 2026-12-31 by settle;
        # then the user changes next_due to 2027-02-15 (db.update_recurring);
        # settle on 2027-02-16 posts on 02-15 and next_due becomes 2027-03-15 (NOT 03-31);
        # upcoming() projects 03-15 too.
        balance.set_balance(self.conn, 50000, "2026-12-01", self.amount)
        sub = db.add_recurring(self.conn, "Streaming", 1200, every_months=1, next_due="2026-12-31")
        balance.settle(self.conn, "2026-12-31", self.amount)

        posted_dec = [e for e in self.events("recurring") if e["note"] == "Streaming"]
        self.assertEqual(len(posted_dec), 1)
        self.assertEqual(posted_dec[0]["date"], "2026-12-31")
        self.assertEqual(db.get_recurring(self.conn, sub["id"])["next_due"], "2027-01-31")

        db.update_recurring(self.conn, sub["id"], next_due="2027-02-15")
        balance.settle(self.conn, "2027-02-16", self.amount)

        posted_feb = [e for e in self.events("recurring") if e["note"] == "Streaming"]
        self.assertEqual(len(posted_feb), 2)
        self.assertEqual(posted_feb[-1]["date"], "2027-02-15")
        self.assertEqual(db.get_recurring(self.conn, sub["id"])["next_due"], "2027-03-15")

        up = balance.upcoming(self.conn, "2027-02-16", "2027-04-01", self.amount)
        sub_projected = [e for e in up["events"] if e["label"] == "Streaming"]
        self.assertEqual(len(sub_projected), 1)
        self.assertEqual(sub_projected[0]["date"], "2027-03-15")

    def test_recurring_month_end_clamp_recovery(self):
        # 1 (cont). Also: next_due 2026-01-31 every 1 month, settled through 2026-04-30 gives posts on 01-31, 02-28, 03-31
        # and next_due 2026-04-30 -> then 05-31 (month-end clamp recovered via the last posted event).
        balance.set_balance(self.conn, 50000, "2026-01-01", self.amount)
        row = db.add_recurring(self.conn, "Club", 800, every_months=1, next_due="2026-01-31")

        # Settling before 2026-04-30 (through 2026-04-29) posts Jan, Feb, Mar and leaves next_due at 2026-04-30
        balance.settle(self.conn, "2026-04-29", self.amount)
        posted = [e for e in self.events("recurring") if e["note"] == "Club"]
        self.assertEqual([e["date"] for e in posted], ["2026-01-31", "2026-02-28", "2026-03-31"])
        self.assertEqual(db.get_recurring(self.conn, row["id"])["next_due"], "2026-04-30")

        # upcoming() projects April 30 and recovers the clamp to May 31
        up = balance.upcoming(self.conn, "2026-04-29", "2026-06-05", self.amount)
        club_proj = [e for e in up["events"] if e["label"] == "Club"]
        self.assertEqual([e["date"] for e in club_proj], ["2026-04-30", "2026-05-31"])

        # Settle through 2026-05-31: posts 04-30 and 05-31, recovering day 31 from the last posted event
        balance.settle(self.conn, "2026-05-31", self.amount)
        posted_after = [e for e in self.events("recurring") if e["note"] == "Club"]
        self.assertEqual([e["date"] for e in posted_after],
                         ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31"])
        self.assertEqual(db.get_recurring(self.conn, row["id"])["next_due"], "2026-06-30")

    def test_same_day_set_expense_and_two_sets(self):
        # 2. Same-day: set at day D with expense logged BEFORE the set on day D (not counted)
        # and one AFTER (counted); two sets on the same day (second typed value wins,
        # segments correct in history, expenses logged between them belong to the first segment).
        db.add_expense(self.conn, "2026-06-15", 500, "needs", "Pre-set expense")
        balance.set_balance(self.conn, 10000, "2026-06-15", self.amount, note="First Set")
        db.add_expense(self.conn, "2026-06-15", 300, "free", "Mid expense")

        # Check before second set: pre-set expense excluded, mid expense included
        self.assertEqual(balance.current(self.conn, "2026-06-15"), 9700)

        # Second set on the same day
        balance.set_balance(self.conn, 25000, "2026-06-15", self.amount, note="Second Set")
        db.add_expense(self.conn, "2026-06-15", 400, "savings", "Post-set expense")

        # Current balance: second typed value wins, minus post-set expense
        self.assertEqual(balance.current(self.conn, "2026-06-15"), 24600)

        # History segments:
        points = balance.history(self.conn, "2026-06-15")
        labels = [p["label"] for p in points]
        self.assertNotIn("Pre-set expense", labels)
        self.assertEqual(labels, ["First Set", "Mid expense", "Second Set", "Post-set expense"])

        # Running balances and deltas across segments:
        self.assertEqual([p["balance_cents"] for p in points], [10000, 9700, 25000, 24600])
        self.assertEqual([p["delta_cents"] for p in points], [10000, -300, 15300, -400])

    def test_second_set_after_posted_pay_and_recurring(self):
        # 3. A second set after a posted pay and a recurring:
        # current() equals the typed value right after, and later events count only after it.
        balance.set_balance(self.conn, 10000, "2026-02-01", self.amount, note="Set 1")
        db.save_income(self.conn, "2026-01", 10)  # pay_month 2026-02, due 2026-02-05, amount 100000
        db.add_recurring(self.conn, "Rent", 30000, next_due="2026-02-08")
        balance.settle(self.conn, "2026-02-10", self.amount)
        self.assertEqual(balance.current(self.conn, "2026-02-10"), 80000)

        # Second set
        balance.set_balance(self.conn, 45000, "2026-02-15", self.amount, note="Set 2")
        self.assertEqual(balance.current(self.conn, "2026-02-15"), 45000)

        # Later events count only after the second set
        db.add_expense(self.conn, "2026-02-16", 2000, "needs", "Groceries")
        db.add_recurring(self.conn, "Utility", 5000, next_due="2026-02-18")
        balance.settle(self.conn, "2026-02-20", self.amount)

        self.assertEqual(balance.current(self.conn, "2026-02-20"), 38000)
        points = balance.history(self.conn, "2026-02-20")
        self.assertEqual([p["label"] for p in points],
                         ["Set 1", "Pay 2026-02", "Rent", "Set 2", "Groceries", "Utility"])
        self.assertEqual([p["balance_cents"] for p in points],
                         [10000, 110000, 80000, 45000, 43000, 38000])

    def test_pay_and_recurring_same_day_order(self):
        # 4. Pay and a recurring due on the same day: both posted, history order pay before recurring.
        balance.set_balance(self.conn, 5000, "2026-05-01", self.amount)
        db.save_income(self.conn, "2026-04", 10)  # pay_month 2026-05, pay_day 5 -> 2026-05-05
        db.add_recurring(self.conn, "Gym", 3000, next_due="2026-05-05")
        balance.settle(self.conn, "2026-05-05", self.amount)

        events = self.events()
        pay_events = [e for e in events if e["kind"] == "pay"]
        rec_events = [e for e in events if e["kind"] == "recurring"]
        self.assertEqual(len(pay_events), 1)
        self.assertEqual(len(rec_events), 1)
        self.assertEqual(pay_events[0]["date"], "2026-05-05")
        self.assertEqual(rec_events[0]["date"], "2026-05-05")

        points = balance.history(self.conn, "2026-05-05")
        same_day_kinds = [p["kind"] for p in points if p["date"] == "2026-05-05"]
        self.assertEqual(same_day_kinds, ["pay", "recurring"])

    def test_upcoming_matches_settle(self):
        # 5. upcoming() equals what settle() later posts:
        # project for a window, advance the real day, settle, compare amounts/dates of the new ledger events.
        balance.set_balance(self.conn, 20000, "2026-07-01", self.amount)
        db.save_income(self.conn, "2026-07", 10)  # pay_month 2026-08, due 2026-08-05
        db.add_recurring(self.conn, "Phone", 4500, every_months=1, next_due="2026-07-15")
        db.add_recurring(self.conn, "Internet", 6000, every_months=1, next_due="2026-08-01")

        up = balance.upcoming(self.conn, "2026-07-01", "2026-08-10", self.amount)
        proj_items = sorted([(e["date"], e["amount_cents"]) for e in up["events"]])
        self.assertEqual(len(proj_items), 3)

        # Advance day and settle
        balance.settle(self.conn, "2026-08-10", self.amount)
        new_events = [e for e in self.events() if e["kind"] in ("pay", "recurring")]
        posted_items = sorted([(e["date"], e["amount_cents"]) for e in new_events])

        self.assertEqual(posted_items, proj_items)
        self.assertEqual(sum(e["amount_cents"] for e in new_events), up["total_cents"])

    def test_real_pay_entered_before_and_adjustments_after(self):
        # 6. Real pay entered before the pay date: nothing yet;
        # after the pay date a single 'pay' event with the actual amount and no adjust;
        # real pay changed twice after the pay event -> two adjusts with refs ":1" and ":2" summing to the final difference.
        balance.set_balance(self.conn, 10000, "2026-09-01", self.amount)
        db.save_income(self.conn, "2026-08", 10)  # pay_month 2026-09, due 2026-09-05

        # Real pay entered before pay date
        db.set_actual(self.conn, "2026-09", 115000)
        balance.settle(self.conn, "2026-09-03", self.amount)
        self.assertEqual([e for e in self.events() if e["kind"] in ("pay", "pay_adjust")], [])

        # After pay date: single pay event with actual amount, no adjust
        balance.settle(self.conn, "2026-09-05", self.amount)
        pay_events = self.events("pay")
        adjust_events = self.events("pay_adjust")
        self.assertEqual(len(pay_events), 1)
        self.assertEqual(pay_events[0]["amount_cents"], 115000)
        self.assertEqual(adjust_events, [])

        # Real pay changed twice after pay event
        db.set_actual(self.conn, "2026-09", 125000)
        balance.settle(self.conn, "2026-09-08", self.amount)

        db.set_actual(self.conn, "2026-09", 108000)
        balance.settle(self.conn, "2026-09-10", self.amount)

        adjustments = self.events("pay_adjust")
        self.assertEqual(len(adjustments), 2)
        self.assertEqual(adjustments[0]["ref"], "2026-09:1")
        self.assertEqual(adjustments[1]["ref"], "2026-09:2")
        self.assertEqual(adjustments[0]["amount_cents"], 10000)   # 125000 - 115000
        self.assertEqual(adjustments[1]["amount_cents"], -17000)  # 108000 - 125000
        final_difference = 108000 - 115000                        # -7000
        self.assertEqual(sum(e["amount_cents"] for e in adjustments), final_difference)

    def test_delete_expense_or_income_consistency(self):
        # 7. Deleting an expense or income row later:
        # current() and history() stay consistent (last history point equals current()).
        balance.set_balance(self.conn, 30000, "2026-10-01", self.amount, note="Set")
        exp1 = db.add_expense(self.conn, "2026-10-03", 2500, "needs", "Expense 1")
        db.add_expense(self.conn, "2026-10-04", 1500, "free", "Expense 2")
        db.save_income(self.conn, "2026-09", 10)  # pay_month 2026-10
        balance.settle(self.conn, "2026-10-06", self.amount)

        points = balance.history(self.conn, "2026-10-06")
        self.assertEqual(points[-1]["balance_cents"], balance.current(self.conn, "2026-10-06"))

        # Deleting expense row later
        db.delete_expense(self.conn, exp1["id"])
        points_after_exp_del = balance.history(self.conn, "2026-10-06")
        self.assertEqual(points_after_exp_del[-1]["balance_cents"], balance.current(self.conn, "2026-10-06"))
        self.assertNotIn("Expense 1", [p["label"] for p in points_after_exp_del])
        self.assertEqual(balance.current(self.conn, "2026-10-06"), 30000 - 1500 + 100000)

        # Deleting income row later
        db.delete_income(self.conn, "2026-10")
        points_after_inc_del = balance.history(self.conn, "2026-10-06")
        self.assertEqual(points_after_inc_del[-1]["balance_cents"], balance.current(self.conn, "2026-10-06"))

    def test_settle_twice_and_future_set_ignored(self):
        # 8. settle() twice in a row, and with an unrelated set in the FUTURE (set date > today is ignored).
        balance.set_balance(self.conn, 20000, "2026-03-01", self.amount, note="Current Set")
        db.add_recurring(self.conn, "News", 1000, next_due="2026-03-05")

        # Future set event with date > today
        db.add_balance_event(self.conn, "2026-04-01", "set", 88888, note="Future Set")

        # Settle on 2026-03-10: future set is ignored
        balance.settle(self.conn, "2026-03-10", self.amount)
        self.assertEqual(balance.current(self.conn, "2026-03-10"), 19000)
        points = balance.history(self.conn, "2026-03-10")
        self.assertNotIn("Future Set", [p["label"] for p in points])

        # Settle twice in a row: no extra events, balance unchanged
        events_before = db.list_balance_events(self.conn)
        balance.settle(self.conn, "2026-03-10", self.amount)
        events_after = db.list_balance_events(self.conn)
        self.assertEqual(events_before, events_after)
        self.assertEqual(balance.current(self.conn, "2026-03-10"), 19000)

    def test_recurring_annual_and_bimonthly_overdue(self):
        # 9. A recurring cost every 12 months and one with every_months=2, 3 overdue occurrences in one settle.
        balance.set_balance(self.conn, 100000, "2023-01-01", self.amount)
        ann = db.add_recurring(self.conn, "Annual Insurance", 12000, every_months=12, next_due="2023-06-15")
        bi = db.add_recurring(self.conn, "Water Bill", 4000, every_months=2, next_due="2025-01-10")

        # Settle on 2025-06-20: exactly 3 overdue occurrences for each
        balance.settle(self.conn, "2025-06-20", self.amount)

        ann_events = [e for e in self.events("recurring") if e["note"] == "Annual Insurance"]
        self.assertEqual(len(ann_events), 3)
        self.assertEqual([e["date"] for e in ann_events], ["2023-06-15", "2024-06-15", "2025-06-15"])
        self.assertEqual(db.get_recurring(self.conn, ann["id"])["next_due"], "2026-06-15")
        self.assertTrue(all(e["amount_cents"] == -12000 for e in ann_events))

        bi_events = [e for e in self.events("recurring") if e["note"] == "Water Bill"]
        self.assertEqual(len(bi_events), 3)
        self.assertEqual([e["date"] for e in bi_events], ["2025-01-10", "2025-03-10", "2025-05-10"])
        self.assertEqual(db.get_recurring(self.conn, bi["id"])["next_due"], "2025-07-10")
        self.assertTrue(all(e["amount_cents"] == -4000 for e in bi_events))

    def test_huge_amounts_and_negative_balance(self):
        # 10. Huge amounts and negative balance: set_balance(-150000) then an expense; history deltas.
        balance.set_balance(self.conn, -150000, "2026-11-01", self.amount, note="Negative Base")
        db.add_expense(self.conn, "2026-11-05", 500_000_000, "needs", "Huge machinery")
        db.add_expense(self.conn, "2026-11-10", 75000, "free", "Coffee")

        expected_current = -150000 - 500_000_000 - 75000
        self.assertEqual(balance.current(self.conn, "2026-11-15"), expected_current)

        points = balance.history(self.conn, "2026-11-15")
        self.assertEqual(len(points), 3)

        self.assertEqual(points[0]["kind"], "set")
        self.assertEqual(points[0]["delta_cents"], -150000)
        self.assertEqual(points[0]["balance_cents"], -150000)

        self.assertEqual(points[1]["kind"], "expense")
        self.assertEqual(points[1]["delta_cents"], -500_000_000)
        self.assertEqual(points[1]["balance_cents"], -500_150_000)

        self.assertEqual(points[2]["kind"], "expense")
        self.assertEqual(points[2]["delta_cents"], -75000)
        self.assertEqual(points[2]["balance_cents"], expected_current)

        for p in points:
            self.assertIsInstance(p["delta_cents"], int)
            self.assertIsInstance(p["balance_cents"], int)


if __name__ == "__main__":
    unittest.main()
