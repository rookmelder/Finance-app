import os
import tempfile
import unittest

from src import db
from src.app import create_app


class CalendarApiTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "t.db")
        self.app = create_app({"PASSWORD": "pw", "SECRET_KEY": "x" * 32, "DB_PATH": self.path,
                               "HOST": "127.0.0.1", "PORT": 8080})
        self.c = self.app.test_client()
        self.c.get("/login")
        self.form("/login", password="pw")
        self.form("/settings", rate="15.97", allowance="3.68", clothing="2.20", rsz="2.71",
                  hours_per_day="7.5", other="", needs_pct="50", free_pct="20", save_pct="30",
                  carry_over="1", sunday_per_hour="2", sunday_cap_hours="6")

    def token(self):
        with self.c.session_transaction() as s:
            return s["csrf"]

    def form(self, url, **data):
        data["csrf_token"] = self.token()
        return self.c.post(url, data=data)

    def save(self, month, days, token=True, raw=None):
        headers = {"X-CSRF-Token": self.token()} if token else {}
        if raw is not None:
            return self.c.post(f"/calendar/{month}/save", data=raw, headers=headers,
                               content_type="application/json")
        return self.c.post(f"/calendar/{month}/save", json={"days": days}, headers=headers)

    OCT = [{"date": "2026-10-04", "minutes": 450, "holiday": False},
           {"date": "2026-10-05", "minutes": 450, "holiday": False},
           {"date": "2026-10-11", "minutes": 450, "holiday": False}]

    def test_october_payslip_through_calendar(self):
        r = self.save("2026-10", self.OCT)
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual((body["pay_month"], body["days"], body["sundays"]), ("2026-11", 3, 2))
        self.assertEqual(body["total"], "€390.58")
        labels = [row["label"] for row in body["rows"]]
        self.assertIn("Sunday premium", labels)
        self.assertEqual(body["rows"][-1]["kind"], "total")

    def test_overview_and_list_agree_with_calendar(self):
        self.save("2026-10", self.OCT)
        self.assertIn("€390.58", self.c.get("/?month=2026-11").get_data(as_text=True))
        self.assertIn("€390.58", self.c.get("/income").get_data(as_text=True))

    def test_replace_days_and_clear(self):
        self.save("2026-10", self.OCT)
        r = self.save("2026-10", [self.OCT[1]])
        self.assertEqual(r.get_json()["sundays"], 0)
        r = self.save("2026-10", [])
        self.assertEqual((r.get_json()["days"], r.get_json()["total"]), (0, "€0.00"))

    def test_empty_save_without_income_creates_nothing(self):
        r = self.save("2026-10", [])
        self.assertEqual(r.status_code, 200)
        conn = db.connect(self.path)
        self.assertEqual(db.list_income(conn), [])

    def test_bad_input_is_400_never_500(self):
        cases = [
            [{"date": "2026-11-02", "minutes": 450, "holiday": False}],
            [{"date": "2026-10-05", "minutes": 0, "holiday": False}],
            [{"date": "2026-10-05", "minutes": 5000, "holiday": False}],
            [{"date": "2026-10-05", "minutes": "450", "holiday": False}],
            [{"date": "2026-10-05", "minutes": 450}, {"date": "2026-10-05", "minutes": 60}],
            [{"date": "2026-10-40", "minutes": 450}],
            [{"date": "2026-10-05", "minutes": 450, "holiday": "yes"}],
            ["x"], [None],
        ]
        for days in cases:
            r = self.save("2026-10", days)
            self.assertEqual(r.status_code, 400, days)
            self.assertFalse(r.get_json()["ok"])
        for raw in ("not json", "[1,2]", "{}", '{"days": "nope"}', '{"days": {"a": 1}}'):
            r = self.save("2026-10", None, raw=raw)
            self.assertEqual(r.status_code, 400, raw)

    def test_failed_save_keeps_previous_days(self):
        self.save("2026-10", self.OCT)
        self.save("2026-10", [{"date": "2026-10-05", "minutes": 0}])
        conn = db.connect(self.path)
        self.assertEqual(len(db.list_days(conn, "2026-10")), 3)

    def test_csrf_required(self):
        r = self.save("2026-10", self.OCT, token=False)
        self.assertEqual(r.status_code, 400)
        conn = db.connect(self.path)
        self.assertEqual(db.list_income(conn), [])

    def test_login_required(self):
        anon = self.app.test_client()
        r = anon.post("/calendar/2026-10/save", json={"days": []})
        self.assertIn(r.status_code, (302, 400))

    def test_invalid_month_404(self):
        for m in ("2026-13", "garbage", "2026-1"):
            self.assertEqual(self.save(m, []).status_code, 404, m)

    def test_holiday_double_pay_and_sunday_holiday(self):
        # 1 Nov 2026 is a Sunday AND All Saints' Day: both rules apply (to be confirmed with the user)
        r = self.save("2026-11", [{"date": "2026-11-01", "minutes": 450, "holiday": True}])
        labels = [row["label"] for row in r.get_json()["rows"]]
        self.assertIn("Holiday extra", labels)
        self.assertIn("Sunday premium", labels)

    def test_legacy_month_untouched_until_calendar_save(self):
        self.form("/income", work_month="2026-09", days="3", hours_override="", sundays="2", holiday_hours="")
        self.assertIn("€390.58", self.c.get("/?month=2026-10").get_data(as_text=True))
        conn = db.connect(self.path)
        self.assertEqual(db.get_income(conn, "2026-10")["per_day"], 0)


if __name__ == "__main__":
    unittest.main()
