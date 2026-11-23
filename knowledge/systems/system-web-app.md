# System: Web Application
> Server-rendered Flask web application, dashboard, navigation, and financial management views.

Back to [Systems](systems.md).

## What it does
Renders the server-side HTML UI and JSON endpoints for personal finance tracking.
Serves the monthly overview dashboard, worked months list, income editor, recurring costs, bucketed expenses, and settings.
Coordinates database access, pay calculation breakdowns, budget chaining, and Google Calendar sync triggers per request.

## Files
- `src/app.py`: Flask application factory (`create_app`), request DB management (`get_db`, `close_db`), estimation helper (`_estimate`), Google sync caller (`_gcal_sync`), view rendering helpers, template filters, and route endpoints.
- `src/templates/base.html`: base layout shell with top header, desktop and mobile navigation bars (`nav_items`), and CSRF-protected logout form.
- `src/templates/overview.html`: monthly dashboard showing available free budget, carry-in, estimate vs actual income, 3-way budget split, and needs spending after fixed costs.
- `src/templates/income.html`: months view displaying multi-year calendar grid of worked months and collapsible form to add income by totals.
- `src/templates/income_edit.html`: single pay-month income view to view pay breakdown, enter or clear actual received pay, refresh snapshot rates to current settings, or delete income.
- `src/templates/costs.html`: "Monthly costs" recurring bills and subscriptions list with monthly set-aside totals, active toggles, and add/edit/delete forms.
- `src/templates/expenses.html`: discretionary expense log with month filtering, bucket assignment (`needs`, `free`, `savings`), and deletion.
- `src/templates/settings.html`: settings page configuring wage rates, standard work hours, daily allowances, RSZ percentage, budget splits, carry-over toggle, and Google Calendar connection status.
- `tests/test_app.py`: its tests (covers route responses, session authentication, CSRF validation, form submissions, and income calculations).

## How it connects
- Uses: [Pay and Budget Calculations](system-pay-calc.md) (`calc` for parsing, formatting, pay estimation, budget splits, and carry-over chaining), [Database](system-database.md) (`db` for SQLite queries and schema management), [Authentication and Configuration](system-auth-config.md) (`auth` for session login and CSRF protection; `config` for environment settings), and [Google Calendar sync](system-google-calendar.md) (`gcal` for OAuth and calendar sync).
- Used by: HTTP clients and browsers directly; [Calendar UI](system-calendar-ui.md) interacts with `/calendar/<work_month>` and `/calendar/<work_month>/save`.
- Data: reads and writes form data, session cookies, and JSON (`/calendar/<work_month>/save`). Money is integer cents, time is integer minutes, percentages are integer percents or basis points (`rsz_bp`), dates are ISO strings (`YYYY-MM-DD`), and months are `YYYY-MM`.

## Why it is built this way
- Work month vs pay month: work is logged for a work month `YYYY-MM`, but pay is received and budgeted in the following pay month (`pay_month`). Overview (`/`) and income edit (`/income/<pay_month>`) use pay months; the calendar and income overview grid use work months.
- Centralized estimation: `_estimate` is the single breakdown function for all views so overview, income lists, and budget chains never disagree. Uses `calc.estimate_from_days` if `per_day` is set, else `calc.estimate_breakdown`.
- Actual vs estimate: budget split uses `actual_cents` when recorded; otherwise it uses the estimated total.
- Snapshot isolation: each `income` row stores frozen rate snapshot columns (`rate_cents`, `allowance_cents`, `minutes_per_day`, `rsz_bp`). Settings edits do not change existing months unless updated via `refresh_snapshot`.
- Mode protection: POST to `/income` rejects totals-based updates if the month already has calendar days (`per_day = 1`).
- Budget carry-over: overview runs `calc.budget_chain` across all pay months to compute `carry_in` and `free_available`. Carry-in can be negative when past spending exceeded free budget.
- Monthly set-aside: recurring costs with interval `every_months` are smoothed to monthly amounts using `calc.monthly_set_aside` and deducted from the needs budget.
- Error isolation: `_gcal_sync` traps Google API failures, stores `last_error` in SQLite, and prevents external sync errors from breaking saves.
- Post/Redirect/Get: mutations redirect with HTTP 303 to avoid double-submissions; validation errors re-render templates with HTTP 400.

## Known issues
- none
