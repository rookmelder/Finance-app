# System: Google Calendar sync
> One-way push of worked days to a dedicated "Work" calendar in the user's Google Calendar.

Back to [Systems](systems.md).

## What it does
Every calendar save (and "Sync all months now" in Settings) makes the app-created "Work" calendar match the
worked days of that work month: one all-day event per day, titled `Work 7.5h` or `Holiday 7.5h`.
The app is the source of truth; edits made in Google to these events are overwritten on the next sync.

## Files
- `src/gcal.py`: OAuth URLs and token exchange, `Client` (Calendar REST calls, access-token cache), `plan_month` / `sync_month` (diff and apply).
- `src/app.py`: `_gcal_sync` (never raises; stores `last_error`), routes `/google/connect` (POST), `/google/callback`, `/google/sync` (POST), `/google/disconnect` (POST); `calendar_save` returns `gcal: null | "ok" | "error"`.
- `src/db.py`: table `google_sync` (one row: refresh_token, calendar_id, last_sync, last_error), `worked_months`.
- `src/config.py`: optional `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` (all or none; https only).
- `src/templates/settings.html`: the Google Calendar card.
- `tests/test_gcal.py`: its tests (fake transport, no network).

## How it connects
- Uses: Google OAuth 2.0 auth-code flow and Calendar API v3 over `urllib` (stdlib only).
- Used by: the calendar page save, the income delete action, Settings.
- Data: events carry private extended properties `financeApp=1` and `financeDate=YYYY-MM-DD`; only tagged events are listed or changed.

## Why it is built this way
- Scope `calendar.app.created`: the app can only see and change calendars it created itself, not the user's other calendars.
- Event ids are not stored: `save_month_days` replaces a month's rows, so the sync lists the tagged events and diffs instead (idempotent, heals missed syncs and duplicates).
- All-day events because the app stores minutes per day, not start times.
- Explicit `GOOGLE_REDIRECT_URI`: the app runs behind an https proxy without ProxyFix, so `url_for(_external=True)` would give an http URL.

## Setup (once)
1. console.cloud.google.com: new project, enable **Google Calendar API**.
2. OAuth consent screen: External, add yourself as user, scope `.../auth/calendar.app.created`, then **Publish app** (In production). In Testing mode refresh tokens expire after 7 days.
3. Credentials: OAuth client ID, type **Web application**, authorized redirect URI `https://<your domain>/google/callback`.
4. Put the three `GOOGLE_*` values in `.env`, then `docker compose up -d --build`.
5. Settings → Connect Google Calendar (click past the "unverified app" warning).

## Known issues
- Changes made to the events in Google are overwritten; deleting the "Work" calendar in Google makes the next sync create a new one.
- Months entered "by totals" have no days, so nothing is pushed for them.
