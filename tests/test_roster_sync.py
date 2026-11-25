"""Tests for roster sync orchestration."""

import datetime
import json
import os
import unittest

from src import db, roster
from src.gmail import GmailError
from src.roster import NotARoster, RosterError
from src.roster_pdf import PdfError
from src.roster_sync import sync


class FakeGmail:
    """Fake Gmail client for unit tests."""

    def __init__(self, messages=None, attachments=None):
        self.messages = messages or {}  # id -> msg_dict
        self.attachments = attachments or {}  # (msg_id, att_id) -> bytes
        self.search_error = None
        self.get_message_error = None
        self.get_attachment_error = None
        self.recorded_queries = []
        self.get_message_calls = []
        self.get_attachment_calls = []

    def search(self, query, limit=30):
        self.recorded_queries.append((query, limit))
        if self.search_error:
            raise self.search_error
        return list(self.messages.keys())[:limit]

    def get_message(self, message_id):
        self.get_message_calls.append(message_id)
        if self.get_message_error:
            raise self.get_message_error
        return self.messages[message_id]

    def get_attachment(self, message_id, attachment_id):
        self.get_attachment_calls.append((message_id, attachment_id))
        if self.get_attachment_error:
            raise self.get_attachment_error
        return self.attachments.get((message_id, attachment_id), b"")


class TestRosterSync(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.update_google_config(
            self.conn,
            roster_sender="planner@example.com",
            roster_name="Test Person",
        )
        self.today = datetime.date(2026, 12, 1)

    def test_missing_sender_or_name(self):
        """Empty roster_sender or roster_name returns ok False error."""
        db.update_google_config(self.conn, roster_sender="", roster_name="Test Person")
        gmail = FakeGmail()
        res = sync(self.conn, gmail, self.today)
        self.assertFalse(res["ok"])
        self.assertEqual(res["message"], "Fill in the sender's email address and your name first.")
        self.assertEqual(res["months"], [])
        self.assertEqual(res["ignored"], 0)
        self.assertEqual(res["errors"], [])
        self.assertEqual(res["changed_months"], [])

        db.update_google_config(self.conn, roster_sender="planner@example.com", roster_name="   ")
        res2 = sync(self.conn, gmail, self.today)
        self.assertFalse(res2["ok"])
        self.assertEqual(res2["message"], "Fill in the sender's email address and your name first.")

    def test_sender_mismatch_by_display_name_only(self):
        """Display name containing the sender address while real address differs is skipped."""
        gmail = FakeGmail(
            messages={
                "m1": {
                    "id": "m1",
                    "from": "planner@example.com <impostor@example.com>",
                    "subject": "Roster",
                    "internal_date": 1000,
                    "attachments": [
                        {"filename": "roster.pdf", "mime_type": "application/pdf", "attachment_id": "a1"}
                    ],
                }
            },
            attachments={("m1", "a1"): b"%PDF-1.4 test"},
        )
        res = sync(self.conn, gmail, self.today)
        self.assertTrue(res["ok"])
        self.assertEqual(res["message"], "No roster found in your mail.")
        self.assertEqual(res["months"], [])
        self.assertEqual(res["ignored"], 0)
        self.assertEqual(res["errors"], [])
        self.assertEqual(res["changed_months"], [])

    def test_mail_without_pdf(self):
        """Mails without PDF attachments are skipped silently."""
        gmail = FakeGmail(
            messages={
                "m1": {
                    "id": "m1",
                    "from": "planner@example.com",
                    "subject": "Notes",
                    "internal_date": 1000,
                    "attachments": [
                        {"filename": "roster.png", "mime_type": "image/png", "attachment_id": "a1"},
                        {"filename": "table.xlsx", "mime_type": "application/vnd.ms-excel", "attachment_id": "a2"},
                    ],
                },
                "m2": {
                    "id": "m2",
                    "from": "planner@example.com",
                    "subject": "Empty",
                    "internal_date": 1001,
                    "attachments": [],
                },
            }
        )
        res = sync(self.conn, gmail, self.today)
        self.assertTrue(res["ok"])
        self.assertEqual(res["message"], "No roster found in your mail.")
        self.assertEqual(res["months"], [])
        self.assertEqual(res["ignored"], 0)
        self.assertEqual(res["errors"], [])

    def test_pdf_looking_name_but_bytes_not_pdf(self):
        """Attachment named .pdf but not starting with %PDF is skipped silently."""
        gmail = FakeGmail(
            messages={
                "m1": {
                    "id": "m1",
                    "from": "planner@example.com",
                    "subject": "Fake PDF",
                    "internal_date": 1000,
                    "attachments": [
                        {"filename": "roster.pdf", "mime_type": "application/pdf", "attachment_id": "a1"}
                    ],
                }
            },
            attachments={("m1", "a1"): b"HTML text or error response"},
        )
        res = sync(self.conn, gmail, self.today)
        self.assertTrue(res["ok"])
        self.assertEqual(res["message"], "No roster found in your mail.")
        self.assertEqual(res["ignored"], 0)
        self.assertEqual(res["errors"], [])

    def test_not_a_roster_counted_as_ignored(self):
        """NotARoster exception increments ignored and produces no error."""
        gmail = FakeGmail(
            messages={
                "m1": {
                    "id": "m1",
                    "from": "Planner <planner@example.com>",
                    "subject": "Annual Report",
                    "internal_date": 1000,
                    "attachments": [
                        {"filename": "report.pdf", "mime_type": "application/pdf", "attachment_id": "a1"}
                    ],
                }
            },
            attachments={("m1", "a1"): b"%PDF-1.4 data"},
        )

        def fake_extract(data):
            return ["token"]

        def fake_parse(tokens, name):
            raise NotARoster("Not a roster")

        res = sync(self.conn, gmail, self.today, extract_tokens=fake_extract, parse=fake_parse)
        self.assertTrue(res["ok"])
        self.assertEqual(res["message"], "No roster found in your mail.")
        self.assertEqual(res["ignored"], 1)
        self.assertEqual(res["errors"], [])

    def test_roster_error_and_pdf_error_listed_in_errors(self):
        """PdfError and other RosterError are appended to errors with subject or id label."""
        gmail = FakeGmail(
            messages={
                "m1": {
                    "id": "m1",
                    "from": "planner@example.com",
                    "subject": "Damaged PDF",
                    "internal_date": 1000,
                    "attachments": [
                        {"filename": "damaged.pdf", "mime_type": "application/pdf", "attachment_id": "a1"}
                    ],
                },
                "m2": {
                    "id": "m2",
                    "from": "planner@example.com",
                    "subject": "",
                    "internal_date": 1001,
                    "attachments": [
                        {"filename": "roster.pdf", "mime_type": "application/pdf", "attachment_id": "a2"}
                    ],
                },
            },
            attachments={
                ("m1", "a1"): b"%PDF-1.4 damaged",
                ("m2", "a2"): b"%PDF-1.4 ok",
            },
        )

        def fake_extract(data):
            if b"damaged" in data:
                raise PdfError("Corrupted file")
            return ["token"]

        def fake_parse(tokens, name):
            raise RosterError("Name not found")

        res = sync(self.conn, gmail, self.today, extract_tokens=fake_extract, parse=fake_parse)
        self.assertTrue(res["ok"])
        self.assertEqual(res["message"], "No roster found in your mail.")
        self.assertEqual(res["ignored"], 0)
        self.assertEqual(
            res["errors"],
            ["Damaged PDF: Corrupted file", "m2: Name not found"],
        )

    def test_two_mails_one_month_newest_wins_and_tie_breaking(self):
        """When multiple mails are found for the same month, highest internal_date wins; later wins ties."""
        # 1. Higher internal_date wins
        gmail1 = FakeGmail(
            messages={
                "m1": {
                    "id": "m1",
                    "from": "planner@example.com",
                    "subject": "Old version",
                    "internal_date": 1000,
                    "attachments": [{"filename": "roster.pdf", "mime_type": "application/pdf", "attachment_id": "a1"}],
                },
                "m2": {
                    "id": "m2",
                    "from": "planner@example.com",
                    "subject": "New version",
                    "internal_date": 2000,
                    "attachments": [{"filename": "roster.pdf", "mime_type": "application/pdf", "attachment_id": "a2"}],
                },
            },
            attachments={
                ("m1", "a1"): b"%PDF-1",
                ("m2", "a2"): b"%PDF-2",
            },
        )

        def fake_parse(tokens, name):
            if tokens == ["pdf-1"]:
                return {
                    "month": "2026-12",
                    "days": [{"date": "2026-12-01", "minutes": 450, "code": "V", "start": 450, "end": 930}],
                    "legend": {"V": {"start": 450, "end": 930, "minutes": 450}},
                }
            return {
                "month": "2026-12",
                "days": [
                    {"date": "2026-12-01", "minutes": 450, "code": "V", "start": 450, "end": 930},
                    {"date": "2026-12-02", "minutes": 450, "code": "V", "start": 450, "end": 930},
                ],
                "legend": {"V": {"start": 450, "end": 930, "minutes": 450}},
            }

        def fake_extract(data):
            return ["pdf-1" if data == b"%PDF-1" else "pdf-2"]

        res = sync(self.conn, gmail1, self.today, extract_tokens=fake_extract, parse=fake_parse)
        self.assertTrue(res["ok"])
        self.assertEqual(res["changed_months"], ["2026-12"])
        days = db.list_days(self.conn, "2026-12")
        self.assertEqual(len(days), 2)
        import_row = db.get_roster_import(self.conn, "2026-12")
        self.assertEqual(import_row["message_id"], "m2")
        self.assertEqual(import_row["internal_date"], 2000)

        # 2. Tie in internal_date: later processed wins
        conn2 = db.connect(":memory:")
        db.update_google_config(conn2, roster_sender="planner@example.com", roster_name="Test Person")
        gmail2 = FakeGmail(
            messages={
                "m1": {
                    "id": "m1",
                    "from": "planner@example.com",
                    "subject": "First",
                    "internal_date": 1500,
                    "attachments": [{"filename": "roster.pdf", "mime_type": "application/pdf", "attachment_id": "a1"}],
                },
                "m2": {
                    "id": "m2",
                    "from": "planner@example.com",
                    "subject": "Second",
                    "internal_date": 1500,
                    "attachments": [{"filename": "roster.pdf", "mime_type": "application/pdf", "attachment_id": "a2"}],
                },
            },
            attachments={
                ("m1", "a1"): b"%PDF-1",
                ("m2", "a2"): b"%PDF-2",
            },
        )
        res_tie = sync(conn2, gmail2, self.today, extract_tokens=fake_extract, parse=fake_parse)
        self.assertTrue(res_tie["ok"])
        import_row2 = db.get_roster_import(conn2, "2026-12")
        self.assertEqual(import_row2["message_id"], "m2")

    def test_past_month_skipped(self):
        """Roster for a month earlier than today's month is skipped with detail 'month is already over'."""
        gmail = FakeGmail(
            messages={
                "m1": {
                    "id": "m1",
                    "from": "planner@example.com",
                    "subject": "Past Roster",
                    "internal_date": 1000,
                    "attachments": [{"filename": "roster.pdf", "mime_type": "application/pdf", "attachment_id": "a1"}],
                }
            },
            attachments={("m1", "a1"): b"%PDF-data"},
        )

        def fake_parse(tokens, name):
            return {
                "month": "2026-10",
                "days": [{"date": "2026-10-01", "minutes": 450, "code": "V", "start": 450, "end": 930}],
                "legend": {"V": {"start": 450, "end": 930, "minutes": 450}},
            }

        res = sync(self.conn, gmail, self.today, extract_tokens=lambda d: ["tok"], parse=fake_parse)
        self.assertTrue(res["ok"])
        self.assertEqual(res["message"], "Everything is up to date.")
        self.assertEqual(
            res["months"],
            [{"work_month": "2026-10", "status": "skipped", "detail": "month is already over"}],
        )
        self.assertEqual(res["changed_months"], [])
        self.assertEqual(db.list_days(self.conn, "2026-10"), [])
        self.assertIsNone(db.get_roster_import(self.conn, "2026-10"))

    def test_unchanged_month_reports_unchanged_and_writes_nothing(self):
        """Second run with identical days reports unchanged, updates roster_import, and doesn't change days."""
        gmail = FakeGmail(
            messages={
                "m1": {
                    "id": "m1",
                    "from": "planner@example.com",
                    "subject": "Roster",
                    "internal_date": 1000,
                    "attachments": [{"filename": "roster.pdf", "mime_type": "application/pdf", "attachment_id": "a1"}],
                }
            },
            attachments={("m1", "a1"): b"%PDF-data"},
        )

        def fake_parse(tokens, name):
            return {
                "month": "2026-12",
                "days": [{"date": "2026-12-05", "minutes": 450, "code": "V", "start": 450, "end": 930}],
                "legend": {"V": {"start": 450, "end": 930, "minutes": 450}},
            }

        # First run: updated
        res1 = sync(self.conn, gmail, self.today, extract_tokens=lambda d: ["tok"], parse=fake_parse)
        self.assertEqual(res1["changed_months"], ["2026-12"])
        self.assertEqual(res1["months"][0]["status"], "updated")

        # Second run with same data but newer mail id:
        gmail.messages["m2"] = {
            "id": "m2",
            "from": "planner@example.com",
            "subject": "Roster re-send",
            "internal_date": 1100,
            "attachments": [{"filename": "roster.pdf", "mime_type": "application/pdf", "attachment_id": "a2"}],
        }
        gmail.attachments[("m2", "a2")] = b"%PDF-data"
        del gmail.messages["m1"]

        res2 = sync(self.conn, gmail, self.today, extract_tokens=lambda d: ["tok"], parse=fake_parse)
        self.assertTrue(res2["ok"])
        self.assertEqual(res2["message"], "Everything is up to date.")
        self.assertEqual(res2["changed_months"], [])
        self.assertEqual(
            res2["months"],
            [{"work_month": "2026-12", "status": "unchanged", "detail": "already up to date"}],
        )
        import_row = db.get_roster_import(self.conn, "2026-12")
        self.assertEqual(import_row["message_id"], "m2")
        self.assertEqual(import_row["internal_date"], 1100)

    def test_changed_month_shows_added_removed_changed_counts(self):
        """Modifications to shifts produce correct added, removed, and changed counters."""
        # Initial state in DB: day 1 (V), day 2 (V)
        db.save_month_days(self.conn, "2026-12", [
            {"date": "2026-12-01", "minutes": 450, "holiday": False, "code": "V", "start": 450, "end": 930},
            {"date": "2026-12-02", "minutes": 450, "holiday": False, "code": "V", "start": 450, "end": 930},
        ])
        db.set_roster_import(self.conn, "2026-12", "m_old", 500, "2026-11-20T00:00:00")

        # New roster:
        # 2026-12-01 changed to D (start 510, end 990)
        # 2026-12-02 removed (not present)
        # 2026-12-03 added (V)
        gmail = FakeGmail(
            messages={
                "m1": {
                    "id": "m1",
                    "from": "planner@example.com",
                    "subject": "Revised",
                    "internal_date": 1000,
                    "attachments": [{"filename": "roster.pdf", "mime_type": "application/pdf", "attachment_id": "a1"}],
                }
            },
            attachments={("m1", "a1"): b"%PDF-data"},
        )

        def fake_parse(tokens, name):
            return {
                "month": "2026-12",
                "days": [
                    {"date": "2026-12-01", "minutes": 450, "code": "D", "start": 510, "end": 990},
                    {"date": "2026-12-03", "minutes": 450, "code": "V", "start": 450, "end": 930},
                ],
                "legend": {
                    "V": {"start": 450, "end": 930, "minutes": 450},
                    "D": {"start": 510, "end": 990, "minutes": 450},
                },
            }

        res = sync(self.conn, gmail, self.today, extract_tokens=lambda d: ["tok"], parse=fake_parse)
        self.assertTrue(res["ok"])
        self.assertEqual(res["message"], "Updated Dec 2026: 1 added, 1 removed, 1 changed.")
        self.assertEqual(res["changed_months"], ["2026-12"])
        self.assertEqual(
            res["months"],
            [{"work_month": "2026-12", "status": "updated", "detail": "1 added, 1 removed, 1 changed"}],
        )

    def test_manual_holiday_kept_if_unused_and_replaced_if_roster_date(self):
        """Holiday on an unused date is kept; a rostered holiday stays a holiday (no shift code)."""
        # Initial holidays in DB: 2026-12-15 and 2026-12-25
        db.save_month_days(self.conn, "2026-12", [
            {"date": "2026-12-15", "minutes": 450, "holiday": True},
            {"date": "2026-12-25", "minutes": 450, "holiday": True},
        ])

        gmail = FakeGmail(
            messages={
                "m1": {
                    "id": "m1",
                    "from": "planner@example.com",
                    "subject": "Christmas roster",
                    "internal_date": 1000,
                    "attachments": [{"filename": "roster.pdf", "mime_type": "application/pdf", "attachment_id": "a1"}],
                }
            },
            attachments={("m1", "a1"): b"%PDF-data"},
        )

        def fake_parse(tokens, name):
            return {
                "month": "2026-12",
                "days": [
                    # 2026-12-25 worked day replaces holiday!
                    {"date": "2026-12-25", "minutes": 450, "code": "D", "start": 510, "end": 990},
                    {"date": "2026-12-26", "minutes": 450, "code": "D", "start": 510, "end": 990},
                ],
                "legend": {"D": {"start": 510, "end": 990, "minutes": 450}},
            }

        res = sync(self.conn, gmail, self.today, extract_tokens=lambda d: ["tok"], parse=fake_parse)
        self.assertTrue(res["ok"])
        days = db.list_days(self.conn, "2026-12")
        days_by_date = {d["date"]: d for d in days}

        # 2026-12-15 was not in wanted, so it remains a holiday
        self.assertIn("2026-12-15", days_by_date)
        self.assertTrue(days_by_date["2026-12-15"]["holiday"])

        # 2026-12-25 was a holiday and is rostered: it stays a holiday, with the shift code D
        self.assertIn("2026-12-25", days_by_date)
        self.assertTrue(days_by_date["2026-12-25"]["holiday"])
        self.assertIsNone(days_by_date["2026-12-25"]["code"])

        # 2026-12-26 is worked day
        self.assertIn("2026-12-26", days_by_date)
        self.assertFalse(days_by_date["2026-12-26"]["holiday"])
        self.assertEqual(days_by_date["2026-12-26"]["code"], "D")

    def test_rostered_public_holiday_is_holiday_and_sync_is_idempotent(self):
        """A rostered Belgian public holiday (not marked by hand) is imported as a holiday; a repeat sync changes nothing."""
        gmail = FakeGmail(
            messages={
                "m1": {
                    "id": "m1",
                    "from": "planner@example.com",
                    "subject": "Roster",
                    "internal_date": 1000,
                    "attachments": [{"filename": "roster.pdf", "mime_type": "application/pdf", "attachment_id": "a1"}],
                }
            },
            attachments={("m1", "a1"): b"%PDF-data"},
        )

        def fake_parse(tokens, name):
            return {
                "month": "2026-12",
                "days": [
                    {"date": "2026-12-25", "minutes": 450, "code": "D", "start": 510, "end": 990},
                    {"date": "2026-12-28", "minutes": 450, "code": "D", "start": 510, "end": 990},
                ],
                "legend": {"D": {"start": 510, "end": 990, "minutes": 450}},
            }

        sync(self.conn, gmail, self.today, extract_tokens=lambda d: ["tok"], parse=fake_parse)
        by_date = {d["date"]: d for d in db.list_days(self.conn, "2026-12")}
        self.assertTrue(by_date["2026-12-25"]["holiday"])
        self.assertIsNone(by_date["2026-12-25"]["code"])
        self.assertFalse(by_date["2026-12-28"]["holiday"])

        res = sync(self.conn, gmail, self.today, extract_tokens=lambda d: ["tok"], parse=fake_parse)
        self.assertEqual([m["status"] for m in res["months"]], ["unchanged"])

    def test_legend_shifts_added_without_overwriting_edited_shift(self):
        """Shifts from legend are added to db, but existing edited shifts are never overwritten."""
        # Pre-seed DB with an edited shift 'V'
        db.save_shift(self.conn, "V", start=400, end=880, minutes=450)

        gmail = FakeGmail(
            messages={
                "m1": {
                    "id": "m1",
                    "from": "planner@example.com",
                    "subject": "Roster",
                    "internal_date": 1000,
                    "attachments": [{"filename": "roster.pdf", "mime_type": "application/pdf", "attachment_id": "a1"}],
                }
            },
            attachments={("m1", "a1"): b"%PDF-data"},
        )

        def fake_parse(tokens, name):
            return {
                "month": "2026-12",
                "days": [{"date": "2026-12-01", "minutes": 450, "code": "X", "start": 600, "end": 1080}],
                "legend": {
                    "V": {"start": 450, "end": 930, "minutes": 450},  # should NOT overwrite DB
                    "X": {"start": 600, "end": 1080, "minutes": 450},  # should be added to DB
                },
            }

        res = sync(self.conn, gmail, self.today, extract_tokens=lambda d: ["tok"], parse=fake_parse)
        self.assertTrue(res["ok"])

        shifts = {s["code"]: s for s in db.list_shifts(self.conn)}
        self.assertIn("X", shifts)
        self.assertEqual(shifts["X"]["start"], 600)
        # V should still have the custom start=400
        self.assertEqual(shifts["V"]["start"], 400)
        self.assertEqual(shifts["V"]["end"], 880)

    def test_older_mail_than_applied_is_skipped(self):
        """Mail with lower internal_date than the already applied roster is skipped."""
        db.set_roster_import(self.conn, "2026-12", "m_newer", 5000, "2026-11-20T00:00:00")

        gmail = FakeGmail(
            messages={
                "m_older": {
                    "id": "m_older",
                    "from": "planner@example.com",
                    "subject": "Old roster",
                    "internal_date": 4000,
                    "attachments": [{"filename": "roster.pdf", "mime_type": "application/pdf", "attachment_id": "a1"}],
                }
            },
            attachments={("m_older", "a1"): b"%PDF-data"},
        )

        def fake_parse(tokens, name):
            return {
                "month": "2026-12",
                "days": [{"date": "2026-12-01", "minutes": 450, "code": "V", "start": 450, "end": 930}],
                "legend": {"V": {"start": 450, "end": 930, "minutes": 450}},
            }

        res = sync(self.conn, gmail, self.today, extract_tokens=lambda d: ["tok"], parse=fake_parse)
        self.assertTrue(res["ok"])
        self.assertEqual(res["message"], "Everything is up to date.")
        self.assertEqual(
            res["months"],
            [{"work_month": "2026-12", "status": "skipped", "detail": "a newer roster was already applied"}],
        )
        self.assertEqual(res["changed_months"], [])
        import_row = db.get_roster_import(self.conn, "2026-12")
        self.assertEqual(import_row["message_id"], "m_newer")

    def test_gmail_error_from_search_and_get_message(self):
        """GmailError from search or get_message returns ok False and error message."""
        # 1. Error on search
        gmail1 = FakeGmail()
        gmail1.search_error = GmailError("Network timeout connecting to Gmail", 504)
        res1 = sync(self.conn, gmail1, self.today)
        self.assertFalse(res1["ok"])
        self.assertEqual(res1["message"], "Network timeout connecting to Gmail")
        self.assertEqual(res1["months"], [])

        # 2. Error on get_message
        gmail2 = FakeGmail(
            messages={
                "m1": {"id": "m1", "from": "planner@example.com", "subject": "Roster"}
            }
        )
        gmail2.get_message_error = GmailError("Message corrupted", 500)
        res2 = sync(self.conn, gmail2, self.today)
        self.assertFalse(res2["ok"])
        self.assertEqual(res2["message"], "Message corrupted")
        self.assertEqual(res2["months"], [])

    def test_message_text_formatting(self):
        """Verify summary messages: no roster, everything up to date, single update, and multiple updates."""
        # 1. No roster found
        gmail_empty = FakeGmail()
        res_empty = sync(self.conn, gmail_empty, self.today)
        self.assertEqual(res_empty["message"], "No roster found in your mail.")

        # 2. Updated Dec 2026
        gmail_update = FakeGmail(
            messages={
                "m1": {
                    "id": "m1",
                    "from": "planner@example.com",
                    "subject": "Dec",
                    "internal_date": 1000,
                    "attachments": [{"filename": "roster.pdf", "mime_type": "application/pdf", "attachment_id": "a1"}],
                }
            },
            attachments={("m1", "a1"): b"%PDF-data"},
        )

        def fake_parse_dec(tokens, name):
            return {
                "month": "2026-12",
                "days": [{"date": "2026-12-01", "minutes": 450, "code": "V", "start": 450, "end": 930}],
                "legend": {"V": {"start": 450, "end": 930, "minutes": 450}},
            }

        res_update = sync(self.conn, gmail_update, self.today, extract_tokens=lambda d: ["tok"], parse=fake_parse_dec)
        self.assertEqual(res_update["message"], "Updated Dec 2026: 1 added, 0 removed, 0 changed.")

        # 3. Multiple updated months
        conn_multi = db.connect(":memory:")
        db.update_google_config(conn_multi, roster_sender="planner@example.com", roster_name="Test Person")
        gmail_multi = FakeGmail(
            messages={
                "m1": {
                    "id": "m1",
                    "from": "planner@example.com",
                    "subject": "Dec",
                    "internal_date": 1000,
                    "attachments": [{"filename": "dec.pdf", "mime_type": "application/pdf", "attachment_id": "a1"}],
                },
                "m2": {
                    "id": "m2",
                    "from": "planner@example.com",
                    "subject": "Jan",
                    "internal_date": 1001,
                    "attachments": [{"filename": "jan.pdf", "mime_type": "application/pdf", "attachment_id": "a2"}],
                },
            },
            attachments={
                ("m1", "a1"): b"%PDF-dec",
                ("m2", "a2"): b"%PDF-jan",
            },
        )

        def fake_parse_multi(tokens, name):
            if tokens == ["dec"]:
                return {
                    "month": "2026-12",
                    "days": [{"date": "2026-12-01", "minutes": 450, "code": "V", "start": 450, "end": 930}],
                    "legend": {"V": {"start": 450, "end": 930, "minutes": 450}},
                }
            return {
                "month": "2027-01",
                "days": [{"date": "2027-01-05", "minutes": 450, "code": "V", "start": 450, "end": 930}],
                "legend": {"V": {"start": 450, "end": 930, "minutes": 450}},
            }

        def fake_extract_multi(data):
            return ["dec" if data == b"%PDF-dec" else "jan"]

        res_multi = sync(
            conn_multi,
            gmail_multi,
            self.today,
            extract_tokens=fake_extract_multi,
            parse=fake_parse_multi,
        )
        self.assertEqual(
            res_multi["message"],
            "Updated Dec 2026: 1 added, 0 removed, 0 changed. Updated Jan 2027: 1 added, 0 removed, 0 changed.",
        )

    def test_real_parse_roster_with_fixture(self):
        """Test with real parse_roster and fixture tokens: month 2026-12, 8 days, codes D and V."""
        fixture_path = os.path.join(
            os.path.dirname(__file__), "fixtures", "roster_tokens.json"
        )
        with open(fixture_path, "r", encoding="utf-8") as f:
            tokens = json.load(f)

        gmail = FakeGmail(
            messages={
                "m1": {
                    "id": "m1",
                    "from": "Boss <planner@example.com>",
                    "subject": "December Roster",
                    "internal_date": 1234567,
                    "attachments": [
                        {"filename": "Rooster_Dec_2026.pdf", "mime_type": "application/pdf", "attachment_id": "att1"}
                    ],
                }
            },
            attachments={("m1", "att1"): b"%PDF-fixture-tokens"},
        )

        def fake_extract(data):
            self.assertEqual(data, b"%PDF-fixture-tokens")
            return tokens

        res = sync(
            self.conn,
            gmail,
            self.today,
            extract_tokens=fake_extract,
            parse=roster.parse_roster,
        )

        self.assertTrue(res["ok"])
        self.assertEqual(res["message"], "Updated Dec 2026: 8 added, 0 removed, 0 changed.")
        self.assertEqual(res["changed_months"], ["2026-12"])
        self.assertEqual(res["ignored"], 0)
        self.assertEqual(res["errors"], [])
        self.assertEqual(
            res["months"],
            [{"work_month": "2026-12", "status": "updated", "detail": "8 added, 0 removed, 0 changed"}],
        )

        days = db.list_days(self.conn, "2026-12")
        self.assertEqual(len(days), 8)
        codes = {d["code"] for d in days}
        self.assertEqual(codes, {"D", "V"})

        import_row = db.get_roster_import(self.conn, "2026-12")
        self.assertIsNotNone(import_row)
        self.assertEqual(import_row["message_id"], "m1")
        self.assertEqual(import_row["internal_date"], 1234567)

    def test_value_error_from_save_month_days(self):
        """ValueError during save_month_days marks month with status 'error' and continues other months."""
        gmail = FakeGmail(
            messages={
                "m1": {
                    "id": "m1",
                    "from": "planner@example.com",
                    "subject": "Dec and Jan",
                    "internal_date": 1000,
                    "attachments": [
                        {"filename": "bad.pdf", "mime_type": "application/pdf", "attachment_id": "a1"},
                        {"filename": "good.pdf", "mime_type": "application/pdf", "attachment_id": "a2"},
                    ],
                }
            },
            attachments={
                ("m1", "a1"): b"%PDF-bad",
                ("m1", "a2"): b"%PDF-good",
            },
        )

        def fake_parse(tokens, name):
            if tokens == ["bad"]:
                return {
                    "month": "2026-12",
                    # Invalid minutes (0) will cause db.save_month_days to raise ValueError
                    "days": [{"date": "2026-12-01", "minutes": 0, "code": "V", "start": 450, "end": 930}],
                    "legend": {"V": {"start": 450, "end": 930, "minutes": 450}},
                }
            return {
                "month": "2027-01",
                "days": [{"date": "2027-01-05", "minutes": 450, "code": "V", "start": 450, "end": 930}],
                "legend": {"V": {"start": 450, "end": 930, "minutes": 450}},
            }

        def fake_extract(data):
            return ["bad" if data == b"%PDF-bad" else "good"]

        res = sync(self.conn, gmail, self.today, extract_tokens=fake_extract, parse=fake_parse)
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["months"]), 2)
        dec = res["months"][0]
        jan = res["months"][1]

        self.assertEqual(dec["work_month"], "2026-12")
        self.assertEqual(dec["status"], "error")
        self.assertIn("minutes", dec["detail"])

        self.assertEqual(jan["work_month"], "2027-01")
        self.assertEqual(jan["status"], "updated")

        self.assertEqual(res["changed_months"], ["2027-01"])
        self.assertEqual(res["message"], "Updated Jan 2027: 1 added, 0 removed, 0 changed.")


    def test_newer_unreadable_mail_blocks_older_roster_for_that_month(self):
        """An older roster must not be applied while a newer roster mail could not be read."""
        def mail(mid, date):
            return {"id": mid, "from": "planner@example.com", "subject": "Roster " + mid, "internal_date": date,
                    "attachments": [{"filename": "r.pdf", "mime_type": "application/pdf", "attachment_id": "a" + mid}]}

        gmail = FakeGmail(
            messages={"old": mail("old", 100), "new": mail("new", 200)},
            attachments={("old", "aold"): b"%PDF-good", ("new", "anew"): b"%PDF-bad"},
        )
        day = {"date": "2026-12-07", "minutes": 450, "code": "V", "start": 450, "end": 930}
        result = {"month": "2026-12", "days": [day], "legend": {}, "total_minutes": 450, "revised": None}

        def parse(tokens, name):
            if tokens == "bad":
                raise RosterError("total mismatch")
            return result

        res = sync(self.conn, gmail, self.today,
                   extract_tokens=lambda data: data.decode().replace("%PDF-", ""), parse=parse)
        self.assertTrue(res["ok"])
        self.assertEqual(res["changed_months"], [])
        self.assertEqual(res["months"][0]["status"], "skipped")
        self.assertIn("could not be read", res["months"][0]["detail"])
        self.assertEqual(len(res["errors"]), 1)
        self.assertEqual(db.list_days(self.conn, "2026-12"), [])


    def _mail(self, mid, internal_date=1000):
        return {
            "id": mid,
            "from": "planner@example.com",
            "subject": "Roster " + mid,
            "internal_date": internal_date,
            "attachments": [
                {"filename": "r.pdf", "mime_type": "application/pdf", "attachment_id": "a"}
            ],
        }

    def _result(self, month="2026-12"):
        return {
            "month": month,
            "days": [{"date": month + "-07", "minutes": 450, "code": "V", "start": 450, "end": 930}],
            "legend": {"V": {"start": 450, "end": 930, "minutes": 450}},
        }

    def _sync_fake(self, gmail, **kwargs):
        return sync(
            self.conn, gmail, self.today,
            extract_tokens=lambda data: data,
            parse=lambda tokens, name: self._result(),
            **kwargs,
        )

    def test_progress_two_mails_and_empty_search(self):
        gmail = FakeGmail(messages={mid: self._mail(mid) for mid in ("p1", "p2")})
        events = []
        self._sync_fake(gmail, progress=lambda percent, text: events.append((percent, text)))
        self.assertEqual(events, [
            (2, "Searching your mail..."),
            (10, "Reading mail 1 of 2..."),
            (45, "Reading mail 2 of 2..."),
            (85, "Updating your days..."),
            (100, "Done"),
        ])
        self.assertEqual([p for p, text in events], sorted(p for p, text in events))
        events.clear()
        self._sync_fake(FakeGmail(), progress=lambda p, t: events.append((p, t)))
        self.assertEqual(events, [(2, "Searching your mail..."),
                                  (85, "Updating your days..."), (100, "Done")])

    def test_progress_missing_config(self):
        db.update_google_config(self.conn, roster_name="")
        events = []
        gmail = FakeGmail()
        res = self._sync_fake(gmail, progress=lambda p, t: events.append((p, t)))
        self.assertFalse(res["ok"])
        self.assertEqual(events, [(100, "Done")])
        self.assertEqual(gmail.recorded_queries, [])

    def test_progress_gmail_errors_and_failed_mail_not_cached(self):
        for source in ("search_error", "get_message_error", "get_attachment_error"):
            with self.subTest(source=source):
                gmail = FakeGmail(messages={source: self._mail(source)},
                                  attachments={(source, "a"): b"%PDF-data"})
                setattr(gmail, source, GmailError("Mail unavailable", 500))
                for attempt in range(2):
                    events = []
                    res = self._sync_fake(gmail, progress=lambda p, t: events.append((p, t)))
                    self.assertFalse(res["ok"])
                    self.assertEqual(res["message"], "Mail unavailable")
                    self.assertEqual(events[0], (2, "Searching your mail..."))
                    self.assertEqual(events[-1], (100, "Done"))
                    self.assertEqual([p for p, t in events], sorted(p for p, t in events))
                    self.assertIsNone(db.get_roster_mail(self.conn, source, "planner@example.com", "Test Person"))
                if source != "search_error":
                    self.assertEqual(len(gmail.get_message_calls), 2)

    def test_cached_roster_restores_cleared_days_without_gmail_calls(self):
        gmail = FakeGmail(messages={"cached": self._mail("cached")},
                          attachments={("cached", "a"): b"%PDF-data"})
        first = self._sync_fake(gmail)
        fields = ("date", "minutes", "holiday", "code", "start", "end")
        expected_days = [tuple(day[field] for field in fields)
                         for day in db.list_days(self.conn, "2026-12")]
        db.save_month_days(self.conn, "2026-12", [])
        gmail.get_message_calls.clear()
        gmail.get_attachment_calls.clear()
        second = self._sync_fake(gmail)
        self.assertEqual(second["months"], first["months"])
        self.assertEqual([tuple(day[field] for field in fields)
                          for day in db.list_days(self.conn, "2026-12")], expected_days)
        self.assertEqual(gmail.get_message_calls, [])
        self.assertEqual(gmail.get_attachment_calls, [])
        third = self._sync_fake(gmail)
        fourth = self._sync_fake(gmail)
        self.assertEqual(third["months"], fourth["months"])
        self.assertEqual(third["months"][0]["status"], "unchanged")
        self.assertEqual(gmail.get_message_calls, [])
        self.assertEqual(gmail.get_attachment_calls, [])

    def test_cache_depends_on_name_and_sender(self):
        gmail = FakeGmail(messages={"config": self._mail("config")},
                          attachments={("config", "a"): b"%PDF-data"})
        self._sync_fake(gmail)
        db.update_google_config(self.conn, roster_name="Another Person")
        self._sync_fake(gmail)
        self.assertEqual(len(gmail.get_message_calls), 2)
        self.assertEqual(len(gmail.get_attachment_calls), 2)
        db.update_google_config(self.conn, roster_sender="other@example.com")
        gmail.messages["config"]["from"] = "other@example.com"
        self._sync_fake(gmail)
        self.assertEqual(len(gmail.get_message_calls), 3)
        self.assertEqual(len(gmail.get_attachment_calls), 3)

    def test_parse_and_pdf_errors_are_retried(self):
        for error_type in (RosterError, PdfError):
            with self.subTest(error_type=error_type):
                mid = error_type.__name__
                gmail = FakeGmail(messages={mid: self._mail(mid)},
                                  attachments={(mid, "a"): b"%PDF-data"})

                def fail(value, *args):
                    raise error_type("Unreadable")

                kwargs = {"extract_tokens": fail} if error_type is PdfError else {
                    "extract_tokens": lambda data: data, "parse": fail,
                }
                first = sync(self.conn, gmail, self.today, **kwargs)
                second = sync(self.conn, gmail, self.today, **kwargs)
                self.assertEqual(first["errors"], ["Roster " + mid + ": Unreadable"])
                self.assertEqual(second["errors"], first["errors"])
                self.assertEqual(len(gmail.get_message_calls), 2)
                self.assertEqual(len(gmail.get_attachment_calls), 2)
                self.assertIsNone(db.get_roster_mail(self.conn, mid, "planner@example.com", "Test Person"))

    def test_two_verified_pdfs_are_not_cached(self):
        mail = self._mail("multi")
        mail["attachments"].append({"filename": "second.pdf", "attachment_id": "b"})
        gmail = FakeGmail(messages={"multi": mail}, attachments={
            ("multi", "a"): b"%PDF-first", ("multi", "b"): b"%PDF-second",
        })
        self._sync_fake(gmail)
        self._sync_fake(gmail)
        self.assertEqual(len(gmail.get_message_calls), 2)
        self.assertEqual(len(gmail.get_attachment_calls), 4)
        self.assertIsNone(db.get_roster_mail(self.conn, "multi", "planner@example.com", "Test Person"))

    def test_skip_and_ignored_cached(self):
        messages = {mid: self._mail(mid) for mid in ("sender", "empty", "bytes", "ignored")}
        messages["sender"]["from"] = "impostor@example.com"
        messages["empty"]["attachments"] = []
        gmail = FakeGmail(messages=messages, attachments={
            ("bytes", "a"): b"Not PDF", ("ignored", "a"): b"%PDF-report",
        })

        def not_roster(tokens, name):
            raise NotARoster("Annual report")

        first = sync(self.conn, gmail, self.today, extract_tokens=lambda data: data, parse=not_roster)
        for mid in messages:
            cached = db.get_roster_mail(self.conn, mid, "planner@example.com", "Test Person")
            self.assertEqual(cached["kind"], "ignored" if mid == "ignored" else "skip")
        gmail.get_message_calls.clear()
        gmail.get_attachment_calls.clear()
        second = sync(self.conn, gmail, self.today, extract_tokens=lambda data: data, parse=not_roster)
        self.assertEqual(second, first)
        self.assertEqual(second["ignored"], 1)
        self.assertEqual(gmail.get_message_calls, [])
        self.assertEqual(gmail.get_attachment_calls, [])

    def test_one_verified_pdf_with_non_pdf_attachment_is_cached(self):
        mail = self._mail("mixed")
        mail["attachments"].append({"filename": "fake.pdf", "attachment_id": "b"})
        gmail = FakeGmail(messages={"mixed": mail}, attachments={
            ("mixed", "a"): b"%PDF-roster", ("mixed", "b"): b"HTML",
        })
        self._sync_fake(gmail)
        self._sync_fake(gmail)
        self.assertEqual(len(gmail.get_message_calls), 1)
        self.assertEqual(len(gmail.get_attachment_calls), 2)

    def test_only_month_past_month_filter_query_and_messages(self):
        gmail = FakeGmail(messages={mid: self._mail(mid) for mid in ("dec", "jan")}, attachments={
            ("dec", "a"): b"%PDF-dec", ("jan", "a"): b"%PDF-jan",
        })

        def parse_month(data, name):
            return self._result("2026-12" if data == b"%PDF-dec" else "2027-01")

        def run():
            return sync(self.conn, gmail, datetime.date(2027, 2, 1),
                        extract_tokens=lambda data: data, parse=parse_month, only_month="2026-12")

        first = run()
        self.assertEqual(first["message"], "Updated Dec 2026: 1 added, 0 removed, 0 changed.")
        self.assertEqual(first["changed_months"], ["2026-12"])
        self.assertEqual([m["work_month"] for m in first["months"]], ["2026-12"])
        self.assertEqual(len(db.list_days(self.conn, "2026-12")), 1)
        self.assertEqual(db.list_days(self.conn, "2027-01"), [])
        self.assertIsNone(db.get_roster_import(self.conn, "2027-01"))
        query, limit = gmail.recorded_queries[0]
        self.assertEqual(query, "from:planner@example.com has:attachment filename:pdf after:2026/09/02")
        self.assertGreaterEqual(limit, 60)
        self.assertEqual(run()["message"], "Dec 2026 is already up to date.")
        db.set_roster_import(self.conn, "2026-12", "newer", 5000, "2027-02-01T00:00:00")
        skipped = run()
        self.assertEqual(skipped["months"][0]["status"], "skipped")
        self.assertEqual(skipped["message"], "Dec 2026: a newer roster was already applied.")
        gmail.messages.pop("dec")
        absent = run()
        self.assertEqual(absent["months"], [])
        self.assertEqual(absent["message"], "No roster for Dec 2026 found in your mail.")

    def test_only_month_limit_preserves_larger_max_mails(self):
        gmail = FakeGmail()
        self._sync_fake(gmail, only_month="2026-12", max_mails=80)
        self.assertEqual(gmail.recorded_queries[0][1], 80)

    def test_only_month_newer_unreadable_mail_blocks_cached_roster(self):
        gmail = FakeGmail(messages={"old": self._mail("old", 100)},
                          attachments={("old", "a"): b"%PDF-good"})
        self._sync_fake(gmail, only_month="2026-12")
        db.save_month_days(self.conn, "2026-12", [])
        gmail.messages["new"] = self._mail("new", 200)
        gmail.attachments[("new", "a")] = b"%PDF-bad"

        def fail(tokens, name):
            raise RosterError("total mismatch")

        for attempt in range(2):
            res = sync(self.conn, gmail, datetime.date(2027, 2, 1),
                       extract_tokens=lambda data: data, parse=fail, only_month="2026-12")
            detail = "a newer roster mail could not be read, so this month was left unchanged"
            self.assertEqual(res["months"], [{"work_month": "2026-12", "status": "skipped", "detail": detail}])
            self.assertEqual(res["message"], "Dec 2026: " + detail + ".")
            self.assertEqual(res["errors"], ["Roster new: total mismatch"])
            self.assertEqual(res["changed_months"], [])
            self.assertEqual(db.list_days(self.conn, "2026-12"), [])


if __name__ == "__main__":
    unittest.main()
