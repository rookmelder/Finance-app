"""Shift storage and calendar regression checks against the roster specification."""

import unittest
from unittest.mock import patch

from src import db, gcal


class ShiftDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)

    def save(self, *entries):
        return db.save_month_days(self.conn, "2026-11", list(entries))

    def days(self):
        return db.list_days(self.conn, "2026-11")

    def reconnect(self):
        # Re-run initialization on the same in-memory database, without a file.
        with patch.object(db.sqlite3, "connect", return_value=self.conn):
            self.assertIs(db.connect(":memory:"), self.conn)

    def assert_plain(self, day):
        self.assertIsNone(day["code"])
        self.assertIsNone(day["start"])
        self.assertIsNone(day["end"])

    def test_seed_rows_exact(self):
        self.assertEqual(db.list_shifts(self.conn), [
            {"code": "V", "start": 450, "end": 930, "minutes": 450},
            {"code": "D", "start": 510, "end": 990, "minutes": 450},
            {"code": "L", "start": 810, "end": 1290, "minutes": 450},
        ])

    def test_connect_is_idempotent_and_seeds_only_missing_rows(self):
        db.save_shift(self.conn, "V", 400, 900, 470)
        db.save_shift(self.conn, "X", 100, 200, 90)
        db.delete_shift(self.conn, "D")
        self.save({"date": "2026-11-02", "code": "V"})
        before_days = self.days()
        self.reconnect()
        expected = [
            {"code": "X", "start": 100, "end": 200, "minutes": 90},
            {"code": "V", "start": 400, "end": 900, "minutes": 470},
            {"code": "D", "start": 510, "end": 990, "minutes": 450},
            {"code": "L", "start": 810, "end": 1290, "minutes": 450},
        ]
        self.assertEqual(db.list_shifts(self.conn), expected)
        self.reconnect()
        self.assertEqual(db.list_shifts(self.conn), expected)
        self.assertEqual(self.days(), before_days)

    def test_old_work_days_migrate_with_nullable_shift_columns(self):
        with self.conn:
            self.conn.execute("DROP TABLE work_days")
            self.conn.execute(
                "CREATE TABLE work_days (date TEXT PRIMARY KEY, "
                "minutes INTEGER NOT NULL, holiday INTEGER NOT NULL DEFAULT 0)"
            )
            self.conn.execute(
                "INSERT INTO work_days VALUES (?, ?, ?)", ("2026-11-02", 450, 0)
            )
        self.reconnect()
        self.assertEqual(self.days(), [{
            "date": "2026-11-02", "minutes": 450, "holiday": False,
            "code": None, "start": None, "end": None,
        }])
        columns = {r[1]: r for r in self.conn.execute("PRAGMA table_info(work_days)")}
        for name, kind in (("code", "TEXT"), ("start_min", "INTEGER"),
                           ("end_min", "INTEGER")):
            with self.subTest(column=name):
                self.assertEqual(columns[name][2], kind)
                self.assertEqual(columns[name][3], 0)
        self.reconnect()
        self.assert_plain(self.days()[0])

    def test_old_google_config_migrates_roster_defaults(self):
        with self.conn:
            self.conn.execute("DROP TABLE google_config")
            self.conn.execute("""
                CREATE TABLE google_config (
                    id INTEGER PRIMARY KEY,
                    client_id TEXT NOT NULL DEFAULT '',
                    client_secret TEXT NOT NULL DEFAULT '',
                    redirect_uri TEXT NOT NULL DEFAULT '',
                    weekday_start INTEGER NOT NULL DEFAULT 450,
                    weekday_end INTEGER NOT NULL DEFAULT 930,
                    weekend_start INTEGER NOT NULL DEFAULT 510,
                    weekend_end INTEGER NOT NULL DEFAULT 990,
                    work_color TEXT NOT NULL DEFAULT '6',
                    holiday_color TEXT NOT NULL DEFAULT '2'
                )
            """)
            self.conn.execute("INSERT INTO google_config (id, weekday_start) VALUES (1, 420)")
        self.reconnect()
        cfg = db.get_google_config(self.conn)
        self.assertEqual(cfg["roster_sender"], "")
        self.assertEqual(cfg["roster_name"], "")
        self.assertEqual(cfg["weekday_start"], 420)
        db.update_google_config(self.conn, roster_sender="boss@example.test", roster_name="Alex")
        self.reconnect()
        self.assertEqual(db.get_google_config(self.conn)["roster_name"], "Alex")

    def test_lowercase_code_uses_seed_times_and_paid_minutes(self):
        self.save({"date": "2026-11-02", "code": "v"})
        self.assertEqual(self.days(), [{
            "date": "2026-11-02", "minutes": 450, "holiday": False,
            "code": "V", "start": 450, "end": 930,
        }])

    def test_unknown_code_with_explicit_times_is_allowed(self):
        self.save({"date": "2026-11-02", "code": "x9", "start": 600,
                   "end": 1080, "minutes": 450})
        self.assertEqual(self.days(), [{
            "date": "2026-11-02", "minutes": 450, "holiday": False,
            "code": "X9", "start": 600, "end": 1080,
        }])

    def test_explicit_times_override_template_but_keep_its_paid_minutes(self):
        self.save({"date": "2026-11-02", "code": "V", "start": 600, "end": 1110})
        self.assertEqual(self.days()[0], {
            "date": "2026-11-02", "minutes": 450, "holiday": False,
            "code": "V", "start": 600, "end": 1110,
        })

    def test_explicit_minutes_override_with_or_without_explicit_times(self):
        for times in ({}, {"start": 600, "end": 1080}):
            for minutes in (1, 420, 1440):
                with self.subTest(times=times, minutes=minutes):
                    self.save({"date": "2026-11-02", "code": "V",
                               "minutes": minutes, **times})
                    self.assertEqual(self.days()[0]["minutes"], minutes)

    def test_unknown_code_without_times_raises_and_saves_nothing(self):
        with self.assertRaises(ValueError):
            self.save({"date": "2026-11-02", "code": "V"},
                      {"date": "2026-11-03", "code": "UNKNOWN", "minutes": 450})
        self.assertEqual(self.days(), [])
        self.assertEqual(db.list_income(self.conn), [])

    def test_invalid_replacement_leaves_existing_days_unchanged(self):
        self.save({"date": "2026-11-02", "code": "V"})
        before = self.days()
        income = db.list_income(self.conn)
        with self.assertRaises(ValueError):
            self.save({"date": "2026-11-02", "code": "D"},
                      {"date": "2026-11-03", "code": "UNKNOWN"})
        self.assertEqual(self.days(), before)
        self.assertEqual(db.list_income(self.conn), income)

    def test_invalid_day_times_raise_and_save_nothing(self):
        cases = [
            {"start": 600, "end": 600}, {"start": 601, "end": 600},
            {"start": -1, "end": 600}, {"start": 600, "end": 1441},
            {"start": 1440, "end": 1440}, {"start": 600}, {"end": 900},
            {"start": True, "end": 900}, {"start": 600, "end": False},
            {"start": "600", "end": 900}, {"start": 600, "end": 900.5},
        ]
        for times in cases:
            with self.subTest(times=times):
                with self.assertRaises(ValueError):
                    self.save({"date": "2026-11-02", "code": "V", **times})
                self.assertEqual(self.days(), [])

    def test_day_time_boundaries_are_inclusive_at_zero_and_1440(self):
        for start, end in ((0, 1), (1439, 1440), (0, 1440)):
            with self.subTest(start=start, end=end):
                self.save({"date": "2026-11-02", "code": "V",
                           "start": start, "end": end, "minutes": 1})
                self.assertEqual((self.days()[0]["start"], self.days()[0]["end"]),
                                 (start, end))

    def test_explicit_day_minutes_must_be_valid_integers(self):
        # An explicitly supplied value must not silently fall back to the template.
        for minutes in (0, -1, 1441, True, False, "450", 450.5):
            with self.subTest(minutes=minutes):
                with self.assertRaises(ValueError):
                    self.save({"date": "2026-11-02", "code": "V", "minutes": minutes})
                self.assertEqual(self.days(), [])

    def test_holiday_drops_code_and_times_including_sql_storage(self):
        for shift in ({"code": "V"},
                      {"code": "X", "start": 600, "end": 1080, "minutes": 450}):
            with self.subTest(shift=shift):
                self.save({"date": "2026-11-02", "holiday": True, **shift})
                day = self.days()[0]
                self.assertIs(day["holiday"], True)
                self.assertEqual(day["minutes"], 450)
                self.assert_plain(day)
                row = self.conn.execute("SELECT code, start_min, end_min FROM work_days").fetchone()
                self.assertEqual(tuple(row), (None, None, None))

    def test_absent_code_keeps_stored_shift_when_minutes_equal(self):
        self.save({"date": "2026-11-02", "code": "V", "start": 600, "end": 1080})
        before = self.days()
        db.save_shift(self.conn, "V", 300, 700, 370)
        self.save({"date": "2026-11-02", "minutes": 450})
        self.assertEqual(self.days(), before)

    def test_absent_code_drops_shift_when_minutes_change(self):
        self.save({"date": "2026-11-02", "code": "V"})
        self.save({"date": "2026-11-02", "minutes": 449, "holiday": False})
        self.assert_plain(self.days()[0])
        self.assertEqual(self.days()[0]["minutes"], 449)

    def test_absent_code_drops_shift_when_day_becomes_holiday(self):
        self.save({"date": "2026-11-02", "code": "V"})
        self.save({"date": "2026-11-02", "minutes": 450, "holiday": True})
        self.assert_plain(self.days()[0])
        self.assertIs(self.days()[0]["holiday"], True)

    def test_null_code_clears_stored_shift_even_with_same_minutes(self):
        self.save({"date": "2026-11-02", "code": "V"})
        self.save({"date": "2026-11-02", "minutes": 450, "code": None})
        self.assert_plain(self.days()[0])
        row = self.conn.execute("SELECT code, start_min, end_min FROM work_days").fetchone()
        self.assertEqual(tuple(row), (None, None, None))

    def test_list_days_returns_exact_shift_and_plain_day_fields(self):
        self.save({"date": "2026-11-03", "code": "D"},
                  {"date": "2026-11-02", "minutes": 450})
        self.assertEqual(self.days(), [
            {"date": "2026-11-02", "minutes": 450, "holiday": False,
             "code": None, "start": None, "end": None},
            {"date": "2026-11-03", "minutes": 450, "holiday": False,
             "code": "D", "start": 510, "end": 990},
        ])

    def test_empty_month_returns_no_days(self):
        self.save()
        self.assertEqual(self.days(), [])

    def test_list_shifts_orders_by_start_then_code(self):
        db.save_shift(self.conn, "Z9", 450, 700, 200)
        db.save_shift(self.conn, "A1", 450, 800, 300)
        db.save_shift(self.conn, "EARLY", 0, 60, 60)
        self.assertEqual([s["code"] for s in db.list_shifts(self.conn)],
                         ["EARLY", "A1", "V", "Z9", "D", "L"])

    def test_save_shift_upserts_without_duplicate_codes(self):
        db.save_shift(self.conn, "X1", 600, 1000, 370)
        db.save_shift(self.conn, "X1", 610, 1100, 460)
        self.assertEqual([s for s in db.list_shifts(self.conn) if s["code"] == "X1"],
                         [{"code": "X1", "start": 610, "end": 1100, "minutes": 460}])
        self.assertEqual(len(db.list_shifts(self.conn)), 4)

    def test_save_shift_accepts_code_and_numeric_boundaries(self):
        for code, start, end, minutes in (
            ("0", 0, 1, 1), ("AZ09AZ09", 1439, 1440, 1440), ("ALL", 0, 1440, 1440)
        ):
            with self.subTest(code=code):
                db.save_shift(self.conn, code, start, end, minutes)
                self.assertIn({"code": code, "start": start, "end": end, "minutes": minutes},
                              db.list_shifts(self.conn))

    def test_save_shift_rejects_invalid_codes_without_changes(self):
        before = db.list_shifts(self.conn)
        for code in ("", " ", "ABCDEFGHI", "A-B", "A B", "A_1", "é", "中", None, 7):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    db.save_shift(self.conn, code, 450, 930, 450)
                self.assertEqual(db.list_shifts(self.conn), before)

    def test_save_shift_rejects_invalid_times_without_changes(self):
        before = db.list_shifts(self.conn)
        for start, end in ((-1, 930), (450, 1441), (450, 450), (451, 450),
                           (True, 930), (450, False), ("450", 930), (450, 930.5),
                           (None, 930), (450, None)):
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError):
                    db.save_shift(self.conn, "V", start, end, 450)
                self.assertEqual(db.list_shifts(self.conn), before)

    def test_save_shift_rejects_invalid_minutes_without_changes(self):
        before = db.list_shifts(self.conn)
        for minutes in (0, -1, 1441, True, False, "450", 450.5, None):
            with self.subTest(minutes=minutes):
                with self.assertRaises(ValueError):
                    db.save_shift(self.conn, "V", 450, 930, minutes)
                self.assertEqual(db.list_shifts(self.conn), before)

    def test_delete_shift_keeps_days_stored_codes_and_times(self):
        self.save({"date": "2026-11-02", "code": "V"})
        before = self.days()
        db.delete_shift(self.conn, "V")
        self.assertNotIn("V", [s["code"] for s in db.list_shifts(self.conn)])
        self.assertEqual(self.days(), before)
        self.save({"date": "2026-11-02", "minutes": 450})
        self.assertEqual(self.days(), before)

    def test_google_roster_config_defaults_are_empty_text(self):
        cfg = db.get_google_config(self.conn)
        self.assertEqual(cfg["roster_sender"], "")
        self.assertEqual(cfg["roster_name"], "")
        columns = {r[1]: r[2] for r in self.conn.execute("PRAGMA table_info(google_config)")}
        self.assertEqual(columns["roster_sender"], "TEXT")
        self.assertEqual(columns["roster_name"], "TEXT")

    def test_google_roster_config_updates_persist_independently_and_clear(self):
        db.update_google_config(self.conn, roster_sender="boss@example.test", roster_name="Alex Smith")
        self.reconnect()
        cfg = db.get_google_config(self.conn)
        self.assertEqual(cfg["roster_sender"], "boss@example.test")
        self.assertEqual(cfg["roster_name"], "Alex Smith")
        db.update_google_config(self.conn, roster_name="Sam Jones")
        cfg = db.get_google_config(self.conn)
        self.assertEqual(cfg["roster_sender"], "boss@example.test")
        self.assertEqual(cfg["roster_name"], "Sam Jones")
        db.update_google_config(self.conn, roster_sender="", roster_name="")
        cfg = db.get_google_config(self.conn)
        self.assertEqual((cfg["roster_sender"], cfg["roster_name"]), ("", ""))


class ShiftCalendarTests(unittest.TestCase):
    def setUp(self):
        self.look = {
            "weekday_start": 360, "weekday_end": 840,
            "weekend_start": 540, "weekend_end": 1020,
            "work_color": "6", "holiday_color": "2",
        }

    def day(self, **changes):
        return {"date": "2026-11-02", "minutes": 450, "holiday": False,
                "code": "V", "start": 450, "end": 930, **changes}

    def assert_times(self, body, start, end):
        self.assertEqual(body["start"], {"dateTime": start, "timeZone": "Europe/Brussels"})
        self.assertEqual(body["end"], {"dateTime": end, "timeZone": "Europe/Brussels"})

    def test_shift_event_uses_title_and_days_own_times(self):
        body = gcal.event_body(self.day(start=615, end=1095), self.look)
        self.assertEqual(body["summary"], "Work V")
        self.assert_times(body, "2026-11-02T10:15:00", "2026-11-02T18:15:00")
        self.assertEqual(body["colorId"], "6")

    def test_weekend_shift_uses_days_times_instead_of_weekend_look(self):
        for date in ("2026-11-07", "2026-11-08"):
            with self.subTest(date=date):
                body = gcal.event_body(self.day(date=date, start=810, end=1290), self.look)
                self.assertEqual(body["summary"], "Work V")
                self.assert_times(body, date + "T13:30:00", date + "T21:30:00")

    def test_shift_event_handles_midnight_boundaries_and_month_rollover(self):
        body = gcal.event_body(self.day(date="2026-11-30", start=0, end=1440), self.look)
        self.assert_times(body, "2026-11-30T00:00:00", "2026-12-01T00:00:00")

    def test_holiday_remains_all_day_even_with_shift_fields(self):
        holiday = self.day(date="2026-11-30", holiday=True)
        body = gcal.event_body(holiday, self.look)
        plain = {"date": "2026-11-30", "minutes": 450, "holiday": True}
        self.assertEqual(body, gcal.event_body(plain, self.look))
        self.assertEqual(body["summary"], "Holiday 7.5h")
        self.assertEqual(body["start"], {"date": "2026-11-30"})
        self.assertEqual(body["end"], {"date": "2026-12-01"})
        self.assertEqual(body["colorId"], "2")

    def test_plain_day_retains_hours_title_and_weekday_or_weekend_look(self):
        for date, start, end in (("2026-11-02", "06:00:00", "14:00:00"),
                                 ("2026-11-07", "09:00:00", "17:00:00"),
                                 ("2026-11-08", "09:00:00", "17:00:00")):
            for fields in ({}, {"code": None, "start": None, "end": None}):
                with self.subTest(date=date, fields=fields):
                    body = gcal.event_body({"date": date, "minutes": 450,
                                            "holiday": False, **fields}, self.look)
                    self.assertEqual(body["summary"], "Work 7.5h")
                    self.assert_times(body, date + "T" + start, date + "T" + end)

    def test_default_look_keeps_plain_day_behavior(self):
        body = gcal.event_body({"date": "2026-11-02", "minutes": 450, "holiday": False})
        self.assertEqual(body["summary"], "Work 7.5h")
        self.assert_times(body, "2026-11-02T07:30:00", "2026-11-02T15:30:00")

    def test_signature_changes_when_only_shift_code_changes(self):
        first = gcal.event_body(self.day(), self.look)
        second = gcal.event_body(self.day(code="D"), self.look)
        self.assertNotEqual(first["extendedProperties"]["private"][gcal.TAG_SIG],
                            second["extendedProperties"]["private"][gcal.TAG_SIG])

    def test_signature_changes_when_only_shift_times_change(self):
        first = gcal.event_body(self.day(), self.look)
        second = gcal.event_body(self.day(start=451, end=931), self.look)
        self.assertNotEqual(first["extendedProperties"]["private"][gcal.TAG_SIG],
                            second["extendedProperties"]["private"][gcal.TAG_SIG])

    def test_plan_updates_existing_event_when_only_code_changes(self):
        old = {"id": "existing-event", **gcal.event_body(self.day(), self.look)}
        changed = self.day(code="D")
        inserts, updates, deletes = gcal.plan_month("2026-11", [changed], [old], self.look)
        self.assertEqual(inserts, [])
        self.assertEqual(deletes, [])
        self.assertEqual(len(updates), 1)
        event_id, body = updates[0]
        self.assertEqual(event_id, "existing-event")
        self.assertEqual(body["summary"], "Work D")
        self.assert_times(body, "2026-11-02T07:30:00", "2026-11-02T15:30:00")

    def test_plan_unchanged_shift_has_no_operations(self):
        day = self.day()
        event = {"id": "existing-event", **gcal.event_body(day, self.look)}
        self.assertEqual(gcal.plan_month("2026-11", [day], [event], self.look), ([], [], []))

    def test_plan_empty_inputs_has_no_operations(self):
        self.assertEqual(gcal.plan_month("2026-11", [], [], self.look), ([], [], []))


if __name__ == "__main__":
    unittest.main()
