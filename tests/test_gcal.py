"""Google Calendar sync: gcal module and app routes, with a fake transport (no network)."""
import json
import os
import shutil
import tempfile
import unittest
import urllib.parse

from src import config, db, gcal
from src.app import create_app


class FakeGoogle:
    """Answers like Google's token + Calendar endpoints and remembers every call."""

    def __init__(self):
        self.calls = []
        self.events = {}  # id -> event
        self.calendars = {"cal-1"}
        self.next_id = 0
        self.fail_api = None  # status to return for every Calendar API call
        self.refresh_ok = True

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if url.startswith(gcal.TOKEN_URL):
            form = urllib.parse.parse_qs(body.decode())
            if form["grant_type"] == ["authorization_code"]:
                if form["code"] == ["bad"]:
                    return 400, b'{"error": "invalid_grant"}'
                return 200, json.dumps({"access_token": "acc-1", "refresh_token": "ref-1", "expires_in": 3600,
                                        "scope": gcal.SCOPE}).encode()
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
        parts = path.split("/")  # ['', 'calendars', cal, 'events', (id)]
        cal = urllib.parse.unquote(parts[2])
        if cal not in self.calendars:
            return 404, b'{"error": {"message": "Not Found"}}'
        if method == "GET":
            items = [e for e in self.events.values() if e["cal"] == cal]
            return 200, json.dumps({"items": items}).encode()
        if method == "POST":
            self.next_id += 1
            ev = dict(json.loads(body), id=f"e{self.next_id}", cal=cal)
            self.events[ev["id"]] = ev
            return 200, json.dumps(ev).encode()
        ev_id = urllib.parse.unquote(parts[4])
        if method == "PATCH":
            self.events[ev_id].update(json.loads(body))
            return 200, json.dumps(self.events[ev_id]).encode()
        if method == "DELETE":
            if ev_id not in self.events:
                return 410, b""
            del self.events[ev_id]
            return 204, b""
        return 500, b""

    def summaries(self):
        return sorted((e["start"]["date"], e["summary"]) for e in self.events.values())


def day(date, minutes=450, holiday=False):
    return {"date": date, "minutes": minutes, "holiday": holiday}


class TestGcalModule(unittest.TestCase):
    def setUp(self):
        gcal._token_cache.clear()

    def test_auth_url_has_state_offline_consent_and_narrow_scope(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(gcal.auth_url("cid", "https://x/cb", "st8")).query)
        self.assertEqual(q["state"], ["st8"])
        self.assertEqual(q["access_type"], ["offline"])
        self.assertEqual(q["prompt"], ["consent"])
        self.assertEqual(q["scope"], ["https://www.googleapis.com/auth/calendar.app.created"])
        self.assertEqual(q["redirect_uri"], ["https://x/cb"])

    def test_event_body_all_day_with_exclusive_end_and_tag(self):
        b = gcal.event_body(day("2026-12-31", 390, True))
        self.assertEqual(b["summary"], "Holiday 6.5h")
        self.assertEqual(b["start"], {"date": "2026-12-31"})
        self.assertEqual(b["end"], {"date": "2027-01-01"})
        self.assertEqual(b["extendedProperties"]["private"]["financeDate"], "2026-12-31")
        self.assertEqual(gcal.event_body(day("2026-11-02"))["summary"], "Work 7.5h")

    def test_plan_month_insert_patch_delete_and_duplicates(self):
        def ev(i, date, summary):
            b = gcal.event_body(day(date))
            b.update(id=i, summary=summary)
            return b
        events = [
            ev("a", "2026-11-02", "Work 7.5h"),     # unchanged
            ev("b", "2026-11-03", "Work 7.5h"),     # hours changed -> patch
            ev("c", "2026-11-04", "Work 7.5h"),     # day removed -> delete
            ev("d", "2026-11-02", "Work 7.5h"),     # duplicate -> delete
            ev("x", "2026-10-31", "Work 7.5h"),     # other month -> untouched
        ]
        days = [day("2026-11-02"), day("2026-11-03", 390), day("2026-11-05")]
        inserts, patches, deletes = gcal.plan_month("2026-11", days, events)
        self.assertEqual([b["start"]["date"] for b in inserts], ["2026-11-05"])
        self.assertEqual([(i, b["summary"]) for i, b in patches], [("b", "Work 6.5h")])
        self.assertEqual(sorted(deletes), ["c", "d"])

    def test_sync_month_is_idempotent(self):
        fake = FakeGoogle()
        client = gcal.Client("cid", "sec", "ref-1", fake)
        days = [day("2026-11-02"), day("2026-11-11", 450, True)]
        self.assertEqual(gcal.sync_month(client, "cal-1", "2026-11", days), {"inserted": 2, "patched": 0, "deleted": 0})
        self.assertEqual(gcal.sync_month(client, "cal-1", "2026-11", days), {"inserted": 0, "patched": 0, "deleted": 0})
        self.assertEqual(fake.summaries(), [("2026-11-02", "Work 7.5h"), ("2026-11-11", "Holiday 7.5h")])
        gcal.sync_month(client, "cal-1", "2026-11", [])
        self.assertEqual(fake.events, {})

    def test_token_refreshed_once_and_cached(self):
        fake = FakeGoogle()
        client = gcal.Client("cid", "sec", "ref-1", fake)
        client.list_tagged_events("cal-1", "2026-11-01", "2026-12-01")
        client.list_tagged_events("cal-1", "2026-11-01", "2026-12-01")
        token_calls = [c for c in fake.calls if c[1] == gcal.TOKEN_URL]
        self.assertEqual(len(token_calls), 1)
        api_calls = [c for c in fake.calls if c[1].startswith(gcal.API)]
        self.assertTrue(all(c[2]["Authorization"] == "Bearer acc-2" for c in api_calls))

    def test_revoked_refresh_token_raises_auth_revoked(self):
        fake = FakeGoogle()
        fake.refresh_ok = False
        with self.assertRaises(gcal.AuthRevoked):
            gcal.Client("cid", "sec", "ref-x", fake).list_tagged_events("cal-1", "2026-11-01", "2026-12-01")

    def test_exchange_code_errors(self):
        fake = FakeGoogle()
        with self.assertRaises(gcal.GcalError):
            gcal.exchange_code("cid", "sec", "https://x/cb", "bad", fake)
        self.assertEqual(gcal.exchange_code("cid", "sec", "https://x/cb", "good", fake), "ref-1")


class TestGoogleConfig(unittest.TestCase):
    BASE = {"FINANCE_PASSWORD": "pw", "SECRET_KEY": "x" * 32}

    def test_none_is_fine_and_partial_is_an_error(self):
        self.assertEqual(config.load(self.BASE)["GOOGLE_CLIENT_ID"], "")
        with self.assertRaises(config.ConfigError):
            config.load(dict(self.BASE, GOOGLE_CLIENT_ID="a"))
        with self.assertRaises(config.ConfigError):
            config.load(dict(self.BASE, GOOGLE_CLIENT_ID="a", GOOGLE_CLIENT_SECRET="b", GOOGLE_REDIRECT_URI="http://evil/cb"))
        with self.assertRaises(config.ConfigError):
            config.load(dict(self.BASE, GOOGLE_CLIENT_ID="a", GOOGLE_CLIENT_SECRET="b",
                             GOOGLE_REDIRECT_URI="http://localhost.evil.com/cb"))
        cfg = config.load(dict(self.BASE, GOOGLE_CLIENT_ID="a", GOOGLE_CLIENT_SECRET="b", GOOGLE_REDIRECT_URI="https://x/cb"))
        self.assertEqual(cfg["GOOGLE_REDIRECT_URI"], "https://x/cb")


class TestGoogleRoutes(unittest.TestCase):
    def make_app(self, google=True):
        cfg = {"PASSWORD": "pw", "SECRET_KEY": "x" * 32, "DB_PATH": self.db_path, "HOST": "127.0.0.1", "PORT": 8080}
        if google:
            cfg.update(GOOGLE_CLIENT_ID="cid", GOOGLE_CLIENT_SECRET="sec",
                       GOOGLE_REDIRECT_URI="https://fin.example/google/callback", GCAL_TRANSPORT=self.fake)
        app = create_app(cfg)
        c = app.test_client()
        c.get("/login")
        c.post("/login", data={"password": "pw", "csrf_token": self.csrf(c)})
        return c

    def csrf(self, c):
        with c.session_transaction() as s:
            return s["csrf"]

    def setUp(self):
        gcal._token_cache.clear()
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "t.db")
        self.fake = FakeGoogle()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save_days(self, c, month, days):
        return c.post(f"/calendar/{month}/save", json={"days": days}, headers={"X-CSRF-Token": self.csrf(c)})

    def connect(self, c):
        r = c.post("/google/connect", data={"csrf_token": self.csrf(c)})
        self.assertEqual(r.status_code, 303)
        state = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["Location"]).query)["state"][0]
        return c.get("/google/callback", query_string={"state": state, "code": "good"})

    def test_feature_hidden_without_config(self):
        c = self.make_app(google=False)
        html = c.get("/settings").get_data(as_text=True)
        self.assertIn("Not set up yet", html)
        self.assertNotIn("Connect Google Calendar", html)
        self.assertEqual(c.post("/google/connect", data={"csrf_token": self.csrf(c)}).status_code, 404)
        self.assertEqual(c.get("/google/callback?state=a&code=b").status_code, 404)
        r = self.save_days(c, "2026-11", [day("2026-11-02")])
        self.assertIsNone(r.get_json()["gcal"])

    def test_callback_needs_login(self):
        self.make_app()
        anon = create_app({"PASSWORD": "pw", "SECRET_KEY": "x" * 32, "DB_PATH": self.db_path, "HOST": "h", "PORT": 1,
                           "GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "sec",
                           "GOOGLE_REDIRECT_URI": "https://x/cb", "GCAL_TRANSPORT": self.fake}).test_client()
        r = anon.get("/google/callback?state=a&code=good")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_connect_needs_csrf(self):
        c = self.make_app()
        self.assertEqual(c.post("/google/connect").status_code, 400)

    def test_bad_or_missing_state_rejected(self):
        c = self.make_app()
        r = c.get("/google/callback?state=forged&code=good")
        self.assertIn("google=badstate", r.headers["Location"])
        c.post("/google/connect", data={"csrf_token": self.csrf(c)})
        r = c.get("/google/callback?state=forged&code=good")
        self.assertIn("google=badstate", r.headers["Location"])
        conn = db.connect(self.db_path)
        self.assertIsNone(db.get_google(conn))
        self.assertFalse(any(call[1] == gcal.TOKEN_URL for call in self.fake.calls))

    def test_state_is_single_use(self):
        c = self.make_app()
        r = c.post("/google/connect", data={"csrf_token": self.csrf(c)})
        state = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["Location"]).query)["state"][0]
        c.get("/google/callback", query_string={"state": state, "error": "access_denied"})
        r = c.get("/google/callback", query_string={"state": state, "code": "good"})
        self.assertIn("google=badstate", r.headers["Location"])

    def test_connect_creates_calendar_and_pushes_existing_days(self):
        c = self.make_app()
        self.save_days(c, "2026-11", [day("2026-11-02"), day("2026-11-03", 390)])
        r = self.connect(c)
        self.assertIn("google=connected", r.headers["Location"])
        conn = db.connect(self.db_path)
        row = db.get_google(conn)
        self.assertEqual(row["refresh_token"], "ref-1")
        self.assertEqual(row["calendar_id"], "cal-new")
        self.assertEqual(self.fake.summaries(), [("2026-11-02", "Work 7.5h"), ("2026-11-03", "Work 6.5h")])
        html = c.get("/settings").get_data(as_text=True)
        self.assertIn("Connected", html)
        self.assertNotIn("ref-1", html)

    def test_save_syncs_and_google_error_never_fails_save(self):
        c = self.make_app()
        self.connect(c)
        r = self.save_days(c, "2026-11", [day("2026-11-04")])
        self.assertEqual(r.get_json()["gcal"], "ok")
        self.assertEqual(self.fake.summaries(), [("2026-11-04", "Work 7.5h")])
        self.fake.fail_api = 500
        r = self.save_days(c, "2026-11", [day("2026-11-05")])
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(r.get_json()["gcal"], "error")
        self.assertIn("Last sync failed", c.get("/settings").get_data(as_text=True))

    def test_unexpected_transport_exception_never_fails_save(self):
        import http.client
        c = self.make_app()
        self.connect(c)
        def broken(*a):
            raise http.client.IncompleteRead(b"")
        c.application.config["GCAL_TRANSPORT"] = broken
        gcal._token_cache.clear()
        r = self.save_days(c, "2026-11", [day("2026-11-05")])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["gcal"], "error")

    def test_non_dict_json_is_a_clean_error(self):
        c = self.make_app()
        self.connect(c)
        c.application.config["GCAL_TRANSPORT"] = lambda m, u, h, b: (200, b"null") if u == gcal.TOKEN_URL else (500, b"null")
        gcal._token_cache.clear()
        r = self.save_days(c, "2026-11", [day("2026-11-05")])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["gcal"], "error")

    def test_deleted_calendar_is_recreated(self):
        c = self.make_app()
        self.save_days(c, "2026-10", [day("2026-10-05")])
        self.connect(c)
        self.fake.calendars.clear()
        self.fake.events.clear()
        r = self.save_days(c, "2026-11", [day("2026-11-04")])
        self.assertEqual(r.get_json()["gcal"], "ok")
        self.assertEqual(self.fake.summaries(), [("2026-10-05", "Work 7.5h"), ("2026-11-04", "Work 7.5h")])

    def test_disconnect_revokes_and_forgets(self):
        c = self.make_app()
        self.connect(c)
        r = c.post("/google/disconnect", data={"csrf_token": self.csrf(c)})
        self.assertIn("google=disconnected", r.headers["Location"])
        self.assertTrue(any(call[1] == gcal.REVOKE_URL for call in self.fake.calls))
        self.assertIsNone(db.get_google(db.connect(self.db_path)))
        self.assertIsNone(self.save_days(c, "2026-11", [day("2026-11-04")]).get_json()["gcal"])


if __name__ == "__main__":
    unittest.main()
