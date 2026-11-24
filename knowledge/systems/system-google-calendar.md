# System: Google Calendar sync
> One-way push of worked days to a dedicated "Work" calendar in the user's Google Calendar.

Back to [Systems](systems.md).

## What it does
Every calendar save (and "Sync all months now" in Settings) makes the app-created "Work" calendar match the
worked days of that work month: one event per day, titled `Work 7.5h` or `Holiday 7.5h`. Work days are timed
events at the work hours set in Settings (weekdays / Saturday-Sunday, default 07:30-15:30 / 08:30-16:30); holidays
are all-day. Each kind gets its own Google event color (`colorId`, default Tangerine / Sage).
The app is the source of truth; edits made in Google to these events are overwritten on the next sync.

## Files
- `src/gcal.py`: OAuth URLs and token exchange, `Client` (Calendar REST calls, access-token cache), `event_body(day, look)`, `plan_month` / `sync_month` (diff and apply), `COLORS`, `DEFAULT_LOOK`.
- `src/app.py`: `_gcal_creds` (app-stored credentials first, else env), `_gcal_sync` (never raises; stores `last_error`), routes `/google/connect` (POST), `/google/callback`, `/google/sync` (POST), `/google/disconnect` (POST), `/google/credentials` (POST), `/google/options` (POST, re-syncs all months when connected); `calendar_save` returns `gcal: null | "ok" | "error"`.
- `src/db.py`: table `google_sync` (one row: refresh_token, calendar_id, last_sync, last_error), table `google_config` (one row: client_id, client_secret, redirect_uri, work hours in minutes after midnight, work_color, holiday_color; not part of the pay snapshot), `worked_months`.
- `src/config.py`: optional fallback `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` (all or none); `validate_redirect_uri` (https, or http on localhost) shared with the Settings form.
- `src/templates/settings.html`: the Google Calendar card (connection, credentials form, "Calendar events" hours and colors form).
- `tests/test_gcal.py`: its tests (fake transport, no network).

## How it connects
- Uses: Google OAuth 2.0 auth-code flow and Calendar API v3 over `urllib` (stdlib only).
- Used by: the calendar page save, the income delete action, Settings.
- Data: events carry private extended properties `financeApp=1`, `financeDate=YYYY-MM-DD` and `financeSig` (hash of the body we send); only tagged events are listed or changed.

## Why it is built this way
- Scope `calendar.app.created`: the app can only see and change calendars it created itself, not the user's other calendars.
- Event ids are not stored: `save_month_days` replaces a month's rows, so the sync lists the tagged events and diffs instead (idempotent, heals missed syncs and duplicates).
- Work hours are one setting per weekday/weekend, not per day: the app stores minutes per day, not start times. A short day still shows the configured from-to; the title has the logged hours.
- The diff compares `financeSig`, not Google's echoed fields (Google rewrites `dateTime` with an offset, so a field compare would update every event every sync). Events without a sig are updated once.
- Updates use PUT (full replace), not PATCH: PATCH could leave `start.date` next to `start.dateTime` when an all-day event becomes timed.
- Credentials can be changed only while disconnected (the refresh token belongs to one client id); a new client id needs its secret entered again. The secret is never rendered back into the page.
- Explicit `GOOGLE_REDIRECT_URI`: the app runs behind an https proxy without ProxyFix, so `url_for(_external=True)` would give an http URL.

## Setup (once)
1. console.cloud.google.com: new project, enable **Google Calendar API**.
2. OAuth consent screen (Branding: home page `https://rookmelder.github.io/Finance-app/`, privacy policy `https://rookmelder.github.io/Finance-app/privacy.html`, authorized domain `rookmelder.github.io`; the pages are `docs/` in this repo, served by GitHub Pages): External, add yourself as user, scope `.../auth/calendar.app.created`, then **Publish app** (In production). In Testing mode refresh tokens expire after 7 days.
3. Credentials: OAuth client ID, type **Web application**, authorized redirect URI `https://<your domain>/google/callback`.
4. Settings → Google Calendar → paste client id and secret (the redirect URI is prefilled), Save credentials. (The three `GOOGLE_*` values in `.env` still work as a fallback.)
5. Connect Google Calendar (click past the "unverified app" warning).

## Known issues
- Changes made to the events in Google are overwritten; deleting the "Work" calendar in Google makes the next sync create a new one.
- Months entered "by totals" have no days, so nothing is pushed for them.
