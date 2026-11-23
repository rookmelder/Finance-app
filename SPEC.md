# SPEC: finance app v1

Flask + SQLite, server-rendered Jinja, Python 3.12, standard library + Flask only. Read GOAL.md for the why. This file is the exact "what".

## Files
| File | What | Who |
|---|---|---|
| `src/calc.py` | pure functions, NO Flask, NO database | builder, then tester + reviewer |
| `src/db.py` | SQLite schema + CRUD, NO Flask | builder (parallel with calc) |
| `src/auth.py`, `src/config.py` | login, CSRF, env config | Lead |
| `src/app.py` + `src/templates/*.html` + `src/static/style.css` | routes + UI | builderplus |
Tests: `tests/test_calc.py`, `tests/test_db.py`, `tests/test_app.py`.

## Money and time rules (exact)
- Money = integer cents (`int`). Never floats. Hours = integer MINUTES (7.5 h = 450).
- A "month" is the string `YYYY-MM`.
- **Work month** = the month the hours are worked. **Pay month** = work month + 1 = the month the money arrives and the key everything else uses (income, budget, carry-over).
  `pay_month("2026-11") == "2026-12"`, `pay_month("2026-12") == "2027-01"`.
- `default_work_month(today: date) -> str`: if `today.day >= 15` the NEXT calendar month, else the current month. (20 Nov 2026 -> "2026-12"; 10 Nov 2026 -> "2026-11"; 20 Dec 2026 -> "2027-01".)
- Expenses belong to the calendar month of their date (`date[:7]`). The overview of month M shows income whose pay month is M, and expenses dated in M.
- Rounding "half up" on integers: `(2*a + b) // (2*b)` for a/b with positive ints.

## calc.py (exact signatures)
```python
def pay_month(work_month: str) -> str
def default_work_month(today: datetime.date) -> str
def month_range(start: str, end: str) -> list[str]            # inclusive, e.g. ("2026-11","2027-01")
def estimate_cents(rate_cents: int, minutes_per_day: int, days: int,
                   allowance_cents: int, other_cents: int = 0,
                   minutes_override: int | None = None) -> int
#   minutes = minutes_override if not None else days * minutes_per_day
#   hours_pay = round_half_up(rate_cents * minutes / 60)
#   return hours_pay + days * allowance_cents + other_cents
def split_income(income_cents: int, needs_pct: int, free_pct: int, save_pct: int) -> tuple[int, int, int]
#   ValueError unless all >= 0 and sum == 100. needs = income*needs_pct//100, free = income*free_pct//100,
#   savings = income - needs - free  (so the three ALWAYS add up to income)
def monthly_set_aside(amount_cents: int, every_months: int) -> int
#   ceil(amount / every_months); ValueError if every_months < 1 or amount < 0
def parse_money(text: str) -> int       # "14.50" -> 1450, "14,5" -> 1450, "14" -> 1400; ValueError if invalid/negative/more than 2 decimals
def parse_hours(text: str) -> int       # "7.5" -> 450, "7,5" -> 450, "8" -> 480 (minutes); ValueError if invalid/negative
def format_money(cents: int) -> str     # 196200 -> "€1,962.00", -5 -> "-€0.05"
def format_hours(minutes: int) -> str   # 450 -> "7.5", 480 -> "8", 465 -> "7.75"
def budget_chain(income_by_month: dict[str, int], percents: tuple[int, int, int],
                 spent_free_by_month: dict[str, int], carry_over: bool, months: list[str]) -> dict[str, dict]
```
`budget_chain`: for each month in `months` (ascending, consecutive) returns `{"income", "needs", "free", "savings", "carry_in", "free_available", "free_left"}`.
income = `income_by_month.get(m, 0)`; (needs, free, savings) = `split_income`; `carry_in` = previous month's `free_left` if `carry_over` and not the first month, else 0 (can be negative);
`free_available = free + carry_in - spent_free`; `free_left = free_available`. (First month in `months` has carry_in 0, so the caller passes months starting at the first month that has income.)
The 50% leftover never carries over. Per-month percents are the snapshot stored on that month's income; `budget_chain` takes ONE percents tuple for simplicity, so the caller instead passes `income_by_month` values already split: change signature to
`budget_chain(budget_by_month: dict[str, tuple[int,int,int]], spent_free_by_month, carry_over, months)` where each value is `(needs, free, savings)` for that month (missing month = (0,0,0)).

### Golden examples (tests MUST include these)
1. rate 1400, 450 min/day, 18 days, allowance 400 -> `estimate_cents == 196200` (hours 189000 + allowance 7200). `split_income(196200,50,20,30) == (98100, 39240, 58860)`.
2. rounding: rate 1337, 450 min/day, 3 days, allowance 333 -> 30083 + 999 = **31082**. `split_income(31082,50,20,30) == (15541, 6216, 9325)`.
3. override: rate 1400, minutes_override 8040, days 18, allowance 400 -> 187600 + 7200 = **194800**.
4. `monthly_set_aside(2999, 3) == 1000`; `monthly_set_aside(1200, 12) == 100`; `monthly_set_aside(999, 1) == 999`.
5. carry-over chain: months ["2026-12","2027-01"], budgets {"2026-12": (98100,39240,58860), "2027-01": (0,40000,0)}, spent free {"2026-12": 10000, "2027-01": 5000}, carry_over True -> Dec free_left 29240; Jan carry_in 29240, free_available 64240. With carry_over False -> Jan carry_in 0, free_available 35000. Negative: spent 50000 in Dec -> Dec free_left -10760, Jan carry_in -10760.
6. `pay_month("2026-12") == "2027-01"`; `month_range("2026-11","2027-01") == ["2026-11","2026-12","2027-01"]`.

## Database (db.py, SQLite, file path passed in, `:memory:` allowed)
```python
def connect(path: str) -> sqlite3.Connection   # row_factory = sqlite3.Row, foreign keys on, creates tables if missing, inserts default settings row
```
Tables:
- `settings` (single row, id=1): rate_cents, allowance_cents, minutes_per_day (default 450), other_cents (0), needs_pct (50), free_pct (20), save_pct (30), carry_over (1).
- `income`: pay_month TEXT PRIMARY KEY, work_month TEXT NOT NULL, days INTEGER NOT NULL, minutes_override INTEGER NULL, actual_cents INTEGER NULL, and a SNAPSHOT of the settings at creation: rate_cents, allowance_cents, minutes_per_day, other_cents, needs_pct, free_pct, save_pct. (A later raise must not change past months.)
- `recurring`: id, name TEXT, amount_cents, every_months (>=1), notes TEXT, active INTEGER (1), next_due TEXT NULL (ISO date).
- `expenses`: id, date TEXT (ISO), amount_cents (> 0), bucket TEXT CHECK in ('needs','free','savings'), note TEXT.
Functions (all take `conn` first; return dicts or lists of dicts; raise `ValueError` on invalid input, never print):
`get_settings`, `update_settings(conn, **fields)` (validates the three pcts sum to 100, cents >= 0, minutes_per_day 1..1440);
`save_income(conn, work_month, days, minutes_override=None, refresh_snapshot=False)` (pay_month = calc.pay_month(work_month); creates with snapshot of current settings; on an existing pay_month updates days/override and only re-snapshots if `refresh_snapshot`), `set_actual(conn, pay_month, actual_cents_or_None)`, `get_income(conn, pay_month)`, `list_income(conn)` (newest pay_month first), `delete_income`;
`add_recurring/update_recurring/delete_recurring/list_recurring(conn, only_active=False)/get_recurring`;
`add_expense/delete_expense/list_expenses(conn, month=None)/spent_by_month(conn) -> dict[str, dict[str,int]]` (month -> bucket -> cents).
Derived values live in a function `income_view(row) -> dict` in calc.py-free helper `src/views.py`... NO: put it in app code; db.py only stores.

## Overview page logic (app.py, uses calc + db)
For selected pay month M (default: current calendar month):
- income row for M: `estimate` computed from ITS snapshot via `calc.estimate_cents`; `income = actual_cents if not None else estimate`; show both and which one is used.
- (needs, free, savings) = `split_income(income, snapshot pcts)`; months with no income row use (0,0,0) and the current settings are irrelevant.
- Fixed costs: for each active recurring cost `monthly_set_aside`; `fixed_total` = sum; `needs_after_fixed = needs - fixed_total`; `needs_left = needs_after_fixed - spent(needs in M)` (not carried over).
- Free: use `budget_chain` over months from the first income pay month through M (or just M if none); show carry_in, free_available (= what I can spend), spent.
- Savings: `savings` budget and `spent(savings in M)` (amount actually moved to savings/invested), difference shown.
- Everything shown with `format_money`. Negative numbers shown in red.

## Pages (all require login except /login)
`/login` (POST password), `/logout`, `/` overview (+ `?month=YYYY-MM`), `/income` (list + form: work month defaulting to `default_work_month(today)`, days, optional hours override "7.5"/total), `/income/<pay_month>` (edit days, enter/clear actual pay, delete), `/costs` (recurring list + add/edit/delete + total set aside per month), `/expenses` (log + add/delete, filter by month), `/settings`.
Mobile first, one CSS file, English, big touch targets, bottom/top nav with the 5 pages.

## Security (the Lead writes auth.py/config.py)
`debug=False` always; `SECRET_KEY` and `FINANCE_PASSWORD` from env (refuse to start if missing); `hmac.compare_digest`; every route needs login except `/login` and static; CSRF token on every POST; cookie `HttpOnly`, `SameSite=Lax`; listens on `0.0.0.0:8080` (override with `FINANCE_PORT`, `FINANCE_DB` defaults to `data/finance.db`).

## Checks by risk
calc.py: HIGH (money) -> tester AND reviewer. db.py: medium -> reviewer. app.py: medium; auth: HIGH -> security-reviewer.
