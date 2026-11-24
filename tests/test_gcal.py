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

    def summaries(self):
        return sorted(((e["start"].get("date") or e["start"]["dateTime"][:10]), e["summary"]) for e in self.events.values())


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
        self.assertEqual(q["scope"], ["https://www.googleapis.com/auth/calendar.app.created "
                                      "https://www.googleapis.com/auth/gmail.readonly"])
        self.assertEqual(q["redirect_uri"], ["https://x/cb"])

    def test_event_body_weekday_work_day(self):
        b = gcal.event_body(day("2026-11-24"))
        self.assertEqual(b["summary"], "Work 7.5h")
        self.assertEqual(b["start"], {"dateTime": "2026-11-24T07:30:00", "timeZone": "Europe/Brussels"})
        self.assertEqual(b["end"], {"dateTime": "2026-11-24T15:30:00", "timeZone": "Europe/Brussels"})
        self.assertEqual(b["colorId"], "6")
        self.assertEqual(b["transparency"], "opaque")
        priv = b["extendedProperties"]["private"]
        self.assertEqual(priv["financeApp"], "1")
        self.assertEqual(priv["financeDate"], "2026-11-24")
        self.assertEqual(len(priv["financeSig"]), 16)

    def test_event_body_weekend_work_day(self):
        sat = gcal.event_body(day("2026-11-28"))
        self.assertEqual(sat["start"], {"dateTime": "2026-11-28T08:30:00", "timeZone": "Europe/Brussels"})
        self.assertEqual(sat["end"], {"dateTime": "2026-11-28T16:30:00", "timeZone": "Europe/Brussels"})
        self.assertEqual(sat["colorId"], "6")

        sun = gcal.event_body(day("2026-11-29"))
        self.assertEqual(sun["start"], {"dateTime": "2026-11-29T08:30:00", "timeZone": "Europe/Brussels"})
        self.assertEqual(sun["end"], {"dateTime": "2026-11-29T16:30:00", "timeZone": "Europe/Brussels"})
        self.assertEqual(sun["colorId"], "6")

    def test_event_body_holiday(self):
        b = gcal.event_body(day("2026-12-31", 390, holiday=True))
        self.assertEqual(b["summary"], "Holiday 6.5h")
        self.assertEqual(b["start"], {"date": "2026-12-31"})
        self.assertEqual(b["end"], {"date": "2027-01-01"})
        self.assertEqual(b["colorId"], "2")
        self.assertEqual(b["transparency"], "opaque")
        self.assertEqual(b["extendedProperties"]["private"]["financeDate"], "2026-12-31")

        b_no_color = gcal.event_body(day("2026-12-31", 390, holiday=True), look=dict(gcal.DEFAULT_LOOK, holiday_color=""))
        self.assertNotIn("colorId", b_no_color)

    def test_event_body_custom_look_and_end_minute_1440(self):
        custom = {
            "weekday_start": 480,
            "weekday_end": 1440,
            "weekend_start": 540,
            "weekend_end": 1020,
            "work_color": "4",
            "holiday_color": "5",
        }
        b = gcal.event_body(day("2026-11-24"), look=custom)
        self.assertEqual(b["start"], {"dateTime": "2026-11-24T08:00:00", "timeZone": "Europe/Brussels"})
        self.assertEqual(b["end"], {"dateTime": "2026-11-25T00:00:00", "timeZone": "Europe/Brussels"})
        self.assertEqual(b["colorId"], "4")

    def test_sig(self):
        b1 = gcal.event_body(day("2026-11-24"))
        b2 = gcal.event_body(day("2026-11-24"))
        sig1 = b1["extendedProperties"]["private"][gcal.TAG_SIG]
        sig2 = b2["extendedProperties"]["private"][gcal.TAG_SIG]
        self.assertEqual(sig1, sig2)
        self.assertEqual(b1["extendedProperties"]["private"][gcal.TAG_APP], "1")
        self.assertEqual(b1["extendedProperties"]["private"][gcal.TAG_DATE], "2026-11-24")
        self.assertTrue(b1["extendedProperties"]["private"][gcal.TAG_SIG])

        b_color = gcal.event_body(day("2026-11-24"), look=dict(gcal.DEFAULT_LOOK, work_color="9"))
        self.assertNotEqual(sig1, b_color["extendedProperties"]["private"][gcal.TAG_SIG])

        b_hours = gcal.event_body(day("2026-11-24"), look=dict(gcal.DEFAULT_LOOK, weekday_start=480))
        self.assertNotEqual(sig1, b_hours["extendedProperties"]["private"][gcal.TAG_SIG])

    def test_plan_month(self):
        old_ev = {
            "id": "e-old",
            "summary": "Work 7.5h",
            "start": {"date": "2026-11-24"},
            "end": {"date": "2026-11-25"},
            "transparency": "opaque",
            "extendedProperties": {"private": {gcal.TAG_APP: "1", gcal.TAG_DATE: "2026-11-24"}},
        }
        matching_body = gcal.event_body(day("2026-11-25"))
        matching_ev = dict(matching_body, id="e-match")
        dup_ev = dict(matching_body, id="e-dup")
        other_date_ev = dict(gcal.event_body(day("2026-11-26")), id="e-del")
        other_month_ev = dict(gcal.event_body(day("2026-10-31")), id="e-other")

        days = [day("2026-11-24"), day("2026-11-25"), day("2026-11-27")]
        events = [old_ev, matching_ev, dup_ev, other_date_ev, other_month_ev]

        inserts, updates, deletes = gcal.plan_month("2026-11", days, events)

        self.assertEqual([b["extendedProperties"]["private"][gcal.TAG_DATE] for b in inserts], ["2026-11-27"])
        self.assertEqual([(eid, b["extendedProperties"]["private"][gcal.TAG_DATE]) for eid, b in updates], [("e-old", "2026-11-24")])
        self.assertEqual(sorted(deletes), ["e-del", "e-dup"])

    def test_sync_month(self):
        fake = FakeGoogle()
        client = gcal.Client("cid", "sec", "ref-1", fake)
        days = [day("2026-11-24"), day("2026-11-28")]

        res1 = gcal.sync_month(client, "cal-1", "2026-11", days)
        self.assertEqual(res1, {"inserted": 2, "updated": 0, "deleted": 0})
        self.assertEqual(len(fake.events), 2)

        res2 = gcal.sync_month(client, "cal-1", "2026-11", days)
        self.assertEqual(res2, {"inserted": 0, "updated": 0, "deleted": 0})

        fake.calls.clear()
        diff_look = dict(gcal.DEFAULT_LOOK, work_color="1")
        res3 = gcal.sync_month(client, "cal-1", "2026-11", days, look=diff_look)
        self.assertEqual(res3, {"inserted": 0, "updated": 2, "deleted": 0})
        self.assertEqual(res3["inserted"], 0)
        put_calls = [c for c in fake.calls if c[0] == "PUT"]
        self.assertEqual(len(put_calls), 2)
        self.assertTrue(all(e["colorId"] == "1" for e in fake.events.values()))

        res4 = gcal.sync_month(client, "cal-1", "2026-11", [])
        self.assertEqual(res4, {"inserted": 0, "updated": 0, "deleted": 2})
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


class GoogleRoutesBase(unittest.TestCase):
    """Helpers shared by the route test classes (no tests here)."""

    def make_app(self, google=True):
        cfg = {"PASSWORD": "pw", "SECRET_KEY": "x" * 32, "DB_PATH": self.db_path, "HOST": "127.0.0.1", "PORT": 8080,
               "GCAL_TRANSPORT": self.fake}
        if google:
            cfg.update(GOOGLE_CLIENT_ID="cid", GOOGLE_CLIENT_SECRET="sec",
                       GOOGLE_REDIRECT_URI="https://fin.example/google/callback")
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

class TestGoogleRoutes(GoogleRoutesBase):
    def test_feature_hidden_without_config(self):
        c = self.make_app(google=False)
        html = c.get("/settings").get_data(as_text=True)
        self.assertIn("Not set up yet", html)
        self.assertIn('name="client_id"', html)
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
        ev = self.fake.events["e1"]
        self.assertIn("dateTime", ev["start"])
        self.assertEqual(ev["colorId"], "6")
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


class TestGoogleSettingsRoutes(GoogleRoutesBase):
    def test_save_credentials_in_app(self):
        c = self.make_app(google=False)
        self.save_days(c, "2026-11", [day("2026-11-02")])
        r = c.post("/google/credentials", data={
            "csrf_token": self.csrf(c),
            "client_id": "app-cid",
            "client_secret": "app-sec",
            "redirect_uri": "https://fin.example/google/callback",
        })
        self.assertEqual(r.status_code, 303)
        self.assertIn("google=creds_saved", r.headers["Location"])

        html = c.get("/settings").get_data(as_text=True)
        self.assertIn("Connect Google Calendar", html)

        # Connect redirect contains DB client_id and redirect_uri
        r_conn = c.post("/google/connect", data={"csrf_token": self.csrf(c)})
        self.assertEqual(r_conn.status_code, 303)
        loc = r_conn.headers["Location"]
        self.assertIn("client_id=app-cid", loc)
        self.assertIn("redirect_uri=https%3A%2F%2Ffin.example%2Fgoogle%2Fcallback", loc)

        # Full connect flow with FakeGoogle creates calendar and pushes days
        state = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["state"][0]
        r_cb = c.get("/google/callback", query_string={"state": state, "code": "good"})
        self.assertIn("google=connected", r_cb.headers["Location"])
        conn = db.connect(self.db_path)
        row = db.get_google(conn)
        self.assertEqual(row["calendar_id"], "cal-new")
        self.assertEqual(self.fake.summaries(), [("2026-11-02", "Work 7.5h")])

    def test_empty_secret_keeps_stored_and_never_leaks_in_html(self):
        c = self.make_app(google=False)
        c.post("/google/credentials", data={
            "csrf_token": self.csrf(c),
            "client_id": "cid-1",
            "client_secret": "super-secret-token",
            "redirect_uri": "https://fin.example/google/callback",
        })
        conn = db.connect(self.db_path)
        self.assertEqual(db.get_google_config(conn)["client_secret"], "super-secret-token")

        r = c.post("/google/credentials", data={
            "csrf_token": self.csrf(c),
            "client_id": "cid-1",
            "client_secret": "",
            "redirect_uri": "https://fin2.example/google/callback",
        })
        self.assertEqual(r.status_code, 303)
        self.assertEqual(db.get_google_config(conn)["client_secret"], "super-secret-token")
        self.assertEqual(db.get_google_config(conn)["redirect_uri"], "https://fin2.example/google/callback")

        html = c.get("/settings").get_data(as_text=True)
        self.assertNotIn("super-secret-token", html)

    def test_partial_credentials_and_http_redirect_error(self):
        c = self.make_app(google=False)
        r1 = c.post("/google/credentials", data={
            "csrf_token": self.csrf(c),
            "client_id": "cid-only",
            "client_secret": "",
            "redirect_uri": "",
        })
        self.assertEqual(r1.status_code, 400)
        html1 = r1.get_data(as_text=True)
        self.assertIn("Fill in client id, client secret and redirect URI", html1)

        r2 = c.post("/google/credentials", data={
            "csrf_token": self.csrf(c),
            "client_id": "cid",
            "client_secret": "sec",
            "redirect_uri": "http://example.com/google/callback",
        })
        self.assertEqual(r2.status_code, 400)
        html2 = r2.get_data(as_text=True)
        self.assertIn("Redirect URI must be an https:// address", html2)

    def test_credentials_while_connected_and_clear(self):
        c = self.make_app()
        self.connect(c)
        r = c.post("/google/credentials", data={
            "csrf_token": self.csrf(c),
            "client_id": "new-cid",
            "client_secret": "new-sec",
            "redirect_uri": "https://fin.example/google/callback",
        })
        self.assertEqual(r.status_code, 303)
        self.assertIn("google=disconnect_first", r.headers["Location"])
        conn = db.connect(self.db_path)
        self.assertEqual(db.get_google_config(conn)["client_id"], "")

        c.post("/google/disconnect", data={"csrf_token": self.csrf(c)})
        c.post("/google/credentials", data={
            "csrf_token": self.csrf(c),
            "client_id": "c1",
            "client_secret": "s1",
            "redirect_uri": "https://fin.example/google/callback",
        })
        self.assertEqual(db.get_google_config(conn)["client_id"], "c1")

        r_clear = c.post("/google/credentials", data={
            "csrf_token": self.csrf(c),
            "action": "clear",
        })
        self.assertEqual(r_clear.status_code, 303)
        self.assertIn("google=creds_cleared", r_clear.headers["Location"])
        cfg = db.get_google_config(conn)
        self.assertEqual(cfg["client_id"], "")
        self.assertEqual(cfg["client_secret"], "")
        self.assertEqual(cfg["redirect_uri"], "")

    def test_db_credentials_override_env(self):
        c = self.make_app(google=True)
        c.post("/google/credentials", data={
            "csrf_token": self.csrf(c),
            "client_id": "db-cid",
            "client_secret": "db-sec",
            "redirect_uri": "https://fin.example/db-cb",
        })
        r = c.post("/google/connect", data={"csrf_token": self.csrf(c)})
        self.assertEqual(r.status_code, 303)
        loc = r.headers["Location"]
        self.assertIn("client_id=db-cid", loc)
        self.assertIn("redirect_uri=https%3A%2F%2Ffin.example%2Fdb-cb", loc)

    def test_options_validation_and_sync_when_connected(self):
        c = self.make_app(google=False)
        r = c.post("/google/options", data={
            "csrf_token": self.csrf(c),
            "weekday_from": "07:30",
            "weekday_to": "15:30",
            "weekend_from": "08:30",
            "weekend_to": "16:30",
            "work_color": "4",
            "holiday_color": "5",
        })
        self.assertEqual(r.status_code, 303)
        self.assertIn("google=options_saved", r.headers["Location"])
        conn = db.connect(self.db_path)
        cfg = db.get_google_config(conn)
        self.assertEqual(cfg["weekday_start"], 450)
        self.assertEqual(cfg["weekday_end"], 930)
        self.assertEqual(cfg["weekend_start"], 510)
        self.assertEqual(cfg["weekend_end"], 990)
        self.assertEqual(cfg["work_color"], "4")
        self.assertEqual(cfg["holiday_color"], "5")

        r_bad1 = c.post("/google/options", data={
            "csrf_token": self.csrf(c),
            "weekday_from": "7:5",
            "weekday_to": "15:30",
            "weekend_from": "08:30",
            "weekend_to": "16:30",
            "work_color": "1",
            "holiday_color": "2",
        })
        self.assertEqual(r_bad1.status_code, 400)
        self.assertIn("Weekdays from: enter a time like 07:30", r_bad1.get_data(as_text=True))

        r_bad2 = c.post("/google/options", data={
            "csrf_token": self.csrf(c),
            "weekday_from": "25:00",
            "weekday_to": "15:30",
            "weekend_from": "08:30",
            "weekend_to": "16:30",
            "work_color": "1",
            "holiday_color": "2",
        })
        self.assertEqual(r_bad2.status_code, 400)
        self.assertIn("Weekdays from: enter a time like 07:30", r_bad2.get_data(as_text=True))

        r_bad3 = c.post("/google/options", data={
            "csrf_token": self.csrf(c),
            "weekday_from": "16:00",
            "weekday_to": "08:00",
            "weekend_from": "08:30",
            "weekend_to": "16:30",
            "work_color": "1",
            "holiday_color": "2",
        })
        self.assertEqual(r_bad3.status_code, 400)
        self.assertIn("Weekday work hours: 'from' must be before 'to'",
                      r_bad3.get_data(as_text=True).replace("&#39;", "'"))

        c2 = self.make_app(google=True)
        self.save_days(c2, "2026-11", [day("2026-11-02")])
        self.connect(c2)
        self.fake.calls.clear()

        r_sync = c2.post("/google/options", data={
            "csrf_token": self.csrf(c2),
            "weekday_from": "09:00",
            "weekday_to": "17:00",
            "weekend_from": "10:00",
            "weekend_to": "18:00",
            "work_color": "3",
            "holiday_color": "1",
        })
        self.assertEqual(r_sync.status_code, 303)
        self.assertIn("google=options_synced", r_sync.headers["Location"])
        put_calls = [call for call in self.fake.calls if call[0] == "PUT"]
        self.assertTrue(len(put_calls) >= 1)
        ev = self.fake.events["e1"]
        self.assertEqual(ev["colorId"], "3")
        self.assertEqual(ev["start"]["dateTime"], "2026-11-02T09:00:00")
        self.assertEqual(ev["end"]["dateTime"], "2026-11-02T17:00:00")

    def test_post_routes_need_csrf(self):
        c = self.make_app()
        self.assertEqual(c.post("/google/credentials").status_code, 400)
        self.assertEqual(c.post("/google/options").status_code, 400)



    def test_new_client_id_needs_new_secret(self):
        c = self.make_app(google=False)
        conn = db.connect(self.db_path)
        db.update_google_config(conn, client_id="old-id", client_secret="old-secret",
                                redirect_uri="https://fin.example/google/callback")
        r = c.post("/google/credentials", data={"csrf_token": self.csrf(c), "client_id": "new-id",
                                                "client_secret": "", "redirect_uri": "https://fin.example/google/callback"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Enter the client secret that belongs to the new client id.", r.get_data(as_text=True))
        self.assertEqual(db.get_google_config(conn)["client_id"], "old-id")
        r = c.post("/google/credentials", data={"csrf_token": self.csrf(c), "client_id": "new-id",
                                                "client_secret": "new-secret", "redirect_uri": "https://fin.example/google/callback"})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(db.get_google_config(conn)["client_secret"], "new-secret")
        conn.close()

    def test_options_reject_24_00(self):
        c = self.make_app(google=False)
        r = c.post("/google/options", data={"csrf_token": self.csrf(c), "weekday_from": "07:30", "weekday_to": "24:00",
                                            "weekend_from": "08:30", "weekend_to": "16:30", "work_color": "6",
                                            "holiday_color": "2"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Weekdays to: enter a time like 07:30", r.get_data(as_text=True))

    def test_suggested_redirect_is_http_only_on_localhost(self):
        c = self.make_app(google=False)
        html = c.get("/settings", base_url="http://localhost:8080").get_data(as_text=True)
        self.assertIn('value="http://localhost:8080/google/callback"', html)
        # a real domain gets https (build the view directly: test-client cookies are per host)
        from src.app import _google_view, get_db
        app = create_app({"PASSWORD": "pw", "SECRET_KEY": "x" * 32, "DB_PATH": self.db_path, "HOST": "127.0.0.1",
                          "PORT": 8080})
        with app.test_request_context("/settings", base_url="http://fin.example"):
            self.assertEqual(_google_view(get_db())["suggested_redirect"], "https://fin.example/google/callback")
        with app.test_request_context("/settings", base_url="http://127.0.0.1:8080"):
            self.assertEqual(_google_view(get_db())["suggested_redirect"], "http://127.0.0.1:8080/google/callback")


if __name__ == "__main__":
    unittest.main()
