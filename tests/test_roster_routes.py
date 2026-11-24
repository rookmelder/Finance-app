"""Tests for the Settings 'Roster from email' feature and routes in app.py."""
import base64
import datetime
import json
import os
import shutil
import tempfile
import time
import unittest
import unittest.mock
import urllib.parse

from src import config, db, gcal, gmail, roster_sync
from src.app import create_app


class FakeGoogle:
    """Answers Google's token + Calendar endpoints and remembers every call."""

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
    """Answers Gmail REST API endpoints (messages.list, messages.get, attachments.get)."""

    def __init__(self):
        self.calls = []
        self.messages = {}
        self.attachments = {}
        self.fail_status = None
        self.raise_exc = None

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if self.raise_exc:
            raise self.raise_exc
        if self.fail_status:
            return self.fail_status, json.dumps({"error": {"message": "Gmail error"}}).encode()

        parsed = urllib.parse.urlparse(url)
        path = parsed.path.replace("/gmail/v1/users/me", "")
        query = urllib.parse.parse_qs(parsed.query)

        # GET /messages (search)
        if method == "GET" and path == "/messages":
            limit = int(query.get("maxResults", [50])[0])
            msg_ids = list(self.messages.keys())[:limit]
            return 200, json.dumps({"messages": [{"id": mid} for mid in msg_ids]}).encode()

        # GET /messages/<mid>/attachments/<att_id>
        if method == "GET" and "/attachments/" in path:
            parts = path.split("/")
            mid = urllib.parse.unquote(parts[2])
            att_id = urllib.parse.unquote(parts[4])
            raw = self.attachments.get((mid, att_id), b"%PDF-1.4 fake")
            encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
            return 200, json.dumps({"data": encoded}).encode()

        # GET /messages/<mid>
        if method == "GET" and path.startswith("/messages/"):
            mid = urllib.parse.unquote(path.split("/messages/")[1])
            if mid in self.messages:
                return 200, json.dumps(self.messages[mid]).encode()
            return 404, json.dumps({"error": {"message": "Message not found"}}).encode()

        return 404, b'{"error": "not found"}'


class RosterRoutesBase(unittest.TestCase):
    """Base setup for roster route tests."""

    def setUp(self):
        gcal._token_cache.clear()
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "t.db")
        self.fake_gcal = FakeGoogle()
        self.fake_gmail = FakeGmailTransport()

        fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "roster_tokens.json")
        with open(fixture_path, "r", encoding="utf-8") as f:
            self.fixture_tokens = json.load(f)

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


class TestRosterAuthAndCsrf(RosterRoutesBase):
    """Test login and CSRF enforcement on roster POST routes."""

    def test_roster_settings_requires_login(self):
        anon = self.make_app(logged_in=False)
        anon.get("/login")
        token = self.csrf(anon)
        r = anon.post("/roster/settings", data={"sender": "planner@example.com", "name": "Name", "csrf_token": token})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_roster_settings_requires_csrf(self):
        c = self.make_app(logged_in=True)
        r_missing = c.post("/roster/settings", data={"sender": "planner@example.com", "name": "Name"})
        self.assertEqual(r_missing.status_code, 400)
        r_bad = c.post("/roster/settings", data={"sender": "planner@example.com", "name": "Name", "csrf_token": "bad"})
        self.assertEqual(r_bad.status_code, 400)

    def test_roster_sync_requires_login(self):
        anon = self.make_app(logged_in=False)
        anon.get("/login")
        token = self.csrf(anon)
        r = anon.post("/roster/sync", data={"csrf_token": token})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_roster_sync_requires_csrf(self):
        c = self.make_app(logged_in=True)
        r_missing = c.post("/roster/sync")
        self.assertEqual(r_missing.status_code, 400)
        r_bad = c.post("/roster/sync", data={"csrf_token": "bad"})
        self.assertEqual(r_bad.status_code, 400)


class TestRosterSettingsCardUI(RosterRoutesBase):
    """Test roster card visibility, inputs, warnings, and button states in settings.html."""

    def test_roster_card_hidden_when_google_not_connected(self):
        c = self.make_app(google=True)
        html = c.get("/settings").get_data(as_text=True)
        self.assertNotIn('id="roster"', html)
        self.assertNotIn("Roster from email", html)

    def test_roster_card_when_connected_without_gmail_scope(self):
        c = self.make_app(google=True)
        self.connect(c, gmail_scope=False)
        html = c.get("/settings").get_data(as_text=True)

        self.assertIn('id="roster"', html)
        self.assertIn('name="sender"', html)
        self.assertIn('name="name"', html)
        self.assertIn("Gmail access was not granted yet", html)
        self.assertNotIn("Gmail allowed", html)

        # Sync button must be disabled
        self.assertRegex(html, r'<button[^>]*type="submit"[^>]*disabled[^>]*>\s*Sync roster now\s*</button>')

    def test_roster_card_button_disabled_until_gmail_scope_sender_and_name_set(self):
        c = self.make_app(google=True)
        self.connect(c, gmail_scope=True)

        # 1. Both empty -> disabled
        html1 = c.get("/settings").get_data(as_text=True)
        self.assertIn("Gmail allowed", html1)
        self.assertNotIn("Gmail access was not granted yet", html1)
        self.assertRegex(html1, r'<button[^>]*type="submit"[^>]*disabled[^>]*>\s*Sync roster now\s*</button>')

        # 2. Only sender set -> disabled
        c.post("/roster/settings", data={"sender": "planner@example.com", "name": "", "csrf_token": self.csrf(c)})
        html2 = c.get("/settings").get_data(as_text=True)
        self.assertRegex(html2, r'<button[^>]*type="submit"[^>]*disabled[^>]*>\s*Sync roster now\s*</button>')

        # 3. Only name set -> disabled
        c.post("/roster/settings", data={"sender": "", "name": "Test Person", "csrf_token": self.csrf(c)})
        html3 = c.get("/settings").get_data(as_text=True)
        self.assertRegex(html3, r'<button[^>]*type="submit"[^>]*disabled[^>]*>\s*Sync roster now\s*</button>')

        # 4. Scope + sender + name set -> button is enabled (no disabled attribute)
        c.post("/roster/settings", data={"sender": "planner@example.com", "name": "Test Person", "csrf_token": self.csrf(c)})
        html4 = c.get("/settings").get_data(as_text=True)
        self.assertNotIn("disabled", [
            line for line in html4.splitlines() if "Sync roster now" in line
        ][0])


class TestRosterSettingsRoute(RosterRoutesBase):
    """Test POST /roster/settings field validation, whitespace collapsing, and storage."""

    def test_save_valid_sender_and_name(self):
        c = self.make_app(google=True)
        self.connect(c)
        r = c.post("/roster/settings", data={
            "sender": "  planner@example.com  ",
            "name": "   Test    Person   ",
            "csrf_token": self.csrf(c),
        })
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["Location"], "/settings?google=roster_saved#roster")

        conn = db.connect(self.db_path)
        cfg = db.get_google_config(conn)
        self.assertEqual(cfg["roster_sender"], "planner@example.com")
        self.assertEqual(cfg["roster_name"], "Test Person")

        # Saved values show in form and flash message appears
        html = c.get("/settings?google=roster_saved").get_data(as_text=True)
        self.assertIn('value="planner@example.com"', html)
        self.assertIn('value="Test Person"', html)
        self.assertIn("Roster settings saved.", html)

    def test_save_empty_sender_allowed(self):
        c = self.make_app(google=True)
        self.connect(c)
        r = c.post("/roster/settings", data={
            "sender": "",
            "name": "Test Person",
            "csrf_token": self.csrf(c),
        })
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["Location"], "/settings?google=roster_saved#roster")

        conn = db.connect(self.db_path)
        cfg = db.get_google_config(conn)
        self.assertEqual(cfg["roster_sender"], "")
        self.assertEqual(cfg["roster_name"], "Test Person")

    def test_reject_invalid_senders_and_nothing_saved(self):
        c = self.make_app(google=True)
        self.connect(c)
        conn = db.connect(self.db_path)
        db.update_google_config(conn, roster_sender="initial@example.com", roster_name="Initial Name")

        invalid_senders = [
            "from:x@y.be",
            "a b@y.be",
            'a"b@y.be',
            "<x@y.be>",
            "a,b@y.be",
            "plainaddress",
            "@missinguser.be",
            "user@.be",
            "user@domain",
        ]

        for bad in invalid_senders:
            with self.subTest(sender=bad):
                r = c.post("/roster/settings", data={
                    "sender": bad,
                    "name": "Changed Name",
                    "csrf_token": self.csrf(c),
                })
                self.assertEqual(r.status_code, 303)
                self.assertEqual(r.headers["Location"], "/settings?google=roster_bad_sender#roster")
                cfg = db.get_google_config(conn)
                self.assertEqual(cfg["roster_sender"], "initial@example.com")
                self.assertEqual(cfg["roster_name"], "Initial Name")

    def test_reject_name_over_80_chars_and_nothing_saved(self):
        c = self.make_app(google=True)
        self.connect(c)
        conn = db.connect(self.db_path)
        db.update_google_config(conn, roster_sender="initial@example.com", roster_name="Initial Name")

        # 81 chars
        bad_name = "A" * 81
        r = c.post("/roster/settings", data={
            "sender": "planner@example.com",
            "name": bad_name,
            "csrf_token": self.csrf(c),
        })
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["Location"], "/settings?google=roster_bad_name#roster")

        cfg = db.get_google_config(conn)
        self.assertEqual(cfg["roster_name"], "Initial Name")
        self.assertEqual(cfg["roster_sender"], "initial@example.com")

        # Exactly 80 chars is accepted
        good_name = "B" * 80
        r_ok = c.post("/roster/settings", data={
            "sender": "planner@example.com",
            "name": good_name,
            "csrf_token": self.csrf(c),
        })
        self.assertEqual(r_ok.status_code, 303)
        self.assertEqual(r_ok.headers["Location"], "/settings?google=roster_saved#roster")
        cfg_ok = db.get_google_config(conn)
        self.assertEqual(cfg_ok["roster_name"], good_name)

    def test_reject_name_with_control_characters_and_nothing_saved(self):
        c = self.make_app(google=True)
        self.connect(c)
        conn = db.connect(self.db_path)
        db.update_google_config(conn, roster_sender="initial@example.com", roster_name="Initial Name")

        bad_names = [
            "Test\x00Person",
            "Test\x07Person",
            "Test\x1bPerson",
            "Test\x1fPerson",
        ]

        for bad in bad_names:
            with self.subTest(name=bad):
                r = c.post("/roster/settings", data={
                    "sender": "planner@example.com",
                    "name": bad,
                    "csrf_token": self.csrf(c),
                })
                self.assertEqual(r.status_code, 303)
                self.assertEqual(r.headers["Location"], "/settings?google=roster_bad_name#roster")

                cfg = db.get_google_config(conn)
                self.assertEqual(cfg["roster_name"], "Initial Name")
                self.assertEqual(cfg["roster_sender"], "initial@example.com")


class TestGoogleCallbackScopes(RosterRoutesBase):
    """Test Google OAuth callback storing granted scopes."""

    def test_callback_stores_granted_scopes_in_google_sync(self):
        c = self.make_app(google=True)
        self.connect(c, gmail_scope=True)

        conn = db.connect(self.db_path)
        row = db.get_google(conn)
        self.assertIsNotNone(row)
        scopes = (row.get("scopes") or "").split()
        self.assertIn(gcal.SCOPE, scopes)
        self.assertIn(gcal.SCOPE_GMAIL, scopes)

    def test_callback_without_gmail_scope_still_connects_calendar(self):
        c = self.make_app(google=True)
        r = self.connect(c, gmail_scope=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn("google=connected", r.headers["Location"])

        conn = db.connect(self.db_path)
        row = db.get_google(conn)
        self.assertIsNotNone(row)
        self.assertEqual(row["refresh_token"], "ref-1")
        self.assertEqual(row["calendar_id"], "cal-new")

        scopes = (row.get("scopes") or "").split()
        self.assertIn(gcal.SCOPE, scopes)
        self.assertNotIn(gcal.SCOPE_GMAIL, scopes)


class TestRosterSyncRoute(RosterRoutesBase):
    """Test POST /roster/sync guard checks, exception handling, and UI report rendering."""

    def test_sync_when_not_connected_fails_with_message(self):
        c = self.make_app(google=True)
        r = c.post("/roster/sync", data={"csrf_token": self.csrf(c)})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["Location"], "/settings#roster")

        conn = db.connect(self.db_path)
        state = db.get_roster_state(conn)
        self.assertTrue(state["synced_at"])
        report = json.loads(state["report"])
        self.assertFalse(report["ok"])
        self.assertIn("Connect Google first", report["message"])

    def test_sync_when_connected_without_gmail_scope_fails_with_message(self):
        c = self.make_app(google=True)
        self.connect(c, gmail_scope=False)

        r = c.post("/roster/sync", data={"csrf_token": self.csrf(c)})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["Location"], "/settings#roster")

        conn = db.connect(self.db_path)
        state = db.get_roster_state(conn)
        report = json.loads(state["report"])
        self.assertFalse(report["ok"])
        self.assertIn("Gmail access was not granted", report["message"])

        # Shown in settings page
        html = c.get("/settings").get_data(as_text=True)
        self.assertIn("Gmail access was not granted", html)

    def test_sync_gmail_error_handled_gracefully_never_500(self):
        c = self.make_app(google=True)
        self.connect(c, gmail_scope=True)
        c.post("/roster/settings", data={
            "sender": "planner@example.com",
            "name": "Test Person",
            "csrf_token": self.csrf(c),
        })

        self.fake_gmail.fail_status = 500
        r = c.post("/roster/sync", data={"csrf_token": self.csrf(c)})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["Location"], "/settings#roster")

        conn = db.connect(self.db_path)
        state = db.get_roster_state(conn)
        report = json.loads(state["report"])
        self.assertFalse(report["ok"])
        self.assertIn("Gmail error 500", report["message"])

        html = c.get("/settings").get_data(as_text=True)
        self.assertIn("Gmail error 500", html)

    def test_sync_unexpected_exception_handled_gracefully_never_500(self):
        c = self.make_app(google=True)
        self.connect(c, gmail_scope=True)
        c.post("/roster/settings", data={
            "sender": "planner@example.com",
            "name": "Test Person",
            "csrf_token": self.csrf(c),
        })

        with unittest.mock.patch("src.app.roster_sync.sync", side_effect=RuntimeError("unexpected network drop")):
            r = c.post("/roster/sync", data={"csrf_token": self.csrf(c)})
            self.assertEqual(r.status_code, 303)
            self.assertEqual(r.headers["Location"], "/settings#roster")

        conn = db.connect(self.db_path)
        state = db.get_roster_state(conn)
        report = json.loads(state["report"])
        self.assertFalse(report["ok"])
        self.assertIn("Unexpected error while reading your mail (RuntimeError)", report["message"])

        html = c.get("/settings").get_data(as_text=True)
        self.assertIn("Unexpected error while reading your mail (RuntimeError)", html)

    def test_settings_page_renders_report_message_months_ignored_and_errors(self):
        c = self.make_app(google=True)
        self.connect(c, gmail_scope=True)

        fake_report = {
            "ok": True,
            "message": "Updated Nov 2026: 2 added.",
            "months": [
                {"work_month": "2026-11", "status": "updated", "detail": "2 added, 0 removed, 0 changed"},
                {"work_month": "2026-12", "status": "unchanged", "detail": "already up to date"},
            ],
            "ignored": 3,
            "errors": ["report.pdf: Corrupted PDF file"],
            "changed_months": [],
        }

        with unittest.mock.patch("src.app.roster_sync.sync", return_value=fake_report):
            r = c.post("/roster/sync", data={"csrf_token": self.csrf(c)})
            self.assertEqual(r.status_code, 303)

        html = c.get("/settings").get_data(as_text=True)
        self.assertIn("Updated Nov 2026: 2 added.", html)
        self.assertIn("November 2026: updated (2 added, 0 removed, 0 changed)", html)
        self.assertIn("December 2026: unchanged (already up to date)", html)
        self.assertIn("3 PDFs from that sender did not look like a roster and were ignored.", html)
        self.assertIn("Some mails could not be read, so a newer roster may be missing:", html)
        self.assertIn("report.pdf: Corrupted PDF file", html)

    def test_sync_gcal_sync_error_reported(self):
        c = self.make_app(google=True)
        self.connect(c, gmail_scope=True)

        fake_report = {
            "ok": True,
            "message": "Updated Dec 2026: 1 added.",
            "months": [{"work_month": "2026-12", "status": "updated", "detail": "1 added"}],
            "ignored": 0,
            "errors": [],
            "changed_months": ["2026-12"],
        }

        self.fake_gcal.fail_api = 500
        with unittest.mock.patch("src.app.roster_sync.sync", return_value=fake_report):
            r = c.post("/roster/sync", data={"csrf_token": self.csrf(c)})
            self.assertEqual(r.status_code, 303)

        conn = db.connect(self.db_path)
        state = db.get_roster_state(conn)
        report = json.loads(state["report"])
        self.assertEqual(report["gcal"], "error")

        html = c.get("/settings").get_data(as_text=True)
        self.assertIn("The days were saved, but updating Google Calendar failed.", html)


class TestRosterEndToEnd(RosterRoutesBase):
    """End-to-end sync using fixture tokens, shift creation, Google calendar sync, and idempotency."""

    def test_end_to_end_roster_sync_and_idempotence(self):
        c = self.make_app(google=True)
        self.connect(c, gmail_scope=True)

        # 1. Save roster sender and name
        r_settings = c.post("/roster/settings", data={
            "sender": "planner@example.com",
            "name": "Test Person",
            "csrf_token": self.csrf(c),
        })
        self.assertEqual(r_settings.status_code, 303)

        # 2. Configure fake Gmail with a message holding a PDF attachment
        self.fake_gmail.messages["msg-1"] = {
            "id": "msg-1",
            "internalDate": "1732406400000",
            "payload": {
                "headers": [
                    {"name": "from", "value": "Planner Boss <planner@example.com>"},
                    {"name": "subject", "value": "Roster December 2026"},
                ],
                "parts": [
                    {
                        "filename": "Roster_2026_12.pdf",
                        "mimeType": "application/pdf",
                        "body": {"attachmentId": "att-1", "size": 1234},
                    }
                ],
            },
        }
        self.fake_gmail.attachments[("msg-1", "att-1")] = b"%PDF-1.4 fake attachment content"

        # 3. Patch sync defaults to wrap real_sync with fixture tokens and mocked today date
        real_sync = roster_sync.sync
        tokens = self.fixture_tokens

        with unittest.mock.patch(
            "src.app.roster_sync.sync",
            side_effect=lambda conn, gm, today, **kw: real_sync(
                conn, gm, datetime.date(2026, 11, 24), extract_tokens=lambda data: tokens, **kw
            )
        ):
            # First sync run
            r_sync1 = c.post("/roster/sync", data={"csrf_token": self.csrf(c)})
            self.assertEqual(r_sync1.status_code, 303)
            self.assertEqual(r_sync1.headers["Location"], "/settings#roster")

            conn = db.connect(self.db_path)
            state1 = db.get_roster_state(conn)
            self.assertTrue(state1["synced_at"])
            rep1 = json.loads(state1["report"])
            self.assertTrue(rep1["ok"])
            self.assertEqual(rep1["changed_months"], ["2026-12"])
            self.assertEqual(rep1["gcal"], "ok")

            # Check 8 shift days were added with codes D and V
            days1 = db.list_days(conn, "2026-12")
            self.assertEqual(len(days1), 8)
            self.assertEqual({d["code"] for d in days1}, {"D", "V"})

            # Check calendar events were created in Google Calendar
            self.assertTrue(len(self.fake_gcal.events) >= 8)

            # Check settings UI rendering
            html1 = c.get("/settings").get_data(as_text=True)
            self.assertIn("Updated Dec 2026: 8 added, 0 removed, 0 changed.", html1)
            self.assertIn("December 2026: updated (8 added, 0 removed, 0 changed)", html1)

            # Second sync run (idempotent / unchanged)
            r_sync2 = c.post("/roster/sync", data={"csrf_token": self.csrf(c)})
            self.assertEqual(r_sync2.status_code, 303)

            state2 = db.get_roster_state(conn)
            rep2 = json.loads(state2["report"])
            self.assertTrue(rep2["ok"])
            self.assertEqual(rep2["changed_months"], [])
            self.assertEqual(rep2["message"], "Everything is up to date.")
            self.assertEqual(rep2["months"], [
                {"work_month": "2026-12", "status": "unchanged", "detail": "already up to date"}
            ])

            # Days in DB must be untouched
            days2 = db.list_days(conn, "2026-12")
            self.assertEqual(days2, days1)

            # Settings UI rendering after second sync
            html2 = c.get("/settings").get_data(as_text=True)
            self.assertIn("Everything is up to date.", html2)
            self.assertIn("December 2026: unchanged (already up to date)", html2)


class TestReconsentKeepsCalendar(RosterRoutesBase):
    """Granting Gmail access later must reuse the existing "Work" calendar, not create a second one."""

    def test_allow_gmail_button_reconnects_in_place(self):
        c = self.make_app(google=True)
        self.connect(c, gmail_scope=False)
        conn = db.connect(self.db_path)
        db.update_google(conn, calendar_id="cal-1")
        html = c.get("/settings").get_data(as_text=True)
        self.assertIn("Allow Gmail access", html)
        self.assertNotIn("Disconnect Google, connect again", html)
        self.fake_gcal.calls.clear()

        self.connect(c, gmail_scope=True)  # same POST /google/connect + callback the button triggers

        row = db.get_google(db.connect(self.db_path))
        self.assertEqual(row["calendar_id"], "cal-1")
        self.assertIn(gcal.SCOPE_GMAIL, row["scopes"].split())
        created = [call for call in self.fake_gcal.calls
                   if call[0] == "POST" and call[1].endswith("/calendars")]
        self.assertEqual(created, [])


class TestRosterProgressJob(RosterRoutesBase):
    """The fetch flavour of /roster/sync (background job + /roster/progress) and the per-month option."""

    HEADERS = {"X-Requested-With": "fetch"}

    def prepare(self):
        c = self.make_app(google=True)
        self.connect(c, gmail_scope=True)
        c.post("/roster/settings", data={"sender": "planner@example.com", "name": "Test Person",
                                         "csrf_token": self.csrf(c)})
        return c

    def test_progress_requires_login(self):
        anon = self.make_app(logged_in=False)
        r = anon.get("/roster/progress")
        self.assertEqual(r.status_code, 302)

    def test_progress_idle_state(self):
        c = self.make_app(google=True)
        state = c.get("/roster/progress").get_json()
        self.assertEqual(set(state), {"running", "percent", "text", "done", "ok", "message", "gcal", "work_month"})
        self.assertIn(state["running"], (False,))

    def test_fetch_sync_not_ready_is_400_json(self):
        c = self.make_app(google=True)
        r = c.post("/roster/sync", data={"csrf_token": self.csrf(c)}, headers=self.HEADERS)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()["ok"])

    def test_fetch_sync_runs_and_reports_progress(self):
        c = self.prepare()
        real_sync = roster_sync.sync
        seen = {}

        def fake_sync(conn, gm, today, **kw):
            seen["only_month"] = kw.get("only_month")
            kw["progress"](50, "Reading mail 1 of 1...")
            return {"ok": True, "message": "No roster found in your mail.", "months": [], "ignored": 0,
                    "errors": [], "changed_months": []}

        with unittest.mock.patch("src.app.roster_sync.sync", side_effect=fake_sync):
            r = c.post("/roster/sync", data={"csrf_token": self.csrf(c), "work_month": "2026-12"},
                       headers=self.HEADERS)
            self.assertEqual(r.get_json(), {"ok": True, "started": True})
            state = c.get("/roster/progress").get_json()   # a real file db: the job runs in a thread, wait for it
            for _ in range(100):
                if state["done"]:
                    break
                time.sleep(0.05)
                state = c.get("/roster/progress").get_json()
        self.assertTrue(state["done"])
        self.assertFalse(state["running"])
        self.assertTrue(state["ok"])
        self.assertEqual(state["percent"], 100)
        self.assertEqual(state["work_month"], "2026-12")
        self.assertEqual(seen["only_month"], "2026-12")
        self.assertIn("No roster", state["message"])
        report = json.loads(db.get_roster_state(db.connect(self.db_path))["report"])
        self.assertTrue(report["ok"])

    def test_invalid_month_rejected(self):
        c = self.prepare()
        r = c.post("/roster/sync", data={"csrf_token": self.csrf(c), "work_month": "2026-13"}, headers=self.HEADERS)
        self.assertEqual(r.status_code, 400)

    def test_second_start_while_running_is_409(self):
        from src import app as app_module
        c = self.prepare()
        with app_module._roster_job["lock"]:
            app_module._roster_job["running"] = True
        try:
            r = c.post("/roster/sync", data={"csrf_token": self.csrf(c)}, headers=self.HEADERS)
            self.assertEqual(r.status_code, 409)
        finally:
            with app_module._roster_job["lock"]:
                app_module._roster_job["running"] = False

    def test_plain_form_with_month_still_redirects(self):
        c = self.prepare()
        with unittest.mock.patch("src.app.roster_sync.sync", return_value={
                "ok": True, "message": "x", "months": [], "ignored": 0, "errors": [], "changed_months": []}) as m:
            r = c.post("/roster/sync", data={"csrf_token": self.csrf(c), "work_month": "2026-12"})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(m.call_args.kwargs.get("only_month"), "2026-12")

    def test_months_page_shows_sync_buttons_only_when_ready(self):
        c = self.make_app(google=True)
        self.assertNotIn("data-roster-sync", c.get("/income").get_data(as_text=True))
        c = self.prepare()
        html = c.get("/income").get_data(as_text=True)
        self.assertIn("data-roster-sync", html)
        self.assertIn('name="work_month"', html)


if __name__ == "__main__":
    unittest.main()
