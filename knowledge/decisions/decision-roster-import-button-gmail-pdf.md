# Decision: roster import by button from Gmail PDFs
> Read the roster PDF through the read-only Gmail API when the user presses a button; apply it per month with safety checks.

Back to [Decisions](decisions.md).

## Decision
Read the boss's roster PDF with the Gmail API (`gmail.readonly`, same Google connection as the calendar) when the user presses "Sync roster now"; parse it with pypdf positions; apply it per month; show shifts as "Work V" events.

## Why
- The roster comes as a PDF (not xlsx), once a month, with revisions; a button is enough and needs no background job or public webhook.
- Gmail API over IMAP: no mailbox password stored; one OAuth connection; an unverified published app is fine for a single user.
- pypdf is the one allowed dependency beyond Flask (pure Python, only in `src/roster_pdf.py`), because a PDF cannot be read with the standard library.
- Safety over convenience: month from the title, a total check, exact sender match, finished months untouched, never going back to an older mail.

## Alternatives not taken
- Gmail push notifications (needs a public HTTPS endpoint and a 7-day watch renewal).
- IMAP with an app password (full mailbox access stored in the app).
- Polling every few minutes (no benefit for a monthly mail).

See [Roster import](../systems/system-roster-import.md).
