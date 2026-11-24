"""Tests for Google Calendar configuration in SQLite database and redirect-URI validation."""
import os
import tempfile
import unittest

from src import config, db


class TestGoogleConfigDB(unittest.TestCase):
    def test_defaults_after_connect_and_file_persistence(self):
        conn = db.connect(":memory:")
        cfg = db.get_google_config(conn)
        self.assertEqual(cfg["id"], 1)
        self.assertEqual(cfg["client_id"], "")
        self.assertEqual(cfg["client_secret"], "")
        self.assertEqual(cfg["redirect_uri"], "")
        self.assertEqual(cfg["weekday_start"], 450)
        self.assertEqual(cfg["weekday_end"], 930)
        self.assertEqual(cfg["weekend_start"], 510)
        self.assertEqual(cfg["weekend_end"], 990)
        self.assertEqual(cfg["work_color"], "6")
        self.assertEqual(cfg["holiday_color"], "2")

        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "test.db")
            conn1 = db.connect(fpath)
            db.update_google_config(
                conn1,
                weekday_start=480,
                weekday_end=960,
                weekend_start=540,
                weekend_end=1020,
                work_color="7",
                holiday_color="3",
                client_id="cid",
                client_secret="sec",
                redirect_uri="https://app.test/oauth2callback",
            )
            conn1.close()

            conn2 = db.connect(fpath)
            cfg2 = db.get_google_config(conn2)
            self.assertEqual(cfg2["weekday_start"], 480)
            self.assertEqual(cfg2["weekday_end"], 960)
            self.assertEqual(cfg2["weekend_start"], 540)
            self.assertEqual(cfg2["weekend_end"], 1020)
            self.assertEqual(cfg2["work_color"], "7")
            self.assertEqual(cfg2["holiday_color"], "3")
            self.assertEqual(cfg2["client_id"], "cid")
            self.assertEqual(cfg2["client_secret"], "sec")
            self.assertEqual(cfg2["redirect_uri"], "https://app.test/oauth2callback")
            conn2.close()

    def test_update_hours_and_colors(self):
        conn = db.connect(":memory:")

        # update hours/colors valid
        cfg = db.update_google_config(
            conn,
            weekday_start=400,
            weekday_end=900,
            weekend_start=500,
            weekend_end=1000,
            work_color="11",
            holiday_color="1",
        )
        self.assertEqual(cfg["weekday_start"], 400)
        self.assertEqual(cfg["weekday_end"], 900)
        self.assertEqual(cfg["weekend_start"], 500)
        self.assertEqual(cfg["weekend_end"], 1000)
        self.assertEqual(cfg["work_color"], "11")
        self.assertEqual(cfg["holiday_color"], "1")

        # start >= end rejected for weekday
        with self.assertRaises(ValueError) as ctx:
            db.update_google_config(conn, weekday_start=900, weekday_end=900)
        self.assertEqual(str(ctx.exception), "Weekday work hours: 'from' must be before 'to'")
        with self.assertRaises(ValueError):
            db.update_google_config(conn, weekday_start=901, weekday_end=900)

        # start >= end rejected for weekend
        with self.assertRaises(ValueError) as ctx:
            db.update_google_config(conn, weekend_start=1000, weekend_end=1000)
        self.assertEqual(str(ctx.exception), "Weekend work hours: 'from' must be before 'to'")
        with self.assertRaises(ValueError):
            db.update_google_config(conn, weekend_start=1001, weekend_end=1000)

        # out of range (-1, 1441) and bool rejected
        for field in ("weekday_start", "weekday_end", "weekend_start", "weekend_end"):
            with self.assertRaises(ValueError):
                db.update_google_config(conn, **{field: -1})
            with self.assertRaises(ValueError):
                db.update_google_config(conn, **{field: 1441})
            with self.assertRaises(ValueError):
                db.update_google_config(conn, **{field: True})
            with self.assertRaises(ValueError):
                db.update_google_config(conn, **{field: False})

        # color '12', 'red', 6 (int) rejected; '' accepted
        with self.assertRaises(ValueError):
            db.update_google_config(conn, work_color="12")
        with self.assertRaises(ValueError):
            db.update_google_config(conn, holiday_color="12")
        with self.assertRaises(ValueError):
            db.update_google_config(conn, work_color="red")
        with self.assertRaises(ValueError):
            db.update_google_config(conn, holiday_color="red")
        with self.assertRaises(ValueError):
            db.update_google_config(conn, work_color=6)
        with self.assertRaises(ValueError):
            db.update_google_config(conn, holiday_color=6)

        cfg = db.update_google_config(conn, work_color="", holiday_color="")
        self.assertEqual(cfg["work_color"], "")
        self.assertEqual(cfg["holiday_color"], "")

    def test_credentials_validation(self):
        conn = db.connect(":memory:")

        # all three set ok and stripped
        cfg = db.update_google_config(
            conn,
            client_id="  cid-123  ",
            client_secret="  secret-456  ",
            redirect_uri="  https://example.com/oauth  ",
        )
        self.assertEqual(cfg["client_id"], "cid-123")
        self.assertEqual(cfg["client_secret"], "secret-456")
        self.assertEqual(cfg["redirect_uri"], "https://example.com/oauth")

        # only some set -> ValueError
        c_fresh = db.connect(":memory:")
        with self.assertRaises(ValueError) as ctx:
            db.update_google_config(c_fresh, client_id="cid")
        self.assertEqual(
            str(ctx.exception),
            "Fill in client id, client secret and redirect URI, or leave all three empty",
        )
        with self.assertRaises(ValueError):
            db.update_google_config(c_fresh, client_secret="sec")
        with self.assertRaises(ValueError):
            db.update_google_config(c_fresh, redirect_uri="https://example.com/oauth")
        with self.assertRaises(ValueError):
            db.update_google_config(c_fresh, client_id="cid", client_secret="sec")
        with self.assertRaises(ValueError):
            db.update_google_config(conn, client_id="")

        # clearing all three ok
        cfg = db.update_google_config(conn, client_id="", client_secret="", redirect_uri="")
        self.assertEqual(cfg["client_id"], "")
        self.assertEqual(cfg["client_secret"], "")
        self.assertEqual(cfg["redirect_uri"], "")

        # clearing with whitespace-only ok
        db.update_google_config(
            conn, client_id="cid", client_secret="sec", redirect_uri="https://example.com/oauth"
        )
        cfg = db.update_google_config(conn, client_id="   ", client_secret="  ", redirect_uri="  ")
        self.assertEqual(cfg["client_id"], "")
        self.assertEqual(cfg["client_secret"], "")
        self.assertEqual(cfg["redirect_uri"], "")

        # http://example.com rejected
        with self.assertRaises(ValueError) as ctx:
            db.update_google_config(
                conn, client_id="c", client_secret="s", redirect_uri="http://example.com"
            )
        self.assertEqual(str(ctx.exception), "Redirect URI must be an https:// address")

        # http://localhost:8080/google/callback accepted
        cfg = db.update_google_config(
            conn,
            client_id="c",
            client_secret="s",
            redirect_uri="http://localhost:8080/google/callback",
        )
        self.assertEqual(cfg["redirect_uri"], "http://localhost:8080/google/callback")

        # over 500 chars rejected
        with self.assertRaises(ValueError):
            db.update_google_config(
                conn,
                client_id="x" * 501,
                client_secret="s",
                redirect_uri="http://localhost:8080/google/callback",
            )
        with self.assertRaises(ValueError):
            db.update_google_config(
                conn,
                client_id="c",
                client_secret="x" * 501,
                redirect_uri="http://localhost:8080/google/callback",
            )
        with self.assertRaises(ValueError):
            db.update_google_config(
                conn,
                client_id="c",
                client_secret="s",
                redirect_uri="https://example.com/" + ("x" * 500),
            )

        # unknown field rejected
        with self.assertRaises(ValueError) as ctx:
            db.update_google_config(conn, invalid_field="foo")
        self.assertEqual(str(ctx.exception), "Unknown google config field: invalid_field")
        with self.assertRaises(ValueError) as ctx:
            db.update_google_config(conn, id=1)
        self.assertEqual(str(ctx.exception), "Unknown google config field: id")

    def test_settings_isolation(self):
        conn = db.connect(":memory:")
        settings = db.get_settings(conn)
        self.assertNotIn("client_id", settings)
        self.assertNotIn("client_secret", settings)
        self.assertNotIn("redirect_uri", settings)
        self.assertNotIn("weekday_start", settings)
        self.assertNotIn("weekday_end", settings)
        self.assertNotIn("weekend_start", settings)
        self.assertNotIn("weekend_end", settings)
        self.assertNotIn("work_color", settings)
        self.assertNotIn("holiday_color", settings)

        db.update_google_config(conn, work_color="9", weekday_start=460)
        db.update_settings(conn, rate_cents=2500)

        s_after = db.get_settings(conn)
        self.assertEqual(s_after["rate_cents"], 2500)
        self.assertNotIn("work_color", s_after)
        self.assertNotIn("weekday_start", s_after)

        g_after = db.get_google_config(conn)
        self.assertEqual(g_after["work_color"], "9")
        self.assertEqual(g_after["weekday_start"], 460)

    def test_validate_redirect_uri(self):
        # https ok
        config.validate_redirect_uri("https://example.com/oauth/callback")
        config.validate_redirect_uri("https://secure.site.org:8443/cb")

        # http non-local raises ConfigError
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate_redirect_uri("http://example.com/cb")
        self.assertEqual(str(ctx.exception), "GOOGLE_REDIRECT_URI must be an https:// address")
        with self.assertRaises(config.ConfigError):
            config.validate_redirect_uri("http://localhost.attacker.com/cb")

        # empty-host raises
        with self.assertRaises(config.ConfigError):
            config.validate_redirect_uri("https://")
        with self.assertRaises(config.ConfigError):
            config.validate_redirect_uri("https:///callback")


if __name__ == "__main__":
    unittest.main()
