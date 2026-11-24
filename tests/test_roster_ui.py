"""Tests for the Roster sync UI, progress bar script, and template integration."""

import json
import os
import re
import shutil
import tempfile
import unittest
import urllib.parse

from src import db, gcal
from src.app import create_app


class FakeGoogle:
    """Answers Google's token + Calendar endpoints."""

    def __init__(self, granted_scopes=None):
        self.calls = []
        self.events = {}
        self.calendars = {"cal-1"}
        self.next_id = 0
        self.fail_api = None
        self.refresh_ok = True
        self.granted_scopes = (
            granted_scopes
            if granted_scopes is not None
            else f"{gcal.SCOPE} {gcal.SCOPE_GMAIL}"
        )

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if url.startswith(gcal.TOKEN_URL):
            raw_body = body.decode() if isinstance(body, bytes) else (body or "")
            form = urllib.parse.parse_qs(raw_body)
            if form.get("grant_type") == ["authorization_code"]:
                if form.get("code") == ["bad"]:
                    return 400, b'{"error": "invalid_grant"}'
                return 200, json.dumps({
                    "access_token": "acc-1",
                    "refresh_token": "ref-1",
                    "expires_in": 3600,
                    "scope": self.granted_scopes,
                }).encode()
            if not self.refresh_ok:
                return 400, b'{"error": "invalid_grant"}'
            return 200, b'{"access_token": "acc-2", "expires_in": 3600}'
        if url.startswith(gcal.REVOKE_URL):
            return 200, b""
        if self.fail_api:
            return self.fail_api, b'{"error": {"message": "boom"}}'
        path = urllib.parse.urlparse(url).path.replace("/calendar/v3", "")
        if method == "POST" and path == "/calendars":
            self.calendars.add("cal-new")
            return 200, b'{"id": "cal-new"}'
        parts = path.split("/")
        cal = urllib.parse.unquote(parts[2])
        if cal not in self.calendars:
            return 404, b'{"error": {"message": "Not Found"}}'
        if method == "GET":
            items = [e for e in self.events.values() if e.get("cal") == cal]
            return 200, json.dumps({"items": items}).encode()
        if method == "POST":
            self.next_id += 1
            ev = dict(json.loads(body), id=f"e{self.next_id}", cal=cal)
            self.events[ev["id"]] = ev
            return 200, json.dumps(ev).encode()
        ev_id = urllib.parse.unquote(parts[4])
        if method == "PUT":
            if ev_id not in self.events:
                return 404, b'{"error": {"message": "Not Found"}}'
            ev = dict(json.loads(body), id=ev_id, cal=cal)
            self.events[ev_id] = ev
            return 200, json.dumps(ev).encode()
        if method == "DELETE":
            if ev_id not in self.events:
                return 410, b""
            del self.events[ev_id]
            return 204, b""
        return 500, b""


class FakeGmailTransport:
    """Answers Gmail endpoints."""

    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        return 200, b'{"messages": []}'


class TestRosterUI(unittest.TestCase):
    def setUp(self):
        gcal._token_cache.clear()
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "t.db")
        self.fake_gcal = FakeGoogle()
        self.fake_gmail = FakeGmailTransport()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_app(self, google=True, logged_in=True):
        cfg = {
            "PASSWORD": "pw",
            "SECRET_KEY": "x" * 32,
            "DB_PATH": self.db_path,
            "HOST": "127.0.0.1",
            "PORT": 8080,
            "GCAL_TRANSPORT": self.fake_gcal,
            "GMAIL_TRANSPORT": self.fake_gmail,
        }
        if google:
            cfg.update(
                GOOGLE_CLIENT_ID="cid",
                GOOGLE_CLIENT_SECRET="sec",
                GOOGLE_REDIRECT_URI="https://fin.example/google/callback",
            )
        app = create_app(cfg)
        c = app.test_client()
        if logged_in:
            c.get("/login")
            c.post("/login", data={"password": "pw", "csrf_token": self.csrf(c)})
        return c

    def csrf(self, c):
        with c.session_transaction() as s:
            return s.get("csrf", "")

    def connect(self, c, gmail_scope=True):
        if gmail_scope:
            self.fake_gcal.granted_scopes = f"{gcal.SCOPE} {gcal.SCOPE_GMAIL}"
        else:
            self.fake_gcal.granted_scopes = gcal.SCOPE
        r = c.post("/google/connect", data={"csrf_token": self.csrf(c)})
        self.assertEqual(r.status_code, 303)
        state = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["Location"]).query)["state"][0]
        return c.get("/google/callback", query_string={"state": state, "code": "good"})

    def test_rostersync_js_served_and_safe(self):
        """rostersync.js is served (/static/rostersync.js 200) and contains required keywords

        and none of innerHTML, outerHTML, document.write, eval(.
        """
        c = self.make_app(google=False, logged_in=False)
        r = c.get("/static/rostersync.js")
        self.assertEqual(r.status_code, 200)
        js = r.get_data(as_text=True)

        self.assertIn("data-roster-sync", js)
        self.assertIn("/roster/progress", js)
        self.assertIn("X-Requested-With", js)

        # DOM safety checks: forbidden APIs
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("outerHTML", js)
        self.assertNotIn("document.write", js)
        self.assertNotIn("eval(", js)

        # Accessible progress bar checks
        self.assertIn("progressbar", js)
        self.assertIn("aria-valuenow", js)
        self.assertIn("aria-live", js)

    def test_settings_page_roster_card_ui(self):
        """The settings page (logged in, Google configured and connected) contains

        data-roster-sync, data-rs-panel and the script tag.
        """
        c = self.make_app(google=True, logged_in=True)
        cb = self.connect(c, gmail_scope=True)
        self.assertEqual(cb.status_code, 303)

        r = c.get("/settings")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)

        self.assertIn("data-roster-sync", html)
        self.assertIn("data-rs-panel", html)
        self.assertIn("/static/rostersync.js", html)

        # Verify data-rs-panel is right above the data-roster-sync form
        panel_pos = html.find("data-rs-panel")
        form_pos = html.find("data-roster-sync")
        self.assertTrue(panel_pos != -1)
        self.assertTrue(form_pos != -1)
        self.assertLess(panel_pos, form_pos)

    def test_income_page_without_roster_ready(self):
        """With roster_ready absent/false no data-roster-sync form is rendered on /income.

        The page still lists all month tiles with .mini-month and id='current' once,
        and includes the script tag.
        """
        c = self.make_app(google=False, logged_in=True)
        r = c.get("/income")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)

        # Month tiles are listed
        self.assertIn('class="mini-month', html)
        self.assertEqual(html.count('id="current"'), 1)
        self.assertIn('class="mini-wrap"', html)

        # No sync form rendered because Google is not connected / roster_ready is false
        self.assertNotIn("data-roster-sync", html)
        self.assertNotIn('class="mini-sync"', html)

        # Script tag is included
        self.assertIn("/static/rostersync.js", html)

    def test_income_page_with_roster_ready(self):
        """When roster_ready is true, each month tile has a mini-sync form with

        data-roster-sync, hidden csrf_token, hidden work_month, and a 'Sync roster' button.
        """
        c = self.make_app(google=True, logged_in=True)
        cb = self.connect(c, gmail_scope=True)
        self.assertEqual(cb.status_code, 303)

        # Configure sender and name so _roster_ready returns True
        conn = db.connect(self.db_path)
        db.update_google_config(conn, roster_sender="planner@example.com", roster_name="Jane Doe")
        conn.close()

        r = c.get("/income")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)

        # Month tiles are listed and wrapped in mini-wrap
        self.assertIn('class="mini-month', html)
        self.assertEqual(html.count('id="current"'), 1)
        self.assertIn('class="mini-wrap"', html)

        # Sync forms are rendered with data-roster-sync
        self.assertIn("data-roster-sync", html)
        self.assertIn('class="mini-sync"', html)
        self.assertIn('name="work_month"', html)
        self.assertIn("Sync roster", html)
        self.assertIn('Look for the roster of', html)

        # Script tag is included
        self.assertIn("/static/rostersync.js", html)


if __name__ == "__main__":
    unittest.main()
