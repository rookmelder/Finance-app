"""SQLite database storage and queries for finance app."""

from datetime import date
import sqlite3
from typing import Any, Optional

from src import calc


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
                rsz_bp INTEGER NOT NULL DEFAULT 271
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
                rsz_bp INTEGER NOT NULL DEFAULT 271
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
        # Migrate databases made before these columns existed
        _add_missing_columns(conn, "settings", {
            "clothing_cents": "INTEGER NOT NULL DEFAULT 0",
            "sunday_premium_cents": "INTEGER NOT NULL DEFAULT 1200",
            "rsz_bp": "INTEGER NOT NULL DEFAULT 271",
        })
        _add_missing_columns(conn, "income", {
            "sundays": "INTEGER NOT NULL DEFAULT 0",
            "holiday_minutes": "INTEGER NOT NULL DEFAULT 0",
            "clothing_cents": "INTEGER NOT NULL DEFAULT 0",
            "sunday_premium_cents": "INTEGER NOT NULL DEFAULT 1200",
            "rsz_bp": "INTEGER NOT NULL DEFAULT 271",
        })
        # Insert default settings if missing
        conn.execute("""
            INSERT OR IGNORE INTO settings (
                id, rate_cents, allowance_cents, minutes_per_day,
                other_cents, needs_pct, free_pct, save_pct, carry_over
            ) VALUES (1, 0, 0, 450, 0, 50, 20, 30, 1)
        """)
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
        "clothing_cents", "sunday_premium_cents", "rsz_bp",
    }
    for k in fields:
        if k not in allowed_fields:
            raise ValueError(f"Unknown settings field: {k}")

    current = get_settings(conn)
    merged = {**current, **fields}

    # Validation
    for cent_field in ("rate_cents", "allowance_cents", "other_cents", "clothing_cents", "sunday_premium_cents"):
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
                rsz_bp = ?
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
        ))
    return get_settings(conn)


_SNAPSHOT = ("rate_cents", "allowance_cents", "minutes_per_day", "other_cents", "needs_pct",
             "free_pct", "save_pct", "clothing_cents", "sunday_premium_cents", "rsz_bp")


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
    existing = conn.execute("SELECT 1 FROM income WHERE pay_month = ?", (pay_month,)).fetchone()
    if existing is None:
        raise ValueError(f"Income row for pay_month {pay_month} not found")
    with conn:
        conn.execute("DELETE FROM income WHERE pay_month = ?", (pay_month,))


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
