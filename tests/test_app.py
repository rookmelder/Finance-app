"""Tests for the Finance web application."""

import os
import tempfile
import unittest

from src import db
from src.app import create_app


class TestApp(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.cfg = {
            "PASSWORD": "pw",
            "SECRET_KEY": "x" * 32,
            "DB_PATH": self.temp_db.name,
            "HOST": "127.0.0.1",
            "PORT": 8080,
        }
        self.app = create_app(self.cfg)
        self.client = self.app.test_client()

    def tearDown(self):
        if os.path.exists(self.temp_db.name):
            try:
                os.remove(self.temp_db.name)
            except OSError:
                pass

    def get_csrf(self, client=None):
        c = client or self.client
        with c.session_transaction() as sess:
            tok = sess.get("csrf")
        if not tok:
            c.get("/login")
            with c.session_transaction() as sess:
                tok = sess.get("csrf")
        return tok

    def login(self, client=None, password="pw"):
        c = client or self.client
        c.get("/login")
        token = self.get_csrf(c)
        return c.post(
            "/login",
            data={"password": password, "csrf_token": token},
            follow_redirects=True,
        )

    def test_login_required_and_pages_200(self):
        """Every page returns 200 when logged in and redirects to /login when not."""
        # Unauthenticated requests redirect to /login
        for endpoint in ["/", "/income", "/costs", "/expenses", "/settings"]:
            resp = self.client.get(endpoint)
            self.assertEqual(resp.status_code, 302)
            self.assertIn("/login", resp.headers["Location"])

        # Log in
        login_resp = self.login()
        self.assertEqual(login_resp.status_code, 200)

        # Authenticated requests return 200
        for endpoint in ["/", "/income", "/costs", "/expenses", "/settings"]:
            resp = self.client.get(endpoint)
            self.assertEqual(resp.status_code, 200, f"Endpoint {endpoint} failed")

        # Also test income_edit requires login and returns 200 when logged in
        # First create an income record
        csrf = self.get_csrf()
        self.client.post(
            "/income",
            data={"csrf_token": csrf, "work_month": "2026-10", "days": "18"},
        )
        resp = self.client.get("/income/2026-11")
        self.assertEqual(resp.status_code, 200)

        # Logout and test income_edit redirects
        csrf = self.get_csrf()
        self.client.post("/logout", data={"csrf_token": csrf})
        resp = self.client.get("/income/2026-11")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_post_without_csrf_400(self):
        """POST without CSRF token returns 400."""
        self.login()
        resp = self.client.post("/settings", data={"rate": "15.00"})
        self.assertEqual(resp.status_code, 400)

        resp = self.client.post("/income", data={"work_month": "2026-12", "days": "18"})
        self.assertEqual(resp.status_code, 400)

        resp = self.client.post("/costs", data={"action": "add", "name": "Rent", "amount": "500"})
        self.assertEqual(resp.status_code, 400)

        resp = self.client.post("/expenses", data={"action": "add", "amount": "10"})
        self.assertEqual(resp.status_code, 400)

    def test_settings_save_and_validation_error(self):
        """Settings save + validation error (percentages not 100 -> 400 with message)."""
        self.login()

        # Validation error: percentages sum to 110 != 100
        csrf = self.get_csrf()
        resp = self.client.post(
            "/settings",
            data={
                "csrf_token": csrf,
                "rate": "14.00",
                "allowance": "4.00",
                "hours_per_day": "7.5",
                "other": "0.00",
                "needs_pct": "60",
                "free_pct": "30",
                "save_pct": "20",
                "carry_over": "1",
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Percentages must sum to 100", resp.text)

        # Successful save
        csrf = self.get_csrf()
        resp = self.client.post(
            "/settings",
            data={
                "csrf_token": csrf,
                "rate": "14.00",
                "allowance": "4.00",
                "hours_per_day": "7.5",
                "other": "0.00",
                "needs_pct": "50",
                "free_pct": "20",
                "save_pct": "30",
                "carry_over": "1",
            },
        )
        self.assertEqual(resp.status_code, 303)

        # Verify new settings appear on GET
        get_resp = self.client.get("/settings")
        self.assertEqual(get_resp.status_code, 200)
        self.assertIn('value="14.00"', get_resp.text)
        self.assertIn('value="4.00"', get_resp.text)
        self.assertIn('value="7.5"', get_resp.text)

    def test_create_income_shows_pay_month_2027_01(self):
        """Create income for work month 2026-12 shows pay month 2027-01."""
        self.login()
        csrf = self.get_csrf()
        resp = self.client.post(
            "/income",
            data={
                "csrf_token": csrf,
                "work_month": "2026-12",
                "days": "18",
            },
        )
        self.assertEqual(resp.status_code, 303)

        get_resp = self.client.get("/income")
        self.assertEqual(get_resp.status_code, 200)
        self.assertIn("2027-01", get_resp.text)
        self.assertIn("2026-12", get_resp.text)

    def test_income_list_estimate(self):
        """Income list estimate for rate 1400 / 18 days / allowance 400 / 450 min = €1,962.00."""
        self.login()
        csrf = self.get_csrf()
        # Ensure settings are rate 1400, allowance 400, hours 7.5 (450 min)
        self.client.post(
            "/settings",
            data={
                "csrf_token": csrf,
                "rate": "14.00",
                "allowance": "4.00",
                "hours_per_day": "7.5",
                "other": "0.00",
                "needs_pct": "50",
                "free_pct": "20",
                "save_pct": "30",
                "carry_over": "1",
            },
        )

        csrf = self.get_csrf()
        self.client.post(
            "/income",
            data={
                "csrf_token": csrf,
                "work_month": "2026-10",
                "days": "18",
            },
        )

        resp = self.client.get("/income")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("€1,962.00", resp.text)

    def test_set_actual_then_overview(self):
        """Set actual then overview of that pay month shows the actual amount used."""
        self.login()
        csrf = self.get_csrf()
        # Save income for work month 2026-10 (pay month 2026-11)
        self.client.post(
            "/income",
            data={
                "csrf_token": csrf,
                "work_month": "2026-10",
                "days": "18",
            },
        )

        # Set actual pay
        csrf = self.get_csrf()
        resp = self.client.post(
            "/income/2026-11",
            data={
                "csrf_token": csrf,
                "action": "set_actual",
                "actual": "2100.00",
            },
        )
        self.assertEqual(resp.status_code, 303)

        # Overview of that pay month
        overview_resp = self.client.get("/?month=2026-11")
        self.assertEqual(overview_resp.status_code, 200)
        self.assertIn("€2,100.00", overview_resp.text)
        self.assertIn("actual", overview_resp.text)

    def test_recurring_cost_set_aside(self):
        """Recurring cost 2999 every 3 months shows €10.00 per month."""
        self.login()
        csrf = self.get_csrf()
        resp = self.client.post(
            "/costs",
            data={
                "csrf_token": csrf,
                "action": "add",
                "name": "Quarterly Insurance",
                "amount": "29.99",
                "every_months": "3",
                "notes": "Insurance payment",
                "active": "1",
            },
        )
        self.assertEqual(resp.status_code, 303)

        costs_resp = self.client.get("/costs")
        self.assertEqual(costs_resp.status_code, 200)
        self.assertIn("€10.00 per month", costs_resp.text)

    def test_expense_add_and_delete(self):
        """Expense add + delete."""
        self.login()
        csrf = self.get_csrf()
        resp = self.client.post(
            "/expenses",
            data={
                "csrf_token": csrf,
                "action": "add",
                "date": "2026-11-15",
                "amount": "45.50",
                "bucket": "free",
                "note": "Team dinner",
            },
        )
        self.assertEqual(resp.status_code, 303)

        get_resp = self.client.get("/expenses")
        self.assertEqual(get_resp.status_code, 200)
        self.assertIn("Team dinner", get_resp.text)
        self.assertIn("€45.50", get_resp.text)

        # Get the expense ID from DB
        conn = db.connect(self.cfg["DB_PATH"])
        expenses = db.list_expenses(conn)
        conn.close()
        self.assertEqual(len(expenses), 1)
        exp_id = expenses[0]["id"]

        # Delete the expense
        csrf = self.get_csrf()
        del_resp = self.client.post(
            "/expenses",
            data={
                "csrf_token": csrf,
                "action": "delete",
                "id": str(exp_id),
            },
        )
        self.assertEqual(del_resp.status_code, 303)

        get_resp2 = self.client.get("/expenses")
        self.assertEqual(get_resp2.status_code, 200)
        self.assertNotIn("Team dinner", get_resp2.text)

    def test_free_spend_carry_over(self):
        """Free-spend carry-over visible on the next month's overview.

        budget 39240, spent 10000 in month A => carry-in 29240 in month B when carry-over on, 0 when off.
        """
        self.login()
        # Set settings: rate 14.00, allowance 4.00, hours 7.5, split 50/20/30, carry_over on
        csrf = self.get_csrf()
        self.client.post(
            "/settings",
            data={
                "csrf_token": csrf,
                "rate": "14.00",
                "allowance": "4.00",
                "hours_per_day": "7.5",
                "other": "0.00",
                "needs_pct": "50",
                "free_pct": "20",
                "save_pct": "30",
                "carry_over": "1",
            },
        )

        # Month A: work month 2026-11 -> pay month 2026-12
        # Days 18: estimate = 196200, free budget (20%) = 39240
        csrf = self.get_csrf()
        self.client.post(
            "/income",
            data={
                "csrf_token": csrf,
                "work_month": "2026-11",
                "days": "18",
            },
        )

        # Spend 100.00 (10000 cents) on free in Month A (2026-12)
        csrf = self.get_csrf()
        self.client.post(
            "/expenses",
            data={
                "csrf_token": csrf,
                "action": "add",
                "date": "2026-12-05",
                "amount": "100.00",
                "bucket": "free",
                "note": "Month A free spend",
            },
        )

        # Overview for Month B (2027-01):
        # 39240 - 10000 = 29240 (€292.40) carry-in
        resp_b_on = self.client.get("/?month=2027-01")
        self.assertEqual(resp_b_on.status_code, 200)
        self.assertIn("€292.40", resp_b_on.text)

        # Now turn carry_over off
        csrf = self.get_csrf()
        self.client.post(
            "/settings",
            data={
                "csrf_token": csrf,
                "rate": "14.00",
                "allowance": "4.00",
                "hours_per_day": "7.5",
                "other": "0.00",
                "needs_pct": "50",
                "free_pct": "20",
                "save_pct": "30",
                # carry_over omitted = 0
            },
        )

        # Overview for Month B (2027-01) with carry_over off:
        # Carry-in must be €0.00
        resp_b_off = self.client.get("/?month=2027-01")
        self.assertEqual(resp_b_off.status_code, 200)
        self.assertIn("€0.00", resp_b_off.text)
        # Verify carry-in 29240 is not shown
        self.assertNotIn("€292.40", resp_b_off.text)

    def test_xss_escaping_note(self):
        """A note containing <script> is escaped in the HTML."""
        self.login()
        csrf = self.get_csrf()
        malicious_note = "<script>alert(1)</script>"
        resp = self.client.post(
            "/expenses",
            data={
                "csrf_token": csrf,
                "action": "add",
                "date": "2026-11-20",
                "amount": "12.00",
                "bucket": "free",
                "note": malicious_note,
            },
        )
        self.assertEqual(resp.status_code, 303)

        get_resp = self.client.get("/expenses")
        self.assertEqual(get_resp.status_code, 200)
        self.assertNotIn("<script>alert(1)</script>", get_resp.text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", get_resp.text)


class OverviewBadMonthTests(unittest.TestCase):
    def test_bad_month_redirects_to_current(self):
        import os, tempfile
        tmp = tempfile.mkdtemp()
        app = create_app({"PASSWORD": "pw", "SECRET_KEY": "x" * 32,
                          "DB_PATH": os.path.join(tmp, "t.db"), "HOST": "127.0.0.1", "PORT": 8080})
        c = app.test_client()
        c.get("/login")
        with c.session_transaction() as s:
            tok = s["csrf"]
        c.post("/login", data={"password": "pw", "csrf_token": tok})
        for bad in ("garbage", "2026-13", "2026-1", ""):
            r = c.get("/?month=" + bad)
            self.assertIn(r.status_code, (200, 303), bad)
            if r.status_code == 303:
                self.assertEqual(c.get(r.headers["Location"]).status_code, 200)


if __name__ == "__main__":
    unittest.main()
