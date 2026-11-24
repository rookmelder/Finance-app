"""SQLite database storage and queries for finance app."""

from datetime import date
import sqlite3
from typing import Any, Optional

from src import calc, config


def _validate_month(m: str) -> str:
    calc.pay_month(m)  # raises ValueError unless m is a valid YYYY-MM
    return m


def _validate_iso_date(d: str) -> str:
    if not isinstance(d, str) or len(d) != 10 or d[4] != "-" or d[7] != "-":
        raise ValueError(f"Invalid date format: {d!r}")
    try:
        date.fromisoformat(d)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid date: {d!r}")
    return d


def _add_missing_columns(conn: sqlite3.Connection, table: str, columns: dict) -> None:
    have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl in columns.items():  # table/column names are constants above, never user input
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                rate_cents INTEGER NOT NULL,
                allowance_cents INTEGER NOT NULL,
                minutes_per_day INTEGER NOT NULL,
                other_cents INTEGER NOT NULL,
                needs_pct INTEGER NOT NULL,
                free_pct INTEGER NOT NULL,
                save_pct INTEGER NOT NULL,
                carry_over INTEGER NOT NULL,
                clothing_cents INTEGER NOT NULL DEFAULT 0,
                sunday_premium_cents INTEGER NOT NULL DEFAULT 1200,
                rsz_bp INTEGER NOT NULL DEFAULT 271,
                sunday_cents_per_hour INTEGER NOT NULL DEFAULT 200,
                sunday_cap_minutes INTEGER NOT NULL DEFAULT 360
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS income (
                pay_month TEXT PRIMARY KEY,
                work_month TEXT NOT NULL,
                days INTEGER NOT NULL,
                minutes_override INTEGER NULL,
                actual_cents INTEGER NULL,
                rate_cents INTEGER NOT NULL,
                allowance_cents INTEGER NOT NULL,
                minutes_per_day INTEGER NOT NULL,
                other_cents INTEGER NOT NULL,
                needs_pct INTEGER NOT NULL,
                free_pct INTEGER NOT NULL,
                save_pct INTEGER NOT NULL,
                sundays INTEGER NOT NULL DEFAULT 0,
                holiday_minutes INTEGER NOT NULL DEFAULT 0,
                clothing_cents INTEGER NOT NULL DEFAULT 0,
                sunday_premium_cents INTEGER NOT NULL DEFAULT 1200,
                rsz_bp INTEGER NOT NULL DEFAULT 271,
                sunday_cents_per_hour INTEGER NOT NULL DEFAULT 200,
                sunday_cap_minutes INTEGER NOT NULL DEFAULT 360,
                per_day INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS work_days (
                date TEXT PRIMARY KEY,
                minutes INTEGER NOT NULL CHECK (minutes BETWEEN 1 AND 1440),
                holiday INTEGER NOT NULL DEFAULT 0,
                code TEXT NULL,
                start_min INTEGER NULL,
                end_min INTEGER NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shifts (
                code TEXT PRIMARY KEY,
                start_min INTEGER NOT NULL,
                end_min INTEGER NOT NULL,
                minutes INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS roster_import (
                work_month TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                internal_date INTEGER NOT NULL,
                applied_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS roster_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                synced_at TEXT NULL,
                report TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recurring (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                every_months INTEGER NOT NULL,
                notes TEXT NOT NULL,
                active INTEGER NOT NULL,
                next_due TEXT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                bucket TEXT NOT NULL CHECK (bucket IN ('needs', 'free', 'savings')),
                note TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS google_sync (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                refresh_token TEXT NOT NULL,
                calendar_id TEXT NULL,
                last_sync TEXT NULL,
                last_error TEXT NULL,
                scopes TEXT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS google_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                client_id TEXT NOT NULL DEFAULT '',
                client_secret TEXT NOT NULL DEFAULT '',
                redirect_uri TEXT NOT NULL DEFAULT '',
                weekday_start INTEGER NOT NULL DEFAULT 450,
                weekday_end INTEGER NOT NULL DEFAULT 930,
                weekend_start INTEGER NOT NULL DEFAULT 510,
                weekend_end INTEGER NOT NULL DEFAULT 990,
                work_color TEXT NOT NULL DEFAULT '6',
                holiday_color TEXT NOT NULL DEFAULT '2',
                roster_sender TEXT NOT NULL DEFAULT '',
                roster_name TEXT NOT NULL DEFAULT ''
            )
        """)
        # Migrate databases made before these columns existed
        _add_missing_columns(conn, "settings", {
            "clothing_cents": "INTEGER NOT NULL DEFAULT 0",
            "sunday_premium_cents": "INTEGER NOT NULL DEFAULT 1200",
            "rsz_bp": "INTEGER NOT NULL DEFAULT 271",
            "sunday_cents_per_hour": "INTEGER NOT NULL DEFAULT 200",
            "sunday_cap_minutes": "INTEGER NOT NULL DEFAULT 360",
        })
        _add_missing_columns(conn, "income", {
            "sundays": "INTEGER NOT NULL DEFAULT 0",
            "holiday_minutes": "INTEGER NOT NULL DEFAULT 0",
            "clothing_cents": "INTEGER NOT NULL DEFAULT 0",
            "sunday_premium_cents": "INTEGER NOT NULL DEFAULT 1200",
            "rsz_bp": "INTEGER NOT NULL DEFAULT 271",
            "sunday_cents_per_hour": "INTEGER NOT NULL DEFAULT 200",
            "sunday_cap_minutes": "INTEGER NOT NULL DEFAULT 360",
            "per_day": "INTEGER NOT NULL DEFAULT 0",
        })
        _add_missing_columns(conn, "google_sync", {"scopes": "TEXT NULL"})
        _add_missing_columns(conn, "work_days", {
            "code": "TEXT NULL",
            "start_min": "INTEGER NULL",
            "end_min": "INTEGER NULL",
        })
        _add_missing_columns(conn, "google_config", {
            "roster_sender": "TEXT NOT NULL DEFAULT ''",
            "roster_name": "TEXT NOT NULL DEFAULT ''",
        })
        # Insert default settings if missing
        conn.execute("""
            INSERT OR IGNORE INTO settings (
                id, rate_cents, allowance_cents, minutes_per_day,
                other_cents, needs_pct, free_pct, save_pct, carry_over
            ) VALUES (1, 0, 0, 450, 0, 50, 20, 30, 1)
        """)
        conn.execute("""
            INSERT OR IGNORE INTO google_config (id) VALUES (1)
        """)
        conn.executemany("""
            INSERT OR IGNORE INTO shifts (code, start_min, end_min, minutes)
            VALUES (?, ?, ?, ?)
        """, [
            ("V", 450, 930, 450),
            ("D", 510, 990, 450),
            ("L", 810, 1290, 450),
        ])
    return conn


def get_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    if row is None:
        raise ValueError("Settings not initialized")
    return dict(row)


def update_settings(conn: sqlite3.Connection, **fields) -> dict[str, Any]:
    allowed_fields = {
        "rate_cents", "allowance_cents", "minutes_per_day",
        "other_cents", "needs_pct", "free_pct", "save_pct", "carry_over",
        "clothing_cents", "rsz_bp", "sunday_cents_per_hour", "sunday_cap_minutes",
    }
    for k in fields:
        if k not in allowed_fields:
            raise ValueError(f"Unknown settings field: {k}")

    current = get_settings(conn)
    merged = {**current, **fields}

    # Validation
    for cent_field in ("rate_cents", "allowance_cents", "other_cents", "clothing_cents", "sunday_cents_per_hour"):
        val = merged[cent_field]
        if not isinstance(val, int) or isinstance(val, bool) or val < 0:
            raise ValueError(f"{cent_field} must be integer >= 0")

    mpd = merged["minutes_per_day"]
    if not isinstance(mpd, int) or isinstance(mpd, bool) or mpd < 1 or mpd > 1440:
        raise ValueError("minutes_per_day must be integer between 1 and 1440")

    for pct_field in ("needs_pct", "free_pct", "save_pct"):
        val = merged[pct_field]
        if not isinstance(val, int) or isinstance(val, bool) or val < 0:
            raise ValueError(f"{pct_field} must be integer >= 0")

    if merged["needs_pct"] + merged["free_pct"] + merged["save_pct"] != 100:
        raise ValueError("Percentages must sum to 100")

    cap = merged["sunday_cap_minutes"]
    if not isinstance(cap, int) or isinstance(cap, bool) or cap < 0 or cap > 1440:
        raise ValueError("sunday_cap_minutes must be an integer between 0 and 1440")
    # the flat per-Sunday premium used by the "add by totals" form is derived from the two fields
    merged["sunday_premium_cents"] = (2 * merged["sunday_cents_per_hour"] * cap + 60) // 120

    rsz = merged["rsz_bp"]
    if not isinstance(rsz, int) or isinstance(rsz, bool) or rsz < 0 or rsz > 10000:
        raise ValueError("rsz_bp must be an integer between 0 and 10000")

    carry = merged["carry_over"]
    if isinstance(carry, bool):
        carry = 1 if carry else 0
        merged["carry_over"] = carry
    if carry not in (0, 1):
        raise ValueError("carry_over must be 0 or 1")

    with conn:
        conn.execute("""
            UPDATE settings SET
                rate_cents = ?,
                allowance_cents = ?,
                minutes_per_day = ?,
                other_cents = ?,
                needs_pct = ?,
                free_pct = ?,
                save_pct = ?,
                carry_over = ?,
                clothing_cents = ?,
                sunday_premium_cents = ?,
                rsz_bp = ?,
                sunday_cents_per_hour = ?,
                sunday_cap_minutes = ?
            WHERE id = 1
        """, (
            merged["rate_cents"],
            merged["allowance_cents"],
            merged["minutes_per_day"],
            merged["other_cents"],
            merged["needs_pct"],
            merged["free_pct"],
            merged["save_pct"],
            merged["carry_over"],
            merged["clothing_cents"],
            merged["sunday_premium_cents"],
            merged["rsz_bp"],
            merged["sunday_cents_per_hour"],
            merged["sunday_cap_minutes"],
        ))
    return get_settings(conn)


_SNAPSHOT = ("rate_cents", "allowance_cents", "minutes_per_day", "other_cents", "needs_pct",
             "free_pct", "save_pct", "clothing_cents", "sunday_premium_cents", "rsz_bp",
             "sunday_cents_per_hour", "sunday_cap_minutes")


def save_income(
    conn: sqlite3.Connection,
    work_month: str,
    days: int,
    minutes_override: Optional[int] = None,
    refresh_snapshot: bool = False,
    sundays: int = 0,
    holiday_minutes: int = 0,
) -> dict[str, Any]:
    pay_m = calc.pay_month(work_month)

    def bad_int(v, lo, hi):
        return not isinstance(v, int) or isinstance(v, bool) or v < lo or v > hi

    if bad_int(days, 0, 31):
        raise ValueError("days must be integer between 0 and 31")
    if minutes_override is not None and bad_int(minutes_override, 0, 44640):
        raise ValueError("minutes_override must be None or integer between 0 and 44640")
    if bad_int(sundays, 0, 31):
        raise ValueError("sundays must be integer between 0 and 31")
    if sundays > days:
        raise ValueError("sundays cannot be more than days worked")
    if bad_int(holiday_minutes, 0, 44640):
        raise ValueError("holiday_minutes must be integer between 0 and 44640")

    existing = conn.execute("SELECT 1 FROM income WHERE pay_month = ?", (pay_m,)).fetchone()
    current = get_settings(conn)
    snap = [current[k] for k in _SNAPSHOT]

    with conn:
        if existing is None:
            conn.execute(
                "INSERT INTO income (pay_month, work_month, days, minutes_override, actual_cents,"
                " sundays, holiday_minutes, " + ", ".join(_SNAPSHOT) + ")"
                " VALUES (?, ?, ?, ?, NULL, ?, ?, " + ", ".join("?" for _ in _SNAPSHOT) + ")",
                [pay_m, work_month, days, minutes_override, sundays, holiday_minutes, *snap],
            )
        elif refresh_snapshot:
            conn.execute(
                "UPDATE income SET work_month = ?, days = ?, minutes_override = ?, sundays = ?,"
                " holiday_minutes = ?, " + ", ".join(k + " = ?" for k in _SNAPSHOT) +
                " WHERE pay_month = ?",
                [work_month, days, minutes_override, sundays, holiday_minutes, *snap, pay_m],
            )
        else:
            conn.execute(
                "UPDATE income SET work_month = ?, days = ?, minutes_override = ?, sundays = ?,"
                " holiday_minutes = ? WHERE pay_month = ?",
                [work_month, days, minutes_override, sundays, holiday_minutes, pay_m],
            )

    return get_income(conn, pay_m)  # type: ignore


def set_actual(conn: sqlite3.Connection, pay_month: str, actual_cents_or_None: Optional[int]) -> dict[str, Any]:
    _validate_month(pay_month)
    if actual_cents_or_None is not None:
        if not isinstance(actual_cents_or_None, int) or isinstance(actual_cents_or_None, bool) or actual_cents_or_None < 0:
            raise ValueError("actual_cents must be None or integer >= 0")

    existing = conn.execute("SELECT * FROM income WHERE pay_month = ?", (pay_month,)).fetchone()
    if existing is None:
        raise ValueError(f"Income row for pay_month {pay_month} does not exist")

    with conn:
        conn.execute("UPDATE income SET actual_cents = ? WHERE pay_month = ?", (actual_cents_or_None, pay_month))

    return get_income(conn, pay_month)  # type: ignore


def get_income(conn: sqlite3.Connection, pay_month: str) -> Optional[dict[str, Any]]:
    _validate_month(pay_month)
    row = conn.execute("SELECT * FROM income WHERE pay_month = ?", (pay_month,)).fetchone()
    return dict(row) if row is not None else None


def list_income(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM income ORDER BY pay_month DESC").fetchall()
    return [dict(r) for r in rows]


def delete_income(conn: sqlite3.Connection, pay_month: str) -> None:
    _validate_month(pay_month)
    existing = conn.execute("SELECT work_month FROM income WHERE pay_month = ?", (pay_month,)).fetchone()
    if existing is None:
        raise ValueError(f"Income row for pay_month {pay_month} not found")
    with conn:
        conn.execute("DELETE FROM work_days WHERE date LIKE ?", (existing["work_month"] + "-%",))
        conn.execute("DELETE FROM income WHERE pay_month = ?", (pay_month,))


def list_days(conn: sqlite3.Connection, work_month: str) -> list[dict[str, Any]]:
    """The worked days of a work month, oldest first: [{"date", "minutes", "holiday", "code", "start", "end"}]."""
    _validate_month(work_month)
    rows = conn.execute(
        "SELECT date, minutes, holiday, code, start_min, end_min FROM work_days WHERE date LIKE ? ORDER BY date",
        (work_month + "-%",)
    ).fetchall()
    return [
        {
            "date": r["date"],
            "minutes": r["minutes"],
            "holiday": bool(r["holiday"]),
            "code": r["code"],
            "start": r["start_min"],
            "end": r["end_min"],
        }
        for r in rows
    ]


def save_month_days(conn: sqlite3.Connection, work_month: str, entries: list) -> Optional[dict[str, Any]]:
    """Replace all worked days of a work month (calendar mode) and switch the month to per-day estimates.

    entries: [{"date": "YYYY-MM-DD", "minutes": 1..1440, "holiday": bool, ...}]. Every date must lie inside work_month,
    no date twice. Creates the income row (with a settings snapshot) when missing; with no entries and no
    income row nothing is created and None is returned. Derived columns days/sundays/minutes_override/
    holiday_minutes are kept in step so lists stay correct.
    """
    pay_m = calc.pay_month(work_month)
    if not isinstance(entries, list):
        raise ValueError("days must be a list")

    existing_days = {
        r["date"]: dict(r)
        for r in conn.execute(
            "SELECT date, minutes, holiday, code, start_min, end_min FROM work_days WHERE date LIKE ?",
            (work_month + "-%",)
        ).fetchall()
    }

    clean = []
    seen = set()
    for e in entries:
        if not isinstance(e, dict):
            raise ValueError("each day must be an object")
        date_s = e.get("date")
        _validate_iso_date(date_s)
        if date_s[:7] != work_month:
            raise ValueError(f"{date_s} is not in {work_month}")
        if date_s in seen:
            raise ValueError(f"duplicate date {date_s}")
        seen.add(date_s)

        holiday = e.get("holiday", False)
        if not isinstance(holiday, bool):
            raise ValueError("holiday must be true or false")

        if "code" in e:
            code_raw = e["code"]
            if code_raw is None:
                code_val = None
                start_min = None
                end_min = None
                minutes = e.get("minutes")
                if not isinstance(minutes, int) or isinstance(minutes, bool) or not (1 <= minutes <= 1440):
                    raise ValueError(f"minutes must be an integer between 1 and 1440 ({date_s})")
            else:
                code_val = _validate_shift_code(code_raw)
                has_start = "start" in e and e["start"] is not None
                has_end = "end" in e and e["end"] is not None
                if has_start != has_end:
                    raise ValueError(f"Both start and end must be provided or neither ({date_s})")

                if has_start and has_end:
                    s_val = e["start"]
                    e_val = e["end"]
                    if isinstance(s_val, bool) or not isinstance(s_val, int) or isinstance(e_val, bool) or not isinstance(e_val, int):
                        raise ValueError(f"start and end must be integers ({date_s})")
                    if not (0 <= s_val < e_val <= 1440):
                        raise ValueError(f"start and end must satisfy 0 <= start < end <= 1440 ({date_s})")
                    start_min = s_val
                    end_min = e_val
                    shift_row = conn.execute(
                        "SELECT minutes FROM shifts WHERE code = ?", (code_val,)
                    ).fetchone()
                else:
                    shift_row = conn.execute(
                        "SELECT start_min, end_min, minutes FROM shifts WHERE code = ?", (code_val,)
                    ).fetchone()
                    if shift_row is None:
                        raise ValueError(f"Unknown shift code: {code_val!r} ({date_s})")
                    start_min = shift_row["start_min"]
                    end_min = shift_row["end_min"]

                explicit_min = e.get("minutes")
                if explicit_min is not None:
                    if not isinstance(explicit_min, int) or isinstance(explicit_min, bool) or not (1 <= explicit_min <= 1440):
                        raise ValueError(f"minutes must be an integer between 1 and 1440 ({date_s})")
                    minutes = explicit_min
                elif shift_row is not None:
                    minutes = shift_row["minutes"]
                else:
                    minutes = end_min - start_min
        else:
            minutes = e.get("minutes")
            if not isinstance(minutes, int) or isinstance(minutes, bool) or not (1 <= minutes <= 1440):
                raise ValueError(f"minutes must be an integer between 1 and 1440 ({date_s})")

            if not holiday and date_s in existing_days:
                prev = existing_days[date_s]
                if not bool(prev["holiday"]) and prev["minutes"] == minutes:
                    code_val = prev["code"]
                    start_min = prev["start_min"]
                    end_min = prev["end_min"]
                else:
                    code_val = None
                    start_min = None
                    end_min = None
            else:
                code_val = None
                start_min = None
                end_min = None

        if holiday:
            code_val = None
            start_min = None
            end_min = None

        clean.append((date_s, minutes, 1 if holiday else 0, code_val, start_min, end_min))

    existing = conn.execute("SELECT 1 FROM income WHERE pay_month = ?", (pay_m,)).fetchone()
    if existing is None and not clean:
        return None
    total = sum(m for _, m, _, _, _, _ in clean)
    holiday_minutes = sum(m for _, m, h, _, _, _ in clean if h)
    sundays = sum(1 for dt, _, _, _, _, _ in clean if date.fromisoformat(dt).weekday() == 6)
    if existing is None:
        save_income(conn, work_month, 0)  # creates the row with a settings snapshot
    with conn:
        conn.execute("DELETE FROM work_days WHERE date LIKE ?", (work_month + "-%",))
        conn.executemany(
            "INSERT INTO work_days (date, minutes, holiday, code, start_min, end_min) VALUES (?, ?, ?, ?, ?, ?)",
            clean,
        )
        conn.execute(
            "UPDATE income SET per_day = 1, days = ?, sundays = ?, minutes_override = ?, holiday_minutes = ?"
            " WHERE pay_month = ?",
            (len(clean), sundays, total, holiday_minutes, pay_m),
        )
    return get_income(conn, pay_m)


def _validate_shift_code(code: Any) -> str:
    if not isinstance(code, str):
        raise ValueError(f"Shift code must be a string: {code!r}")
    code = code.strip().upper()
    if not (1 <= len(code) <= 8) or not code.isalnum() or not code.isascii():
        raise ValueError(f"Invalid shift code: {code!r}")
    return code


def list_shifts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """All shift templates, ordered by start time then code."""
    rows = conn.execute(
        "SELECT code, start_min, end_min, minutes FROM shifts ORDER BY start_min ASC, code ASC"
    ).fetchall()
    return [{"code": r["code"], "start": r["start_min"], "end": r["end_min"], "minutes": r["minutes"]} for r in rows]


def save_shift(conn: sqlite3.Connection, code: str, start: int, end: int, minutes: int) -> dict[str, Any]:
    """Upsert a shift template. Raises ValueError on invalid inputs."""
    code_val = _validate_shift_code(code)
    if isinstance(start, bool) or not isinstance(start, int) or isinstance(end, bool) or not isinstance(end, int):
        raise ValueError("start and end must be integers")
    if not (0 <= start < end <= 1440):
        raise ValueError("start and end must satisfy 0 <= start < end <= 1440")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not (1 <= minutes <= 1440):
        raise ValueError("minutes must be an integer between 1 and 1440")
    with conn:
        conn.execute("""
            INSERT INTO shifts (code, start_min, end_min, minutes)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                start_min = excluded.start_min,
                end_min = excluded.end_min,
                minutes = excluded.minutes
        """, (code_val, start, end, minutes))
    return {"code": code_val, "start": start, "end": end, "minutes": minutes}


def delete_shift(conn: sqlite3.Connection, code: str) -> None:
    """Delete a shift template by code. Work days keep their stored shift data."""
    code_val = _validate_shift_code(code)
    with conn:
        conn.execute("DELETE FROM shifts WHERE code = ?", (code_val,))


def get_roster_import(conn: sqlite3.Connection, work_month: str) -> Optional[dict[str, Any]]:
    """Which roster mail was last applied to a work month: {"work_month","message_id","internal_date" (ms),"applied_at"} or None."""
    _validate_month(work_month)
    row = conn.execute("SELECT * FROM roster_import WHERE work_month = ?", (work_month,)).fetchone()
    return dict(row) if row else None


def set_roster_import(conn: sqlite3.Connection, work_month: str, message_id: str, internal_date: int,
                      applied_at: str) -> None:
    _validate_month(work_month)
    with conn:
        conn.execute(
            "INSERT INTO roster_import (work_month, message_id, internal_date, applied_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(work_month) DO UPDATE SET message_id = excluded.message_id,"
            " internal_date = excluded.internal_date, applied_at = excluded.applied_at",
            (work_month, str(message_id), int(internal_date), str(applied_at)))


def get_roster_state(conn: sqlite3.Connection) -> dict[str, Any]:
    """The last roster sync: {"synced_at": str|None, "report": str (JSON text, '' when never synced)}."""
    row = conn.execute("SELECT synced_at, report FROM roster_state WHERE id = 1").fetchone()
    return {"synced_at": row["synced_at"], "report": row["report"]} if row else {"synced_at": None, "report": ""}


def set_roster_state(conn: sqlite3.Connection, synced_at: str, report: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO roster_state (id, synced_at, report) VALUES (1, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET synced_at = excluded.synced_at, report = excluded.report",
            (synced_at, report))


def refresh_snapshot(conn: sqlite3.Connection, pay_month: str) -> dict[str, Any]:
    """Copy the CURRENT settings into an income row's snapshot (days and hours stay as they are)."""
    _validate_month(pay_month)
    if conn.execute("SELECT 1 FROM income WHERE pay_month = ?", (pay_month,)).fetchone() is None:
        raise ValueError(f"Income row for pay_month {pay_month} does not exist")
    current = get_settings(conn)
    with conn:
        conn.execute("UPDATE income SET " + ", ".join(k + " = ?" for k in _SNAPSHOT) + " WHERE pay_month = ?",
                     [current[k] for k in _SNAPSHOT] + [pay_month])
    return get_income(conn, pay_month)  # type: ignore


# --- Recurring ---

def add_recurring(
    conn: sqlite3.Connection,
    name: str,
    amount_cents: int,
    every_months: int = 1,
    notes: str = "",
    active: int = 1,
    next_due: Optional[str] = None
) -> dict[str, Any]:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents <= 0:
        raise ValueError("amount_cents must be integer > 0")
    if not isinstance(every_months, int) or isinstance(every_months, bool) or every_months < 1:
        raise ValueError("every_months must be integer >= 1")
    if not isinstance(notes, str):
        raise ValueError("notes must be a string")
    if isinstance(active, bool):
        active = 1 if active else 0
    if active not in (0, 1):
        raise ValueError("active must be 0 or 1")
    if next_due is not None:
        _validate_iso_date(next_due)

    with conn:
        cur = conn.execute("""
            INSERT INTO recurring (name, amount_cents, every_months, notes, active, next_due)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (name, amount_cents, every_months, notes, active, next_due))
        rec_id = cur.lastrowid

    return get_recurring(conn, rec_id)  # type: ignore


def get_recurring(conn: sqlite3.Connection, rec_id: int) -> Optional[dict[str, Any]]:
    row = conn.execute("SELECT * FROM recurring WHERE id = ?", (rec_id,)).fetchone()
    return dict(row) if row is not None else None


def update_recurring(conn: sqlite3.Connection, rec_id: int, **fields) -> dict[str, Any]:
    current = get_recurring(conn, rec_id)
    if current is None:
        raise ValueError(f"Recurring cost with id {rec_id} not found")

    allowed = {"name", "amount_cents", "every_months", "notes", "active", "next_due"}
    for k in fields:
        if k not in allowed:
            raise ValueError(f"Unknown recurring field: {k}")

    merged = {**current, **fields}

    if not isinstance(merged["name"], str) or not merged["name"].strip():
        raise ValueError("name must be a non-empty string")
    if not isinstance(merged["amount_cents"], int) or isinstance(merged["amount_cents"], bool) or merged["amount_cents"] <= 0:
        raise ValueError("amount_cents must be integer > 0")
    if not isinstance(merged["every_months"], int) or isinstance(merged["every_months"], bool) or merged["every_months"] < 1:
        raise ValueError("every_months must be integer >= 1")
    if not isinstance(merged["notes"], str):
        raise ValueError("notes must be a string")

    active_val = merged["active"]
    if isinstance(active_val, bool):
        active_val = 1 if active_val else 0
        merged["active"] = active_val
    if active_val not in (0, 1):
        raise ValueError("active must be 0 or 1")

    if merged["next_due"] is not None:
        _validate_iso_date(merged["next_due"])

    with conn:
        conn.execute("""
            UPDATE recurring SET
                name = ?,
                amount_cents = ?,
                every_months = ?,
                notes = ?,
                active = ?,
                next_due = ?
            WHERE id = ?
        """, (
            merged["name"],
            merged["amount_cents"],
            merged["every_months"],
            merged["notes"],
            merged["active"],
            merged["next_due"],
            rec_id
        ))

    return get_recurring(conn, rec_id)  # type: ignore


def delete_recurring(conn: sqlite3.Connection, rec_id: int) -> None:
    existing = conn.execute("SELECT 1 FROM recurring WHERE id = ?", (rec_id,)).fetchone()
    if existing is None:
        raise ValueError(f"Recurring cost with id {rec_id} not found")
    with conn:
        conn.execute("DELETE FROM recurring WHERE id = ?", (rec_id,))


def list_recurring(conn: sqlite3.Connection, only_active: bool = False) -> list[dict[str, Any]]:
    if only_active:
        rows = conn.execute("SELECT * FROM recurring WHERE active = 1 ORDER BY name ASC").fetchall()
    else:
        rows = conn.execute("SELECT * FROM recurring ORDER BY name ASC").fetchall()
    return [dict(r) for r in rows]


# --- Expenses ---

def add_expense(
    conn: sqlite3.Connection,
    date_str: str,
    amount_cents: int,
    bucket: str,
    note: str = ""
) -> dict[str, Any]:
    _validate_iso_date(date_str)
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents <= 0:
        raise ValueError("amount_cents must be integer > 0")
    if bucket not in ("needs", "free", "savings"):
        raise ValueError(f"bucket must be one of 'needs', 'free', 'savings'; got {bucket!r}")
    if not isinstance(note, str):
        raise ValueError("note must be a string")

    with conn:
        cur = conn.execute("""
            INSERT INTO expenses (date, amount_cents, bucket, note)
            VALUES (?, ?, ?, ?)
        """, (date_str, amount_cents, bucket, note))
        exp_id = cur.lastrowid

    row = conn.execute("SELECT * FROM expenses WHERE id = ?", (exp_id,)).fetchone()
    return dict(row)


def delete_expense(conn: sqlite3.Connection, exp_id: int) -> None:
    existing = conn.execute("SELECT 1 FROM expenses WHERE id = ?", (exp_id,)).fetchone()
    if existing is None:
        raise ValueError(f"Expense with id {exp_id} not found")
    with conn:
        conn.execute("DELETE FROM expenses WHERE id = ?", (exp_id,))


def list_expenses(conn: sqlite3.Connection, month: Optional[str] = None) -> list[dict[str, Any]]:
    if month is not None:
        _validate_month(month)
        pattern = f"{month}-%"
        rows = conn.execute("SELECT * FROM expenses WHERE date LIKE ? ORDER BY date DESC, id DESC", (pattern,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM expenses ORDER BY date DESC, id DESC").fetchall()
    return [dict(r) for r in rows]


def spent_by_month(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Returns {month: {"needs": int, "free": int, "savings": int}}"""
    rows = conn.execute("""
        SELECT substr(date, 1, 7) AS month, bucket, sum(amount_cents) AS total
        FROM expenses
        GROUP BY month, bucket
        ORDER BY month ASC
    """).fetchall()

    result: dict[str, dict[str, int]] = {}
    for r in rows:
        m = r["month"]
        if m not in result:
            result[m] = {"needs": 0, "free": 0, "savings": 0}
        result[m][r["bucket"]] = r["total"]
    return result


# --- Google Calendar connection (one row, id = 1) ---

def get_google(conn: sqlite3.Connection) -> Optional[dict[str, Any]]:
    row = conn.execute("SELECT * FROM google_sync WHERE id = 1").fetchone()
    return dict(row) if row else None


def connect_google(conn: sqlite3.Connection, refresh_token: str, scopes: Optional[str] = None) -> None:
    """Store a new refresh token and the granted scopes (space separated, None = unknown); keeps an existing calendar id, clears the error."""
    if not isinstance(refresh_token, str) or not refresh_token:
        raise ValueError("refresh_token must be a non-empty string")
    with conn:
        conn.execute("""
            INSERT INTO google_sync (id, refresh_token, scopes) VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET refresh_token = excluded.refresh_token, scopes = excluded.scopes,
                last_error = NULL
        """, (refresh_token, scopes))


def update_google(conn: sqlite3.Connection, **fields) -> None:
    allowed = {"calendar_id", "last_sync", "last_error"}
    for k in fields:
        if k not in allowed:
            raise ValueError(f"Unknown google field: {k}")
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    with conn:
        conn.execute(f"UPDATE google_sync SET {sets} WHERE id = 1", tuple(fields.values()))


def delete_google(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute("DELETE FROM google_sync WHERE id = 1")


# --- Google Calendar config (one row, id = 1) ---

def get_google_config(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM google_config WHERE id = 1").fetchone()
    if row is None:
        raise ValueError("Google config not initialized")
    return dict(row)


def update_google_config(conn: sqlite3.Connection, **fields) -> dict[str, Any]:
    allowed_fields = {
        "client_id", "client_secret", "redirect_uri",
        "weekday_start", "weekday_end",
        "weekend_start", "weekend_end",
        "work_color", "holiday_color",
        "roster_sender", "roster_name",
    }
    for k in fields:
        if k not in allowed_fields:
            raise ValueError(f"Unknown google config field: {k}")

    current = get_google_config(conn)
    merged = {**current, **fields}

    for str_field in ("client_id", "client_secret", "redirect_uri", "roster_sender", "roster_name"):
        val = merged[str_field]
        if not isinstance(val, str):
            raise ValueError(f"{str_field} must be a string")
        val = val.strip()
        if len(val) > 500:
            raise ValueError(f"{str_field} cannot exceed 500 characters")
        merged[str_field] = val

    if merged["redirect_uri"]:
        try:
            config.validate_redirect_uri(merged["redirect_uri"])
        except config.ConfigError:
            raise ValueError("Redirect URI must be an https:// address")

    creds = [merged["client_id"], merged["client_secret"], merged["redirect_uri"]]
    has_creds = [bool(c) for c in creds]
    if any(has_creds) and not all(has_creds):
        raise ValueError("Fill in client id, client secret and redirect URI, or leave all three empty")

    for hour_field in ("weekday_start", "weekday_end", "weekend_start", "weekend_end"):
        val = merged[hour_field]
        if not isinstance(val, int) or isinstance(val, bool) or val < 0 or val > 1440:
            raise ValueError(f"{hour_field} must be an integer between 0 and 1440")

    if merged["weekday_start"] >= merged["weekday_end"]:
        raise ValueError("Weekday work hours: 'from' must be before 'to'")

    if merged["weekend_start"] >= merged["weekend_end"]:
        raise ValueError("Weekend work hours: 'from' must be before 'to'")

    allowed_colors = {str(i) for i in range(1, 12)} | {""}
    for color_field in ("work_color", "holiday_color"):
        val = merged[color_field]
        if not isinstance(val, str) or val not in allowed_colors:
            raise ValueError(f"{color_field} must be one of '' or '1'..'11'")

    if not fields:
        return current

    sets = ", ".join(f"{k} = ?" for k in fields)
    with conn:
        conn.execute(f"UPDATE google_config SET {sets} WHERE id = 1", [merged[k] for k in fields])

    return get_google_config(conn)


def worked_months(conn: sqlite3.Connection) -> list[str]:
    """Every YYYY-MM that has at least one worked day, oldest first."""
    rows = conn.execute("SELECT DISTINCT substr(date, 1, 7) AS m FROM work_days ORDER BY m").fetchall()
    return [r["m"] for r in rows]
