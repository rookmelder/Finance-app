import json
import unittest
import urllib.parse

from src import db, gcal


def fake(status, payload):
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        return status, json.dumps(payload).encode()

    transport.calls = calls
    return transport


class ScopeTests(unittest.TestCase):
    def test_auth_url_asks_for_calendar_and_gmail(self):
        url = gcal.auth_url("cid", "http://localhost/cb", "st")
        scope = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["scope"][0].split()
        self.assertEqual(scope, [gcal.SCOPE, gcal.SCOPE_GMAIL])

    def test_exchange_code_scopes_returns_granted(self):
        t = fake(200, {"refresh_token": "r", "access_token": "a", "expires_in": 3600,
                       "scope": gcal.SCOPE + " " + gcal.SCOPE_GMAIL})
        self.assertEqual(gcal.exchange_code_scopes("c", "s", "u", "code", t), ("r", [gcal.SCOPE, gcal.SCOPE_GMAIL]))
        t = fake(200, {"refresh_token": "r2", "scope": gcal.SCOPE})
        self.assertEqual(gcal.exchange_code_scopes("c", "s", "u", "code", t), ("r2", [gcal.SCOPE]))
        t = fake(200, {"refresh_token": "r3"})
        self.assertEqual(gcal.exchange_code_scopes("c", "s", "u", "code", t), ("r3", []))

    def test_exchange_code_still_returns_the_token_and_needs_calendar_scope(self):
        t = fake(200, {"refresh_token": "r", "scope": gcal.SCOPE_GMAIL})
        with self.assertRaises(gcal.GcalError):
            gcal.exchange_code("c", "s", "u", "code", t)
        t = fake(200, {"refresh_token": "r4", "scope": gcal.SCOPE})
        self.assertEqual(gcal.exchange_code("c", "s", "u", "code", t), "r4")

    def test_access_token_is_public(self):
        t = fake(200, {"access_token": "tok", "expires_in": 3600})
        client = gcal.Client("c", "s", "refresh-for-scope-test", t)
        self.assertEqual(client.access_token(), "tok")
        self.assertEqual(client.access_token(), "tok")
        self.assertEqual(len(t.calls), 1)
        self.assertEqual(client.access_token(force=True), "tok")
        self.assertEqual(len(t.calls), 2)

    def test_connect_google_stores_scopes(self):
        conn = db.connect(":memory:")
        db.connect_google(conn, "r1", "a b")
        self.assertEqual(db.get_google(conn)["scopes"], "a b")
        db.connect_google(conn, "r2")
        row = db.get_google(conn)
        self.assertEqual((row["refresh_token"], row["scopes"]), ("r2", None))


if __name__ == "__main__":
    unittest.main()
