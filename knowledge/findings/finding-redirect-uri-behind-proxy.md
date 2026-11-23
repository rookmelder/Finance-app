# Finding: Use an explicit OAuth redirect URI behind the https proxy
> url_for(_external=True) builds http:// URLs here, so GOOGLE_REDIRECT_URI is set explicitly in .env.

Back to [Findings](findings.md). Learned 2026-11-23 while working on [Google Calendar sync](../systems/system-google-calendar.md).

## What happened
The app runs in Docker behind an https reverse proxy without ProxyFix, so Flask sees plain http and an external URL would not match the redirect URI registered at Google.

## What works
Configure `GOOGLE_REDIRECT_URI` (https only, checked in `src/config.py`) and use it for both the consent URL and the token exchange.

## What didn't
Building the redirect URI from the request.
