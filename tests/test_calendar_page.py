"""Tests for the Calendar UI and page endpoints."""

import json
import os
import re
import shutil
import tempfile
import unittest

from src.app import create_app


class TestCalendarPage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "t.db")
        self.app = create_app({
            "PASSWORD": "pw",
            "SECRET_KEY": "x" * 32,
            "DB_PATH": self.db_path,
            "HOST": "127.0.0.1",
            "PORT": 8080,
        })
        self.c = self.app.test_client()
        self.c.get("/login")
        self.post("/login", password="pw")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def post(self, url, **data):
        with self.c.session_transaction() as s:
            data["csrf_token"] = s["csrf"]
        return self.c.post(url, data=data)

    def test_calendar_page_and_cal_data_json(self):
        """GET /calendar/2026-11 is 200 and cal-data has correct metadata and presets."""
        resp = self.c.get("/calendar/2026-11")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="cal-data"', html)

        match = re.search(r'<script[^>]*id="cal-data"[^>]*>(.*?)</script>', html, re.DOTALL)
        self.assertIsNotNone(match)
        data = json.loads(match.group(1))

        self.assertEqual(data["weekday_offset"], 6)
        self.assertEqual(data["days_in_month"], 30)
        self.assertIn("2026-11-01", data["holidays"])
        self.assertIn("2026-11-11", data["holidays"])
        self.assertEqual(len(data["presets"]), 3)

    def test_invalid_calendar_month_404(self):
        """GET /calendar/2026-13 and /calendar/garbage return 404."""
        self.assertEqual(self.c.get("/calendar/2026-13").status_code, 404)
        self.assertEqual(self.c.get("/calendar/garbage").status_code, 404)

    def test_calendar_requires_login(self):
        """Calendar page requires authentication (302 to /login)."""
        unauth_client = self.app.test_client()
        resp = unauth_client.get("/calendar/2026-11")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_calendar_js_included_and_served(self):
        """The page includes calendar.js and /static/calendar.js returns 200."""
        resp = self.c.get("/calendar/2026-11")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("calendar.js", html)

        js_resp = self.c.get("/static/calendar.js")
        self.assertEqual(js_resp.status_code, 200)

    def test_calendar_js_forbidden_strings(self):
        """calendar.js contains none of innerHTML, outerHTML, document.write, eval(."""
        static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "static")
        js_path = os.path.join(static_dir, "calendar.js")
        with open(js_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertNotIn("innerHTML", content)
        self.assertNotIn("outerHTML", content)
        self.assertNotIn("document.write", content)
        self.assertNotIn("eval(", content)

    def test_income_page_month_list_and_details_legacy_form(self):
        """The /income page lists the month label 'October 2026' as a link to /calendar/2026-10 with € amounts and legacy form inside <details>."""
        r = self.post("/income", work_month="2026-10", days="3")
        self.assertEqual(r.status_code, 303)

        resp = self.c.get("/income")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn("October 2026", html)
        self.assertIn('href="/calendar/2026-10"', html)
        self.assertIn("€", html)

        details_match = re.search(r'<details[^>]*>(.*?)</details>', html, re.DOTALL)
        self.assertIsNotNone(details_match)
        details_content = details_match.group(1)
        self.assertIn("<form", details_content)
        self.assertIn('name="work_month"', details_content)
        self.assertIn('name="days"', details_content)
        self.assertIn("Add by totals", details_content)

    def test_calendar_save_and_entries_embed(self):
        """POSTing /calendar/2026-10/save JSON with CSRF saves days, which appear in GET /calendar/2026-10 entries."""
        with self.c.session_transaction() as s:
            csrf = s["csrf"]

        save_resp = self.c.post(
            "/calendar/2026-10/save",
            json={
                "days": [
                    {"date": "2026-10-05", "minutes": 450, "holiday": False},
                    {"date": "2026-10-06", "minutes": 390, "holiday": False},
                ]
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(save_resp.status_code, 200)
        save_json = save_resp.get_json()
        self.assertTrue(save_json["ok"])
        self.assertEqual(save_json["days"], 2)

        resp = self.c.get("/calendar/2026-10")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        match = re.search(r'<script[^>]*id="cal-data"[^>]*>(.*?)</script>', html, re.DOTALL)
        self.assertIsNotNone(match)
        cal_data = json.loads(match.group(1))

        self.assertEqual(len(cal_data["entries"]), 2)
        dates = [e["date"] for e in cal_data["entries"]]
        self.assertIn("2026-10-05", dates)
        self.assertIn("2026-10-06", dates)


if __name__ == "__main__":
    unittest.main()
