# System: Database
> SQLite data layer for settings, income snapshots, worked days, recurring costs, expenses, and Google sync.

Back to [Systems](systems.md).

## What it does
Provides the SQLite persistence layer and query interface for the finance application.
Stores application settings, monthly income records, calendar-mode work days, recurring costs, bucketed expenses, and Google Calendar sync tokens.
Handles schema migrations on startup and enforces data integrity across all models.

## Files
- `src/db.py`: schema definitions, migration helper (`_add_missing_columns`), connection factory (`connect`), and query functions for `settings`, `income`, `work_days`, `recurring`, `expenses`, `quick_expenses` (one-tap expense presets: label, amount_cents, bucket), `google_sync`, `google_config`, `shifts`, `roster_import` and `roster_state`.
- `tests/test_db.py`: its tests (covers schema creation, column migrations, settings validation, income snapshots, calendar days, recurring costs, expenses, and SQL injection safety).

## How it connects
- Uses: [Pay calculation](system-pay-calc.md) (`calc.pay_month` for date validation and month shift).
- Used by: [Web application](system-web-app.md), [Calendar UI](system-calendar-ui.md), and [Google Calendar sync](system-google-calendar.md).
- Data: reads and writes SQLite tables (`settings`, `income`, `work_days`, `recurring`, `expenses`, `google_sync`, `google_config`). Money is integer cents, dates are ISO strings (`YYYY-MM-DD` or `YYYY-MM`), and RSZ is stored in basis points.

## Why it is built this way
- Settings snapshots: `income` rows freeze a copy of settings at creation (`rate_cents`, `allowance_cents`, `rsz_bp`, percentages) so historical payslips do not change when settings update.
- Pay month key: `income` is keyed by `pay_month` (`YYYY-MM`) because finances track received pay, while storing the preceding `work_month`.
- Startup migrations: `_add_missing_columns` checks `PRAGMA table_info` in `connect` to add new columns to existing databases without external migration packages.
- Single-row tables: `settings`, `google_sync` and `google_config` enforce `CHECK (id = 1)` to guarantee exactly one active configuration row.
- Calendar mode: `save_month_days` atomically replaces `work_days` for a month and synchronizes derived totals (`days`, `sundays`, `minutes_override`, `holiday_minutes`) into `income` with `per_day = 1`.
- Derived Sunday premium: `sunday_premium_cents` in `settings` is derived from `sunday_cents_per_hour` and `sunday_cap_minutes` rather than edited directly.
- Shifts: table `shifts` (code, start, end, paid minutes; V, D, L seeded) and per-day nullable `work_days.code/start_min/end_min`. `save_month_days` fills times from `shifts` when only a code is given, keeps a stored shift when the entry has no "code" key and the same minutes, and clears it with `"code": null` or for holidays. See [Roster import](system-roster-import.md).
- Roster state: `roster_import` (the mail applied last per work month), `roster_state` (last report JSON), `google_config.roster_sender/roster_name`, `google_sync.scopes` (granted Google scopes).
- Cascading deletes: `delete_income` explicitly deletes associated `work_days` for the month before removing the `income` row.

## Known issues
- none
