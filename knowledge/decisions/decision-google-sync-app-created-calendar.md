# Decision: Google sync to an app-created calendar with a narrow scope
> Worked days go to a "Work" calendar the app creates, with scope calendar.app.created and a month diff instead of stored event ids.

Back to [Decisions](decisions.md). Decided 2026-11-23.

## Options
- ICS feed link: no Google setup, but Google refreshes it only every few hours and the server must be public.
- Calendar API on the primary calendar (scope calendar or calendar.events): instant, but access to all calendars.
- Calendar API on an app-created calendar (calendar.app.created): instant, only sees its own calendar.

## Chosen and why
The API with `calendar.app.created`, standard library only (no Google client libraries, see PROJECT.md). Events are tagged with private extended properties and each sync lists and diffs the month, because `save_month_days` replaces rows and stored event ids would be lost. See [Google Calendar sync](../systems/system-google-calendar.md).

## Revisit when
if timed events (start times) or two-way sync are wanted
