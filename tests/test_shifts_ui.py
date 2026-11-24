"""HTTP and source checks for shifts on the calendar page."""

import json
import os
import re
import secrets
import tempfile
import unittest
from unittest.mock import patch

from src.app import create_app


class TestShiftsUI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        environment = patch.dict(os.environ, {
            "PASSWORD": secrets.token_urlsafe(24),
            "SECRET_KEY": secrets.token_hex(32),
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.app = create_app({
            "PASSWORD": os.environ["PASSWORD"],
            "SECRET_KEY": os.environ["SECRET_KEY"],
            "DB_PATH": os.path.join(self.temp_dir.name, "shifts.db"),
            "HOST": "127.0.0.1",
            "PORT": 8080,
        })
        self.c = self.app.test_client()
        self.c.get("/login")
        with self.c.session_transaction() as session:
            csrf = session["csrf"]
        response = self.c.post("/login", data={
            "password": os.environ["PASSWORD"],
            "csrf_token": csrf,
        })
        self.assertIn(response.status_code, (302, 303))

    def calendar_data(self):
        response = self.c.get("/calendar/2026-10")
        self.assertEqual(response.status_code, 200)
        match = re.search(
            r'<script[^>]*id="cal-data"[^>]*>(.*?)</script>',
            response.get_data(as_text=True),
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        return json.loads(match.group(1))

    def save_days(self, days):
        with self.c.session_transaction() as session:
            csrf = session["csrf"]
        response = self.c.post(
            "/calendar/2026-10/save",
            json={"days": days},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])

    def test_calendar_data_contains_shifts(self):
        shifts = self.calendar_data()["shifts"]
        self.assertEqual({shift["code"] for shift in shifts}, {"V", "D", "L"})
        for shift in shifts:
            for field in ("start", "end", "minutes"):
                self.assertIsInstance(shift[field], int)

    def test_save_code_fills_times_and_null_clears_shift(self):
        shift = next(item for item in self.calendar_data()["shifts"] if item["code"] == "V")
        self.save_days([{
            "date": "2026-10-05",
            "minutes": shift["minutes"],
            "holiday": False,
            "code": "v",
        }])
        entries = self.calendar_data()["entries"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["date"], "2026-10-05")
        self.assertEqual(entry["minutes"], shift["minutes"])
        self.assertFalse(entry["holiday"])
        for field in ("code", "start", "end"):
            self.assertEqual(entry[field], shift[field])

        # A full unchanged payload preserves the stored shift snapshot.
        self.save_days([{
            key: entry[key]
            for key in ("date", "minutes", "holiday", "code", "start", "end")
        }])
        preserved = self.calendar_data()["entries"][0]
        for field in ("code", "start", "end"):
            self.assertEqual(preserved[field], entry[field])

        self.save_days([{
            "date": entry["date"],
            "minutes": entry["minutes"],
            "holiday": False,
            "code": None,
        }])
        cleared = self.calendar_data()["entries"][0]
        self.assertEqual(cleared["minutes"], entry["minutes"])
        for field in ("code", "start", "end"):
            self.assertIsNone(cleared[field])

    def test_calendar_javascript_uses_shifts_and_safe_dom_methods(self):
        response = self.c.get("/static/calendar.js")
        self.assertEqual(response.status_code, 200)
        source = response.get_data(as_text=True)
        self.assertIn("shifts", source)
        self.assertIn("code", source)
        for forbidden in ("innerHTML", "outerHTML", "document.write", "eval("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
