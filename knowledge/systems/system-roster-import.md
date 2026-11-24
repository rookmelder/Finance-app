# System: Roster import from Gmail
> A "Sync roster now" button reads the boss's roster PDF mails, finds the user's row, and updates the worked days with shift codes and real times.

Back to [Systems](systems.md).

## What it does
The boss emails a one-page PDF roster ("Rooster Technische Dienst") for the whole department. Pressing **Sync roster now** (Settings, card "Roster from email") searches Gmail for PDFs from the saved sender, reads each PDF, picks the user's row (setting "Your name in the roster"), keeps the newest mail per work month, and replaces that month's worked days when they differ. Each imported day carries a shift code (V, D, L, ...), the shift's start/end time and the paid minutes. Google Calendar is then synced for the changed months (events read "Work V", at the shift's times). There is no background polling: the button is the only trigger.

## Files
- `src/roster_pdf.py`: `extract_tokens(pdf_bytes)` turns page 1 of a PDF into `(x, y, text)` tokens with pypdf (the only module that imports pypdf); `PdfError`.
- `src/roster.py`: `parse_roster(tokens, name)` finds title month/year, the day grid, the legend (code to times and hours) and the person's row; returns month, days, legend, total and revision date; `RosterError`, `NotARoster`.
- `src/gmail.py`: read-only Gmail REST client over `urllib` (`search`, `get_message`, `get_attachment`); `GmailError`.
- `src/roster_sync.py`: `sync(conn, gmail, today, ...)` orchestration and the report dict.
- `src/app.py`: routes `/roster/settings` (sender, name) and `/roster/sync`, `_gmail_client`, `_roster_view`; `src/templates/settings.html`: the roster card.
- `src/db.py`: tables `roster_import` and `roster_state`, columns `google_config.roster_sender` / `roster_name`, `google_sync.scopes`.
- `tests/test_roster.py`, `tests/test_gmail.py`, `tests/test_roster_sync.py`, `tests/test_roster_routes.py`; fixture `tests/fixtures/roster_tokens.json` (anonymized tokens of a real roster, the user's row is "Test Person").

## How it connects
- Uses: [Google Calendar sync](system-google-calendar.md) (same refresh token; `gcal.Client.access_token`; `_gcal_sync` for changed months), [Database](system-database.md) (`save_month_days`, `list_days`, shifts, import state).
- Used by: the Settings page. Connecting Google asks for both scopes `calendar.app.created` and `gmail.readonly`; `google_sync.scopes` records what was granted.
- Data: sender and name in `google_config`; the last report (JSON) in `roster_state`; per work month the mail applied last in `roster_import`.

## Rules
- Only mails whose From address equals the saved sender (exact, case-insensitive) and only real PDFs (name ends .pdf or MIME pdf, and the bytes start with `%PDF`). A PDF without a roster title is ignored and counted.
- Month and year come from the PDF title, never from the mail date. The grid is read by x position: only columns for day 1..N of that month count, so a stray shift in the next month's first column (a real case: a "d" on 1 January in a December roster) is dropped.
- The parse is rejected when the day-1 weekday does not match, the name is not found exactly once, a worked code is not in the legend, or the summed hours differ from the total printed in the sheet.
- Pay uses the cell's hours (7.5 h = 450 min); the shift times (8:30-16:30) include the unpaid half hour break.
- Per month the newest mail wins; months before the current month are never touched; a month is not overwritten by an older mail than the one applied before; if a newer roster mail could not be read the months older than it are left unchanged.
- Manual holiday days on dates the roster does not use are kept; every other stored day of the month is replaced by the roster.
- Unknown legend shifts are added to the shifts table (never overwriting an edited one) so they can be picked on the calendar page.

## Why it is built this way
- A button, not a poller: the roster arrives about once a month and the app has no background jobs; polling would need a thread and a 7-day watch renewal.
- Tokens, not text: positions from pypdf are needed to assign cells to days; the layout text mode is not reliable for that. Parsing works on plain token lists so tests need no PDF and no pypdf, and no real names are in the repo.
- Total check: the printed per-person total is an independent check that catches a wrongly read row or column.
- See [Roster import decision](../decisions/decision-roster-import-button-gmail-pdf.md).

## Known issues
- A real PDF cannot be built in tests; `roster_pdf.extract_tokens` was only checked by hand against a real roster.
- If the roster layout changes (different grid or legend), the parse is rejected and the report shows the error; nothing is changed.
- Gmail reading needs the app to be published in Google Cloud (unverified is fine for one user) and the Gmail API enabled.
- An existing calendar-only connection gets Gmail through the "Allow Gmail access" button (a normal reconnect, no disconnect): the stored `calendar_id` is kept, so no second "Work" calendar is made (tested in `tests/test_roster_routes.py`).
