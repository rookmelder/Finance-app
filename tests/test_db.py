import sqlite3
import unittest
from src import db


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_default_settings(self):
        s = db.get_settings(self.conn)
        self.assertEqual(s["rate_cents"], 0)
        self.assertEqual(s["allowance_cents"], 0)
        self.assertEqual(s["minutes_per_day"], 450)
        self.assertEqual(s["other_cents"], 0)
        self.assertEqual(s["needs_pct"], 50)
        self.assertEqual(s["free_pct"], 20)
        self.assertEqual(s["save_pct"], 30)
        self.assertEqual(s["carry_over"], 1)

    def test_update_settings_valid_and_invalid(self):
        # Valid update
        updated = db.update_settings(self.conn, rate_cents=1400, allowance_cents=400, needs_pct=60, free_pct=20, save_pct=20)
        self.assertEqual(updated["rate_cents"], 1400)
        self.assertEqual(updated["allowance_cents"], 400)
        self.assertEqual(updated["needs_pct"], 60)
        self.assertEqual(updated["free_pct"], 20)
        self.assertEqual(updated["save_pct"], 20)

        # Invalid pct sum != 100
        with self.assertRaises(ValueError):
            db.update_settings(self.conn, needs_pct=60, free_pct=30, save_pct=20)

        # Unknown field
        with self.assertRaises(ValueError):
            db.update_settings(self.conn, random_field=123)

        # Negative cents
        with self.assertRaises(ValueError):
            db.update_settings(self.conn, rate_cents=-100)

        # Negative pct
        with self.assertRaises(ValueError):
            db.update_settings(self.conn, needs_pct=-10, free_pct=50, save_pct=60)

        # Invalid minutes_per_day
        with self.assertRaises(ValueError):
            db.update_settings(self.conn, minutes_per_day=0)
        with self.assertRaises(ValueError):
            db.update_settings(self.conn, minutes_per_day=1441)

        # Invalid carry_over
        with self.assertRaises(ValueError):
            db.update_settings(self.conn, carry_over=2)

    def test_income_pay_month_rollover(self):
        # "2026-12" -> "2027-01"
        inc = db.save_income(self.conn, "2026-12", days=18)
        self.assertEqual(inc["pay_month"], "2027-01")
        self.assertEqual(inc["work_month"], "2026-12")
        self.assertEqual(inc["days"], 18)

    def test_income_snapshot_isolation(self):
        db.update_settings(self.conn, rate_cents=1400, allowance_cents=400)
        row1 = db.save_income(self.conn, "2026-10", days=15)
        self.assertEqual(row1["pay_month"], "2026-11")
        self.assertEqual(row1["rate_cents"], 1400)
        self.assertEqual(row1["allowance_cents"], 400)

        # Change settings rate and allowance
        db.update_settings(self.conn, rate_cents=2000, allowance_cents=500)

        # Row 1 must keep snapshot
        row1_fetched = db.get_income(self.conn, "2026-11")
        self.assertIsNotNone(row1_fetched)
        self.assertEqual(row1_fetched["rate_cents"], 1400)
        self.assertEqual(row1_fetched["allowance_cents"], 400)

        # Update without refresh_snapshot: changes days, keeps old snapshot
        updated_row1 = db.save_income(self.conn, "2026-10", days=20, refresh_snapshot=False)
        self.assertEqual(updated_row1["days"], 20)
        self.assertEqual(updated_row1["rate_cents"], 1400)

        # Update with refresh_snapshot: snapshot updates to current settings
        refreshed_row1 = db.save_income(self.conn, "2026-10", days=20, refresh_snapshot=True)
        self.assertEqual(refreshed_row1["rate_cents"], 2000)
        self.assertEqual(refreshed_row1["allowance_cents"], 500)

    def test_income_validation_and_set_actual(self):
        # Invalid days
        with self.assertRaises(ValueError):
            db.save_income(self.conn, "2026-10", days=-1)
        with self.assertRaises(ValueError):
            db.save_income(self.conn, "2026-10", days=32)

        # Invalid minutes override
        with self.assertRaises(ValueError):
            db.save_income(self.conn, "2026-10", days=10, minutes_override=-1)
        with self.assertRaises(ValueError):
            db.save_income(self.conn, "2026-10", days=10, minutes_override=45000)

        # Valid save
        db.save_income(self.conn, "2026-10", days=10, minutes_override=4500)
        inc = db.get_income(self.conn, "2026-11")
        self.assertIsNotNone(inc)
        self.assertEqual(inc["minutes_override"], 4500)
        self.assertIsNone(inc["actual_cents"])

        # set_actual
        updated = db.set_actual(self.conn, "2026-11", 150000)
        self.assertEqual(updated["actual_cents"], 150000)

        # clear actual
        cleared = db.set_actual(self.conn, "2026-11", None)
        self.assertIsNone(cleared["actual_cents"])

        # set_actual unknown month
        with self.assertRaises(ValueError):
            db.set_actual(self.conn, "2026-01", 1000)

        # set_actual negative cents
        with self.assertRaises(ValueError):
            db.set_actual(self.conn, "2026-11", -10)

        # delete_income
        db.delete_income(self.conn, "2026-11")
        self.assertIsNone(db.get_income(self.conn, "2026-11"))
        with self.assertRaises(ValueError):
            db.delete_income(self.conn, "2026-11")

    def test_income_list_order(self):
        db.save_income(self.conn, "2026-05", days=10)  # pay 2026-06
        db.save_income(self.conn, "2026-08", days=10)  # pay 2026-09
        db.save_income(self.conn, "2026-01", days=10)  # pay 2026-02

        items = db.list_income(self.conn)
        self.assertEqual([x["pay_month"] for x in items], ["2026-09", "2026-06", "2026-02"])

    def test_expenses_crud_and_spent_by_month(self):
        # Valid expense
        e1 = db.add_expense(self.conn, "2026-11-05", 2500, "needs", "Groceries")
        self.assertEqual(e1["amount_cents"], 2500)
        self.assertEqual(e1["bucket"], "needs")
        self.assertEqual(e1["note"], "Groceries")

        e2 = db.add_expense(self.conn, "2026-11-10", 1500, "free", "Cinema")
        e3 = db.add_expense(self.conn, "2026-11-20", 5000, "savings", "ETF")
        e4 = db.add_expense(self.conn, "2026-12-01", 3000, "needs", "Rent")

        # spent_by_month
        spent = db.spent_by_month(self.conn)
        self.assertEqual(spent["2026-11"], {"needs": 2500, "free": 1500, "savings": 5000})
        self.assertEqual(spent["2026-12"]["needs"], 3000)

        # list_expenses and filter
        all_exp = db.list_expenses(self.conn)
        self.assertEqual(len(all_exp), 4)
        self.assertEqual(all_exp[0]["id"], e4["id"])  # newest date first

        nov_exp = db.list_expenses(self.conn, month="2026-11")
        self.assertEqual(len(nov_exp), 3)

        # Invalid amount (<= 0)
        with self.assertRaises(ValueError):
            db.add_expense(self.conn, "2026-11-05", 0, "needs", "Zero")
        with self.assertRaises(ValueError):
            db.add_expense(self.conn, "2026-11-05", -10, "needs", "Negative")

        # Invalid bucket
        with self.assertRaises(ValueError):
            db.add_expense(self.conn, "2026-11-05", 100, "luxuries", "Invalid")

        # Invalid date
        with self.assertRaises(ValueError):
            db.add_expense(self.conn, "2026-02-30", 100, "needs", "Bad date")
        with self.assertRaises(ValueError):
            db.add_expense(self.conn, "not-a-date", 100, "needs", "Bad format")

        # delete expense
        db.delete_expense(self.conn, e1["id"])
        with self.assertRaises(ValueError):
            db.delete_expense(self.conn, e1["id"])

    def test_recurring_crud(self):
        r1 = db.add_recurring(self.conn, "Internet", 4000, every_months=1, notes="Fiber", active=1, next_due="2026-12-01")
        self.assertEqual(r1["name"], "Internet")
        self.assertEqual(r1["amount_cents"], 4000)
        self.assertEqual(r1["every_months"], 1)
        self.assertEqual(r1["active"], 1)

        r2 = db.add_recurring(self.conn, "Gym", 3000, every_months=1, active=0)

        # list_recurring
        all_rec = db.list_recurring(self.conn)
        self.assertEqual([x["name"] for x in all_rec], ["Gym", "Internet"])

        active_rec = db.list_recurring(self.conn, only_active=True)
        self.assertEqual([x["name"] for x in active_rec], ["Internet"])

        # update_recurring
        up = db.update_recurring(self.conn, r1["id"], amount_cents=4500, active=0)
        self.assertEqual(up["amount_cents"], 4500)
        self.assertEqual(up["active"], 0)

        # unknown id update/delete
        with self.assertRaises(ValueError):
            db.update_recurring(self.conn, 9999, amount_cents=100)
        with self.assertRaises(ValueError):
            db.delete_recurring(self.conn, 9999)

        # delete recurring
        db.delete_recurring(self.conn, r1["id"])
        self.assertIsNone(db.get_recurring(self.conn, r1["id"]))

        # Invalid parameters
        with self.assertRaises(ValueError):
            db.add_recurring(self.conn, "", 1000)
        with self.assertRaises(ValueError):
            db.add_recurring(self.conn, "Test", 0)
        with self.assertRaises(ValueError):
            db.add_recurring(self.conn, "Test", 100, every_months=0)
        with self.assertRaises(ValueError):
            db.add_recurring(self.conn, "Test", 100, next_due="2026-13-01")

    def test_sql_injection_verbatim(self):
        weird_text = "'; DROP TABLE settings; -- \" OR 1=1"
        rec = db.add_recurring(self.conn, weird_text, 1000, notes=weird_text)
        self.assertEqual(rec["name"], weird_text)
        self.assertEqual(rec["notes"], weird_text)

        fetched_rec = db.get_recurring(self.conn, rec["id"])
        self.assertIsNotNone(fetched_rec)
        self.assertEqual(fetched_rec["name"], weird_text)

        exp = db.add_expense(self.conn, "2026-11-15", 500, "free", note=weird_text)
        self.assertEqual(exp["note"], weird_text)

        # Settings table still exists
        s = db.get_settings(self.conn)
        self.assertEqual(s["id"], 1)


class PayslipFieldTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def test_new_setting_defaults_and_update(self):
        s = db.get_settings(self.conn)
        self.assertEqual((s["clothing_cents"], s["sunday_premium_cents"], s["rsz_bp"]), (0, 1200, 271))
        s = db.update_settings(self.conn, clothing_cents=220, rsz_bp=271)
        self.assertEqual(s["clothing_cents"], 220)
        with self.assertRaises(ValueError):
            db.update_settings(self.conn, rsz_bp=10001)

    def test_income_stores_sundays_holiday_and_snapshot(self):
        db.update_settings(self.conn, clothing_cents=220)
        row = db.save_income(self.conn, "2026-11", 7, minutes_override=3030, sundays=1, holiday_minutes=450)
        self.assertEqual((row["sundays"], row["holiday_minutes"], row["clothing_cents"]), (1, 450, 220))
        db.update_settings(self.conn, clothing_cents=300)
        row = db.save_income(self.conn, "2026-11", 7, sundays=1)
        self.assertEqual(row["clothing_cents"], 220)  # snapshot kept
        row = db.save_income(self.conn, "2026-11", 7, sundays=1, refresh_snapshot=True)
        self.assertEqual(row["clothing_cents"], 300)

    def test_sundays_validation(self):
        with self.assertRaises(ValueError):
            db.save_income(self.conn, "2026-11", 3, sundays=4)
        with self.assertRaises(ValueError):
            db.save_income(self.conn, "2026-11", 3, holiday_minutes=-1)

    def test_migrates_old_database(self):
        import os, sqlite3, tempfile
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        old = sqlite3.connect(path)
        old.executescript("""
            CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK (id = 1), rate_cents INTEGER NOT NULL,
              allowance_cents INTEGER NOT NULL, minutes_per_day INTEGER NOT NULL, other_cents INTEGER NOT NULL,
              needs_pct INTEGER NOT NULL, free_pct INTEGER NOT NULL, save_pct INTEGER NOT NULL, carry_over INTEGER NOT NULL);
            INSERT INTO settings VALUES (1, 1597, 368, 450, 0, 50, 20, 30, 1);
            CREATE TABLE income (pay_month TEXT PRIMARY KEY, work_month TEXT NOT NULL, days INTEGER NOT NULL,
              minutes_override INTEGER NULL, actual_cents INTEGER NULL, rate_cents INTEGER NOT NULL,
              allowance_cents INTEGER NOT NULL, minutes_per_day INTEGER NOT NULL, other_cents INTEGER NOT NULL,
              needs_pct INTEGER NOT NULL, free_pct INTEGER NOT NULL, save_pct INTEGER NOT NULL);
            INSERT INTO income VALUES ('2026-12','2026-11',7,NULL,NULL,1597,368,450,0,50,20,30);
        """)
        old.commit(); old.close()
        conn = db.connect(path)
        self.assertEqual(db.get_settings(conn)["rate_cents"], 1597)
        self.assertEqual(db.get_settings(conn)["rsz_bp"], 271)
        row = db.get_income(conn, "2026-12")
        self.assertEqual((row["sundays"], row["rsz_bp"]), (0, 271))
        conn.close()
        db.connect(path).close()  # second connect must not fail


class CalendarDbTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.update_settings(self.conn, rate_cents=1597, allowance_cents=368, clothing_cents=220)

    def test_settings_defaults_and_derived_flat_premium(self):
        s = db.get_settings(self.conn)
        self.assertEqual((s["sunday_cents_per_hour"], s["sunday_cap_minutes"], s["sunday_premium_cents"]), (200, 360, 1200))
        s = db.update_settings(self.conn, sunday_cents_per_hour=300)
        self.assertEqual(s["sunday_premium_cents"], 1800)
        with self.assertRaises(ValueError):
            db.update_settings(self.conn, sunday_premium_cents=5)  # derived, not editable

    def test_save_month_days_creates_income_and_derives(self):
        days = [{"date": "2026-10-04", "minutes": 450, "holiday": False},
                {"date": "2026-10-05", "minutes": 450, "holiday": False},
                {"date": "2026-11-01"[:7] + "-x", "minutes": 1}][:2]
        row = db.save_month_days(self.conn, "2026-10", days)
        self.assertEqual(row["pay_month"], "2026-11")
        self.assertEqual((row["per_day"], row["days"], row["sundays"], row["minutes_override"]), (1, 2, 1, 900))
        self.assertEqual(db.list_days(self.conn, "2026-10"), days)
        self.assertEqual(row["rate_cents"], 1597)

    def test_replace_and_clear(self):
        db.save_month_days(self.conn, "2026-10", [{"date": "2026-10-04", "minutes": 450, "holiday": True}])
        row = db.save_month_days(self.conn, "2026-10", [{"date": "2026-10-06", "minutes": 390, "holiday": False}])
        self.assertEqual([x["date"] for x in db.list_days(self.conn, "2026-10")], ["2026-10-06"])
        self.assertEqual((row["sundays"], row["holiday_minutes"]), (0, 0))
        row = db.save_month_days(self.conn, "2026-10", [])
        self.assertEqual((row["days"], row["per_day"]), (0, 1))
        self.assertEqual(db.list_days(self.conn, "2026-10"), [])

    def test_empty_without_income_creates_nothing(self):
        self.assertIsNone(db.save_month_days(self.conn, "2026-10", []))
        self.assertEqual(db.list_income(self.conn), [])

    def test_validation_leaves_db_untouched(self):
        db.save_month_days(self.conn, "2026-10", [{"date": "2026-10-06", "minutes": 450, "holiday": False}])
        bad = [[{"date": "2026-11-02", "minutes": 450}],
               [{"date": "2026-10-06", "minutes": 0}],
               [{"date": "2026-10-06", "minutes": 450}, {"date": "2026-10-06", "minutes": 60}],
               [{"date": "2026-10-32", "minutes": 450}],
               [{"date": "2026-10-06", "minutes": 450, "holiday": "yes"}],
               "nope", [5]]
        for entries in bad:
            with self.assertRaises(ValueError):
                db.save_month_days(self.conn, "2026-10", entries)
        self.assertEqual(len(db.list_days(self.conn, "2026-10")), 1)

    def test_delete_income_removes_days_and_refresh_snapshot(self):
        db.save_month_days(self.conn, "2026-10", [{"date": "2026-10-06", "minutes": 450, "holiday": False}])
        db.update_settings(self.conn, rate_cents=2000)
        self.assertEqual(db.get_income(self.conn, "2026-11")["rate_cents"], 1597)
        self.assertEqual(db.refresh_snapshot(self.conn, "2026-11")["rate_cents"], 2000)
        self.assertEqual(len(db.list_days(self.conn, "2026-10")), 1)
        db.delete_income(self.conn, "2026-11")
        self.assertEqual(db.list_days(self.conn, "2026-10"), [])

    def test_migrates_from_payslip_schema(self):
        import os, sqlite3, tempfile
        path = os.path.join(tempfile.mkdtemp(), "mid.db")
        c = db.connect(path)
        c.executescript("""
            ALTER TABLE settings DROP COLUMN sunday_cents_per_hour; ALTER TABLE settings DROP COLUMN sunday_cap_minutes;
            ALTER TABLE income DROP COLUMN sunday_cents_per_hour; ALTER TABLE income DROP COLUMN sunday_cap_minutes;
            ALTER TABLE income DROP COLUMN per_day; DROP TABLE work_days;
        """)
        c.commit(); c.close()
        c = db.connect(path)
        self.assertEqual(db.get_settings(c)["sunday_cap_minutes"], 360)
        self.assertEqual(db.list_days(c, "2026-10"), [])
        c.close()


if __name__ == "__main__":
    unittest.main()
