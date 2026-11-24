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
def budget_chain(budget_by_month: dict[str, tuple[int, int, int]],
                 spent_free_by_month: dict[str, int], carry_over: bool,
                 months: list[str]) -> dict[str, dict]
```
`budget_chain`: `budget_by_month[m]` = `(needs, free, savings)` already split for that month (missing month = (0,0,0)). For each month in `months` (ascending, consecutive) it returns `{"free", "carry_in", "spent_free", "free_available"}`:
`carry_in` = previous month's `free_available` if `carry_over` and the month is not the first in `months`, else 0 (may be negative);
`spent_free` = `spent_free_by_month.get(m, 0)`; `free_available = free + carry_in - spent_free` (this is what I can still spend, and it is what carries into the next month).
The 50% leftover never carries over. The caller passes `months` starting at the first pay month that has income.

### Golden examples (tests MUST include these)
1. rate 1400, 450 min/day, 18 days, allowance 400 -> `estimate_cents == 196200` (hours 189000 + allowance 7200). `split_income(196200,50,20,30) == (98100, 39240, 58860)`.
2. rounding: rate 1337, 450 min/day, 3 days, allowance 333 -> 30083 + 999 = **31082**. `split_income(31082,50,20,30) == (15541, 6216, 9325)`.
3. override: rate 1400, minutes_override 8040, days 18, allowance 400 -> 187600 + 7200 = **194800**.
4. `monthly_set_aside(2999, 3) == 1000`; `monthly_set_aside(1200, 12) == 100`; `monthly_set_aside(999, 1) == 999`.
5. carry-over chain: months ["2026-12","2027-01"], budgets {"2026-12": (98100,39240,58860), "2027-01": (0,40000,0)}, spent free {"2026-12": 10000, "2027-01": 5000}, carry_over True -> Dec free_available 29240; Jan carry_in 29240, free_available 64240. With carry_over False -> Jan carry_in 0, free_available 35000. Negative: spent 50000 in Dec -> Dec free_available -10760, Jan carry_in -10760.
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
db.py only stores data; all derived numbers come from calc.py.

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


---
# Addendum 2026-11-23: calendar mode (per-day work entries)

## Payslip model (supersedes the flat estimate above; see calc.estimate_breakdown)
Gross = hours_pay + holiday_extra + sunday_pay; RSZ = half_up(gross * rsz_bp / 10000); net = gross - RSZ; total = net + days * (fiets + kledij) + other.

## Per-day months
- `work_days` table: `date` TEXT PRIMARY KEY (ISO), `minutes` INTEGER (1..1440), `holiday` INTEGER (0/1, stored, NOT recomputed on read). Work month = `date[:7]`.
- `income.per_day` INTEGER DEFAULT 0. When 1 the estimate comes from the `work_days` rows via `calc.estimate_from_days`; when 0 the legacy formula (days/override/sundays/holiday_minutes) is used. Saving a month's calendar replaces all its day rows in one transaction, creates the income row (with settings snapshot) if missing, sets `per_day=1` and writes derived `days`, `sundays`, `minutes_override` (= total minutes), `holiday_minutes` into the income row so lists stay correct.
- Settings (and the income snapshot): `sunday_cents_per_hour` (default 200), `sunday_cap_minutes` (default 360). `sunday_premium_cents` becomes DERIVED = half_up(per_hour * cap / 60) (= 1200) and is no longer editable; the legacy "Sundays" total field keeps using it.
- Rounding (matches the payslip): hours_pay rounded ONCE on the total minutes; holiday_extra rounded once on total holiday minutes; Sunday premium rounded PER SUNDAY: `half_up(per_hour * min(minutes, cap) / 60)`.
- A Sunday is any day whose date has weekday Sunday. A day that is a holiday gets holiday_extra (extra 1x rate on its minutes). A Sunday that is also a holiday gets BOTH (rule is isolated in estimate_from_days; to be confirmed with the user).
- Belgian legal holidays (`calc.belgian_holidays(year)`): 1 Jan, Easter Monday, 1 May, Ascension (Easter+39), Whit Monday (Easter+50), 21 Jul, 15 Aug, 1 Nov, 11 Nov, 25 Dec. The holiday flag is defaulted from this list when a day is placed; the "Holiday" preset forces it on.

## Endpoints
- `GET /income`: list of months (work month -> pay month, days, estimate, actual) linking to the calendar; `<details>` form "add by totals" keeps the legacy POST /income.
- `GET /calendar/<work_month>`: the calendar page (JSON data in a `<script type="application/json" id="cal-data">`).
- `POST /calendar/<work_month>/save`: JSON body `{"days":[{"date":"2026-11-03","minutes":450,"holiday":false}, ...]}`, header `X-CSRF-Token`. Replies JSON `{"ok":true,"pay_month":"2026-12","days":7,"sundays":1,"total":"€954.00","rows":[{"label":"Hours (50.5 h)","value":"€806.49","kind":"normal"}, ...]}`; on invalid input status 400 `{"ok":false,"error":"..."}`. Never a 500 for bad input.
- `/income/<pay_month>` (income_edit) stays for actual pay, refresh-snapshot and delete.


# Addendum 2026-11-24: shifts and roster import from Gmail

## Goal
The boss emails a one-page PDF roster ("Rooster Technische Dienst") for the whole department. A "Sync roster" button
reads the newest roster mail per month, finds the user's row, and updates the worked days (with shift code and real
start/end times). The Google Calendar event then reads "Work V" and uses the shift's times. Days can also be given a
shift by hand on the calendar page. Work is paid on hours (7.5 h = 450 min): the unpaid 30 min break is NOT in minutes.

## Shifts (db.py, gcal.py)
- Table `shifts` (code TEXT PRIMARY KEY upper-case 1-8 chars A-Z0-9, start_min INTEGER, end_min INTEGER, minutes INTEGER):
  0 <= start_min < end_min <= 1440, 1 <= minutes <= 1440. Seeded with INSERT OR IGNORE: V 450-930 450, D 510-990 450, L 810-1290 450.
- `work_days` gets nullable columns `code TEXT`, `start_min INTEGER`, `end_min INTEGER` (added with _add_missing_columns, old rows stay NULL).
- A day is {"date","minutes","holiday","code","start","end"}; code/start/end are None for a plain day. Holidays never carry a shift.
- `save_month_days` entry keys: date, minutes (optional when code is given), holiday (default false), code (optional),
  start/end (optional minutes). Rules: if "code" is given: it is upper-cased; if start AND end are given they are used
  (both or neither; 0 <= start < end <= 1440), else start/end/minutes come from the `shifts` row (unknown code -> ValueError).
  Explicit "minutes" always wins over the shift's minutes. If the key "code" is ABSENT and the date already exists with the
  same minutes and holiday false, the stored code/start/end are KEPT (the calendar page does not send them back). "code": null clears them.
- gcal.event_body: a non-holiday day with code, start and end becomes a timed event start..end (that day's own times, not the
  weekday/weekend look) with title `Work <CODE>` (e.g. "Work V"). Day without code: unchanged ("Work 7.5h", look hours). Holiday: unchanged.
- db helpers: list_shifts(conn) -> [{"code","start","end","minutes"}] ordered by start, code; save_shift(conn, code, start, end, minutes)
  (upsert, validated, ValueError); delete_shift(conn, code) (days keep their stored code/times).
- google_config gets text columns `roster_sender` and `roster_name` (default ''), settable through update_google_config.

## roster.py (pure, stdlib only): tokens -> days
`parse_roster(tokens, name) -> dict`, tokens = list of (x, y, text), PDF coordinates (y grows upward), as made by
roster_pdf.extract_tokens. Raises `RosterError(Exception)`; `NotARoster(RosterError)` when the title is missing. Returns
{"month": "YYYY-MM", "days": [{"date","minutes","code","start","end"}], "legend": {CODE: {"start","end","minutes"}},
 "total_minutes": int, "revised": "YYYY-MM-DD"|None}. Real example: tests/fixtures/roster_tokens.json (anonymized; the
user's row is "Test Person"; Dec 2026). Layout facts (all from the real sheet):
1. Title: two tokens on the same y (|dy|<1): a month name (English or Dutch, case-insensitive: january/januari ... december)
   and, to its right, a 4-digit year. Exactly one such pair, else NotARoster (none) / RosterError (several).
2. Each section has a weekday row (tokens za zon ma din woe don vrij) and, ~5 pt below it, a number row: tokens "1".."N"
   on one y. The number row for a person is the NEAREST number row ABOVE the person's row (larger y). Number row: >=28 tokens
   that are integers 1..31 on one y (+-0.5). colx[n] = x of token n; step = median difference of consecutive colx.
   Required: numbers are exactly 1..N with N = days in the title's month.
   Cross-check: the weekday token whose x is nearest colx[1] (within step, y within 2..10 above the number row) must be the
   weekday of day 1: monday ma, tuesday din, wednesday woe, thursday don, friday vrij, saturday za, sunday zon, else RosterError.
3. A cell x belongs to day n when |x - colx[n]| <= step/2 for the nearest n; otherwise it is OUTSIDE the month and ignored
   (the grid has columns before day 1 and after day N: e.g. a stray "d" at 1 January MUST NOT end up in December).
4. Person row: the name appears twice (left label x~68, right copy x~538, y differs by ~1.8). Match case-insensitive,
   whitespace collapsed. All matches must lie within 3 pt of each other vertically (one row), else RosterError; none ->
   RosterError("name not found"). anchor_y = y of the RIGHT copy (max x) = the CODE line. HOURS line = tokens with
   anchor_y-8 < y < anchor_y-2.5 (real: 5.2 below). CODE line = tokens with |y-anchor_y| <= 1.0 and x within the grid
   (x >= colx[1]-step/2). Other people's rows are ~10.4 away and must never be read.
5. Total: the numeric token on the hours line with |x - x_right_name| < 20. Missing -> RosterError.
6. Cells: code tokens (letters) on the code line, hour tokens (number, "7.5" or "8") on the hours line, matched per day.
   Two code tokens for one day -> RosterError. Hours via Decimal(text) * 60 -> integer minutes (never float);
   hours must give a whole number of minutes.
7. Legend: rows (cluster tokens by y, tol 1.0) at x < colx[1]-step, above the first number row, that hold a code token
   (letters/digits, x < 50), a time range token matching H:MM - H:MM (one token, e.g. "8:30 - 16:30") and an hours number.
   Rows without a time range (VAR, Perm) are not shifts. legend[CODE] = start/end minutes + minutes = Decimal hours*60.
   end must be > start.
8. Per day in 1..N: code (case-insens.) with hours > 0: must be in the legend (else RosterError "unknown code X on DATE");
   minutes = the CELL's hours (not the legend's); start/end from the legend; code upper-cased.
   Code with hours 0 or with no hours token: if the code is in the legend and has no hours -> RosterError; if not in
   the legend (jv, bf, perm, wr, ...) -> ignored (leave days).
9. sum(minutes of days) MUST equal the total*60 (Decimal) of step 5, else RosterError("total mismatch: ...").
10. "revised": the first token matching M/D/YYYY (US order, e.g. 11/21/2026) as an ISO date, else None; never fails.
Days are sorted by date. Expected for the fixture + name "Test Person": month 2026-12; days 12-06 D, 12-19 D, 12-21 V, 12-22 V,
12-26 D, 12-28 V, 12-29 V, 12-31 V, each 450 min; D = 510-990, V = 450-930; total_minutes 3600; revised 2026-11-21.
(The 9th code, a "d" at 1 January, is outside the month and ignored.)

## roster_pdf.py (Lead writes it): extract_tokens(pdf_bytes) -> list of (x, y, text); uses pypdf visitor_text on page 0.

## gmail.py: Gmail REST client (urllib, stdlib only), same style as gcal.py
Base https://gmail.googleapis.com/gmail/v1/users/me . `GmailError(Exception)` with .status. `Client(token_provider, transport=urllib_transport)`;
token_provider(force=False) -> access token string (the app passes a gcal.Client's token function). Transport signature as in gcal.py.
- search(query, limit=50) -> [message id] (messages.list, q=query, follows nextPageToken until limit).
- get_message(id) -> {"id","internal_date": int ms,"from": str,"subject": str,"attachments":[{"filename","mime_type","attachment_id","size"}]}
  (messages.get format=full; walk payload.parts recursively; an attachment = part with a filename and body.attachmentId).
- get_attachment(message_id, attachment_id, max_bytes=5_000_000) -> bytes (base64url decode; GmailError if larger).
- 401 -> retry once with token_provider(force=True); 403 with insufficient scope text -> GmailError("Gmail access was not granted...").

## roster_sync.py (later wave): orchestration + report; app: Settings card with sender/name fields and "Sync roster" button.
Rules decided with the user: only emails whose sender matches the setting; only real PDF attachments (name ends .pdf or MIME type is application/pdf, AND bytes
start with %PDF); a PDF that is not a roster (NotARoster) is ignored; per work month the NEWEST email (internal_date) wins;
never apply a month whose work month is before the current month; a month is applied only when the parsed days differ from the
stored days (date, minutes, code, start, end); never go back to an older mail than the one applied before (table roster_import);
the month and year always come from the PDF title, never from the email date.


# Addendum 2026-11-24 (2): sync progress, per-month sync, mail cache
- sync(...) gets only_month=None and progress=None. progress(percent:int 0..100, text:str) is called: (2, "Searching your mail..."), then per mail i of n
  (10 + 70*(i-1)//n, "Reading mail i of n..."), (85, "Updating your days..."), and (100, "Done"). Percent never goes down.
- Cache table roster_mail (db.get_roster_mail / save_roster_mail): the outcome per Gmail message id for a given sender+name: kind skip (not from the sender / no PDF),
  ignored (PDF without roster title), roster (parsed result of the user's row, JSON). The PDF itself is NEVER stored (colleagues' names). Errors are never cached.
  A cached mail needs no get_message / get_attachment call. Only mails with 0 or exactly 1 PDF candidate are cached.
- only_month="YYYY-MM": query uses `after:YYYY/MM/DD` (first day of that month minus 90 days) instead of newer_than, max_mails at least 60; only that month is considered
  (others are ignored, not reported); the "month already over" rule does NOT apply (explicit request); all other rules do.
- Background job in app.py: POST /roster/sync with header X-Requested-With: fetch starts a thread and answers JSON at once; GET /roster/progress returns
  {"running","percent","text","done","ok","message","gcal","work_month"}; without the header the POST still runs synchronously and redirects (no-JS fallback).
