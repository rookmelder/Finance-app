import unittest

from flask import Flask

from src import auth, config


def make_app():
    app = Flask(__name__, template_folder="../src/templates")
    app.config["SECRET_KEY"] = "x" * 32
    app.config["TESTING"] = True
    auth.install(app, "pw123")

    @app.route("/")
    def overview():
        return "home"

    @app.route("/thing", methods=["POST"])
    def thing():
        return "done"

    @app.route("/login-template-stub")
    def stub():
        return ""
    return app


def token(client):
    client.get("/login")
    with client.session_transaction() as s:
        return s["csrf"]


class AuthTests(unittest.TestCase):
    def setUp(self):
        auth._fails.clear()
        self.app = make_app()
        self.c = self.app.test_client()

    def login(self, pw="pw123"):
        return self.c.post("/login", data={"password": pw, "csrf_token": token(self.c)})

    def test_redirects_when_logged_out(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_wrong_password(self):
        r = self.login("nope")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get("/").status_code, 302)

    def test_login_ok_then_access(self):
        r = self.login()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.c.get("/").data, b"home")

    def test_post_without_csrf_rejected(self):
        self.login()
        self.assertEqual(self.c.post("/thing").status_code, 400)
        self.assertEqual(self.c.post("/thing", data={"csrf_token": "bad"}).status_code, 400)

    def test_post_with_csrf_ok(self):
        self.login()
        with self.c.session_transaction() as s:
            t = s["csrf"]
        self.assertEqual(self.c.post("/thing", data={"csrf_token": t}).data, b"done")

    def test_login_post_needs_csrf(self):
        self.c.get("/login")
        r = self.c.post("/login", data={"password": "pw123"})
        self.assertEqual(r.status_code, 400)

    def test_lockout_after_failures(self):
        for _ in range(auth.MAX_FAILS):
            self.login("bad")
        r = self.login("pw123")
        self.assertEqual(r.status_code, 200)  # still locked out
        self.assertEqual(self.c.get("/").status_code, 302)

    def test_logout_requires_post_and_clears(self):
        self.login()
        self.assertEqual(self.c.get("/logout").status_code, 405)
        with self.c.session_transaction() as s:
            t = s["csrf"]
        self.c.post("/logout", data={"csrf_token": t})
        self.assertEqual(self.c.get("/").status_code, 302)

    def test_cookie_flags_and_headers(self):
        r = self.c.get("/login")
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        self.assertIn("HttpOnly", r.headers.get("Set-Cookie", ""))
        self.assertIn("SameSite=Lax", r.headers.get("Set-Cookie", ""))


class ConfigTests(unittest.TestCase):
    def test_missing_password(self):
        with self.assertRaises(config.ConfigError):
            config.load({"SECRET_KEY": "x" * 32})

    def test_short_secret(self):
        with self.assertRaises(config.ConfigError):
            config.load({"FINANCE_PASSWORD": "a", "SECRET_KEY": "short"})

    def test_bad_port(self):
        with self.assertRaises(config.ConfigError):
            config.load({"FINANCE_PASSWORD": "a", "SECRET_KEY": "x" * 32, "FINANCE_PORT": "abc"})

    def test_ok(self):
        c = config.load({"FINANCE_PASSWORD": "a", "SECRET_KEY": "x" * 32})
        self.assertEqual(c["PORT"], 8080)


if __name__ == "__main__":
    unittest.main()
