# System: Bank account balance
> The user types what is on their regular bank account once; the app keeps it up to date from pay, expenses and subscriptions and keeps every change in a ledger with a chart.

Back to [Systems](systems.md).

## What it does
Settings has a "Bank account" card: type the balance now (negative allowed, optional note) and the pay day (1-28, default 5). From then on the balance moves by itself: logged expenses (every bucket) lower it, pay raises it on the pay day of its pay month (the estimate until the real pay is entered, then a correction event with the difference), and every active subscription with a "Next due" date lowers it on its due date (then next_due moves on by its interval). Typing the balance again starts a new segment (a jump in the chart). The Overview shows a "Bank account" card (balance, as-of line, still to pay this month, pay expected, expected end of month, chart, History link); `/balance` lists every change newest first.
Month-end leftovers never reset the balance. The settings toggle "carry leftover free budget" (default OFF since 2026-11-24) only decides whether leftover free budget is added to next month's "You can spend".

## Files
- `src/balance.py`: `settle`, `set_balance`, `history`, `current`, `upcoming`, `last_day_of_month` (ISO string dates, integer cents).
- `src/balance_chart.py`: `chart_svg(points, today)`, a pure inline SVG step chart (CSS variables, escaped labels, no scripts).
- `src/db.py`: tables `balance_events` (append-only ledger), `balance_config` (pay_day); `add_balance_event`, `list_balance_events`, `get/update_balance_config`, `max_expense_id`.
- `src/app.py`: `_bank_state`, routes `/balance`, `/balance/set`, `/balance/payday`; templates `balance.html`, cards in `settings.html` and `overview.html`; `src/static/balance.css`.
- `tests/test_balance.py`, `tests/test_balance_chart.py`, `tests/test_balance_ui.py`.

## How it connects
- Uses: [Database](system-database.md), [Pay calculations](system-pay-calc.md) (income amount = real pay or the estimate), the expenses log and the recurring costs ([Web app](system-web-app.md)).
- Used by: Overview and Settings pages. `settle` runs (idempotent) whenever Overview, Settings or History is opened.

## Rules (why it is built this way)
- Ledger, not a stored number: events are only appended; `current` and the chart are computed from the last 'set' plus later changes, so history is never lost. See [Bank balance ledger decision](../decisions/decision-bank-balance-ledger.md).
- An expense counts when its date is after the set date, or on the same day with a higher id than the highest expense id at set time (`meta`); backdated and future-dated expenses do not count until valid. Pay and subscription events dated on or before the set date are assumed to be in the typed number.
- Nothing is tracked and no next_due moves before the first balance is typed.
- Subscriptions without a next_due are not subtracted; logging a subscription also as an expense would count it twice.

## Known issues
- A monthly cost whose due day is clamped at a month end keeps its original day only within one settle run (it can drift to the clamped day across runs).
- Deleting an income row or an expense later changes the computed history from that point (ledger events for pay/subscriptions stay).
