# System: Calendar UI
> Google Calendar style month view, interactive day logger, and live pay estimation autosave.

Back to [Systems](systems.md).

## What it does
Provides the primary interactive interface for logging hours and paid holidays per worked day.
Renders a client-side month grid, preset chips, custom hours input, and a per-day modal editor dialog.
Autosaves work entries to the server via debounced JSON requests and updates payslip estimates and Google Calendar in real time.

## Files
- `src/static/calendar.js`: client-side month view grid rendering, day tools (presets, custom hours, erase), drag-and-drop and tap handlers, modal day editor dialog, debounced single-in-flight autosave via `fetch`, and live summary rendering.
- `src/templates/calendar.html`: server-rendered HTML page with CSRF metadata token, month navigation header, embedded `#cal-data` JSON script tag, layout containers, and script inclusion.
- `src/app.py`: server-side calendar route handlers (`calendar_home` redirecting to current month, `calendar_page` bootstrapping `#cal-data` JSON, and `calendar_save` validating and persisting days with CSRF protection and Google Calendar sync).
- `tests/test_calendar_api.py`: its tests (covers `calendar_save` endpoint, CSRF verification, input validation, and income record creation; Google sync is tested in `tests/test_gcal.py`).
- `tests/test_calendar_page.py`: its tests (covers `calendar_home`, `calendar_page`, JSON data embedding in `#cal-data`, authentication requirements, and static asset verification).

## Shifts
The tool bar has one chip per shift (code and times, from `cal.shifts`); painting one sends `{"minutes": shift minutes, "holiday": false, "code": "V"}` and the server fills the times. Untouched days send back their loaded `code/start/end`; plain/custom/holiday days send `"code": null`. Shift days show the code and times in the cell; the day editor has a Shift select. Shift times are edited in Settings ("Shifts"). See [Roster import](system-roster-import.md).

## How it connects
- Uses: [Web Application](system-web-app.md) (route endpoints `calendar_home`, `calendar_page`, `calendar_save`, `_estimate`, `_breakdown_rows`), [Database](system-database.md) (`db.list_days`, `db.save_month_days`, `db.get_income`, `db.get_settings`), [Pay and Budget Calculations](system-pay-calc.md) (`calc.pay_month`, `calc.belgian_holidays`, `calc.estimate_from_days`, `calc.format_money`, `calc.add_months`), [Authentication and Configuration](system-auth-config.md) (session authentication and `X-CSRF-Token` validation), and [Google Calendar sync](system-google-calendar.md) (`_gcal_sync`).
- Used by: [Web Application](system-web-app.md) (linked from the Calendar tab via `/calendar`, the months view `/income`, and the income editor `/income/<pay_month>`); end users in web browsers.
- Data: reads `#cal-data` JSON containing `work_month` (`YYYY-MM`), `pay_month` (`YYYY-MM`), `weekday_offset` (0 for Monday), `days_in_month`, `holidays` (date to name mapping), `presets`, and `entries`. Sends JSON `{"days": [{"date": "YYYY-MM-DD", "minutes": int, "holiday": bool}]}` with `X-CSRF-Token` header to `/calendar/<work_month>/save`. Receives JSON response with status, updated day counts, Sundays count, formatted total, breakdown rows, and `gcal` sync status. Money is integer cents, time is integer minutes (custom hours 0.25 to 24 parsed to minutes), and dates are ISO strings `YYYY-MM-DD`.

## Why it is built this way
- Debounced single-in-flight save: autosaves 400ms after user interactions. If a request is already in-flight, subsequent edits flag `pendingSave` instead of firing concurrent requests, rescheduling after completion.
- Work month vs pay month: the calendar displays and saves by `work_month` (`YYYY-MM`), while income is received and budgeted in `pay_month` (`calc.pay_month(work_month)`), which is the following month.
- Per-day mode enforcement: saving days sets `per_day = 1` on the income record, which disables totals-based additions from the `/income` form to avoid data desynchronization.
- Safe DOM construction: avoids `innerHTML`, `outerHTML`, `document.write`, and `eval(`. Elements, attributes, and text nodes are constructed strictly via DOM methods to prevent XSS.
- Fault-tolerant Google sync: `_gcal_sync` failures do not fail calendar saves; the server saves to SQLite and returns `gcal: "error"`, surfacing a warning toast to the user while keeping local state saved.
- Snapshot rate isolation: uses rate snapshots stored on the income row (or current settings for new months), ensuring rate edits in settings do not silently alter past logged calendar days without explicit snapshot refresh.

## Known issues
- none
