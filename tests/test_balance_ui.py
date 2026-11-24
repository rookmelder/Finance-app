"""Bank balance routes, settlement, display and security."""

import datetime
import os
import secrets
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src import balance, calc, db
from src.app import _estimate, create_app


class FixedDate(datetime.date):
    @classmethod
    def today(cls):
        return cls(2026, 4, 20)


class BalanceUITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "test.sqlite")
        env = patch.dict(os.environ, {
            "PASSWORD": secrets.token_urlsafe(24),
            "SECRET_KEY": secrets.token_urlsafe(32),
        })
        env.start()
        self.addCleanup(env.stop)
        date_patch = patch("src.app.datetime", SimpleNamespace(
            date=FixedDate, datetime=datetime.datetime))
        date_patch.start()
        self.addCleanup(date_patch.stop)
        self.app = create_app({
            "PASSWORD": os.environ["PASSWORD"],
            "SECRET_KEY": os.environ["SECRET_KEY"],
            "DB_PATH": self.db_path,
            "TESTING": True,
        })
        self.client = self.app.test_client()
        self.client.get("/login")
        response = self.client.post("/login", data={
            "password": os.environ["PASSWORD"], "csrf_token": self.csrf(self.client),
        })
        self.assertIn(response.status_code, (302, 303))
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)

    def csrf(self, client):
        with client.session_transaction() as session:
            return session.get("csrf", "")

    def post(self, path, **data):
        return self.client.post(path, data={**data, "csrf_token": self.csrf(self.client)})

    def html(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def seed_balance(self):
        db.add_balance_event(self.conn, "2026-01-01", "set", 100000, meta=0)

    def test_settings_card_and_empty_pages(self):
        html = self.html("/settings")
        self.assertIn('id="bank"', html)
        self.assertIn("Bank account", html)
        self.assertIn('name="amount"', html)
        self.assertIn('name="note" maxlength="80"', html)
        self.assertIn('name="pay_day"', html)
        self.assertIn('value="' + str(db.get_balance_config(self.conn)["pay_day"]) + '"', html)
        for path in ("/", "/balance"):
            self.assertIn("Bank account - not set yet.", self.html(path))
        self.assertEqual(db.list_balance_events(self.conn), [])

    def test_valid_balances_stored_in_cents(self):
        for text, cents in (("1234.56", 123456), ("1234,56", 123456),
                            ("-20", -2000), ("€ 15", 1500),
                            (" -€ 20,50 ", -2050), ("0", 0)):
            with self.subTest(amount=text):
                response = self.post("/balance/set", amount=text)
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response.headers["Location"], "/settings?bank=saved#bank")
                event = db.list_balance_events(self.conn)[-1]
                self.assertEqual(event["kind"], "set")
                self.assertEqual(event["amount_cents"], cents)
                self.assertEqual(event["date"], "2026-04-20")

    def test_invalid_amount_changes_nothing(self):
        self.seed_balance()
        db.add_recurring(self.conn, name="Due cost", amount_cents=100,
                         every_months=1, next_due="2026-02-01")
        before = db.list_balance_events(self.conn)
        for text in ("abc", "", "1e3", "1.234,50", "1,234.50", "--20", "+20", "€€15"):
            with self.subTest(amount=text):
                response = self.post("/balance/set", amount=text)
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response.headers["Location"], "/settings?bank=bad_amount#bank")
                self.assertEqual(db.list_balance_events(self.conn), before)
        self.post("/balance/set", amount="10", note="x" * 81)
        self.assertEqual(db.list_balance_events(self.conn), before)
        self.assertEqual(db.list_recurring(self.conn)[0]["next_due"], "2026-02-01")

    def test_overview_balance_and_expense(self):
        self.post("/balance/set", amount="1000")
        html = self.html("/")
        self.assertLess(html.index("Bank account"), html.index("You can spend"))
        self.assertIn(calc.format_money(100000), html)
        self.assertIn("as of 2026-04-20", html)
        self.assertIn("+€0.00 since then", html)
        self.assertIn(calc.format_money(100000), self.html("/settings"))
        response = self.post("/expenses", action="add", date="2026-04-20",
                             amount="25", bucket="free", note="Lunch")
        self.assertEqual(response.status_code, 303)
        html = self.html("/")
        self.assertIn(calc.format_money(97500), html)
        self.assertIn("-€25.00 since then", html)
        self.assertIn('<svg ', html)

    def test_recurring_today_and_tomorrow_not_posted_after_todays_set(self):
        self.post("/balance/set", amount="1000")
        for due in ("2026-04-20", "2026-04-21"):
            db.add_recurring(self.conn, name="Subscription " + due,
                             amount_cents=2000, every_months=1, next_due=due)
        html = self.html("/")
        self.assertEqual([e["kind"] for e in db.list_balance_events(self.conn)], ["set"])
        self.assertEqual(balance.current(self.conn, "2026-04-20"), 100000)
        self.assertIn("Still to pay this month: " + calc.format_money(2000), html)
        self.assertIn("Expected at the end of the month: " + calc.format_money(98000), html)

    def test_past_recurring_settles_once(self):
        self.seed_balance()
        db.add_recurring(self.conn, name="Monthly subscription", amount_cents=2000,
                         every_months=1, next_due="2026-02-01")
        html = self.html("/")
        events = db.list_balance_events(self.conn)
        self.assertEqual(len([e for e in events if e["kind"] == "recurring"]), 3)
        self.assertEqual(balance.current(self.conn, "2026-04-20"), 94000)
        self.assertIn(calc.format_money(94000), html)
        self.html("/")
        self.assertEqual(db.list_balance_events(self.conn), events)

    def test_pay_estimate_and_real_pay_correction(self):
        response = self.post("/settings", rate="20", allowance="0",
                             hours_per_day="7.5", other="0", clothing="0",
                             needs_pct="50", free_pct="30", save_pct="20", rsz="0")
        self.assertEqual(response.status_code, 303)
        self.seed_balance()
        db.save_income(self.conn, work_month="2026-01", days=10)
        row = db.get_income(self.conn, "2026-02")
        estimate = _estimate(self.conn, row)["total"]
        self.assertGreater(estimate, 0)
        self.html("/")
        pay = [e for e in db.list_balance_events(self.conn) if e["kind"] == "pay"]
        self.assertEqual(len(pay), 1)
        self.assertEqual(pay[0]["amount_cents"], estimate)
        self.assertEqual(balance.current(self.conn, "2026-04-20"), 100000 + estimate)
        actual = estimate + 2500
        response = self.post("/income/2026-02", action="set_actual",
                             actual=f"{actual // 100}.{actual % 100:02d}")
        self.assertEqual(response.status_code, 303)
        self.assertIn(calc.format_money(100000 + actual), self.html("/"))
        adjustments = [e for e in db.list_balance_events(self.conn) if e["kind"] == "pay_adjust"]
        self.assertEqual(len(adjustments), 1)
        self.assertEqual(adjustments[0]["amount_cents"], 2500)
        self.assertIn("Pay correction", self.html("/balance"))
        self.assertEqual(len(db.list_balance_events(self.conn)), 3)

    def test_actual_pay_and_upcoming_pay(self):
        self.seed_balance()
        db.save_income(self.conn, work_month="2026-01", days=1)
        db.set_actual(self.conn, "2026-02", 12000)
        db.save_income(self.conn, work_month="2026-03", days=1)
        db.set_actual(self.conn, "2026-04", 34000)
        db.update_balance_config(self.conn, 28)
        html = self.html("/")
        self.assertEqual(balance.current(self.conn, "2026-04-20"), 112000)
        self.assertIn("Pay expected this month: " + calc.format_money(34000), html)
        self.assertIn("Expected at the end of the month: " + calc.format_money(146000), html)

    def test_history_newest_first_chart_and_escaped_note(self):
        self.post("/balance/set", amount="1000", note="<script>x</script>")
        self.post("/expenses", action="add", date="2026-04-20", amount="10",
                  bucket="free", note="Later expense <img src=x onerror=x>")
        html = self.html("/balance")
        self.assertIn('<svg ', html)
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", html)
        self.assertNotIn("<script>x</script>", html)
        self.assertNotIn("<img src=x", html)
        table = html.split('<table class="bank-history">', 1)[1]
        self.assertLess(table.index("Later expense"), table.index("&lt;script&gt;"))
        self.assertIn("Expense</small>", table)
        self.assertIn("Set</small>", table)
        self.assertIn("-€10.00", table)
        self.assertIn(calc.format_money(99000), table)

    def test_payday_validation_and_messages(self):
        before = db.get_balance_config(self.conn)["pay_day"]
        for bad in ("0", "29", "x", "", "1.5"):
            response = self.post("/balance/payday", pay_day=bad)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["Location"], "/settings?bank=bad_payday#bank")
            self.assertEqual(db.get_balance_config(self.conn)["pay_day"], before)
        for good in (1, 28):
            response = self.post("/balance/payday", pay_day=str(good))
            self.assertEqual(response.headers["Location"], "/settings?bank=payday_saved#bank")
            self.assertEqual(db.get_balance_config(self.conn)["pay_day"], good)
        for code, message in (("saved", "Bank balance saved."),
                              ("payday_saved", "Pay day saved."),
                              ("bad_amount", "Enter a valid balance"),
                              ("bad_payday", "Enter a pay day from 1 to 28.")):
            self.assertIn(message, self.html("/settings?bank=" + code))

    def test_login_required(self):
        anon = self.app.test_client()
        anon.get("/login")
        for path in ("/balance", "/balance/set", "/balance/payday"):
            response = (anon.get(path) if path == "/balance" else anon.post(
                path, data={"csrf_token": self.csrf(anon)}))
            self.assertEqual(response.status_code, 302)
            self.assertIn("/login", response.headers["Location"])

    def test_csrf_required(self):
        for path, data in (("/balance/set", {"amount": "100"}),
                           ("/balance/payday", {"pay_day": "15"})):
            for token in (None, "bad"):
                form = dict(data)
                if token is not None:
                    form["csrf_token"] = token
                self.assertEqual(self.client.post(path, data=form).status_code, 400)
        self.assertEqual(db.list_balance_events(self.conn), [])

    def test_settle_duplicate_is_treated_as_already_settled(self):
        self.post("/balance/set", amount="100")
        with patch("src.app.balance.settle", side_effect=ValueError("Duplicate event")):
            self.assertIn(calc.format_money(10000), self.html("/"))


if __name__ == "__main__":
    unittest.main()
