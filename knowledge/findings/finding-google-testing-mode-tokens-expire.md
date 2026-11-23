# Finding: Google OAuth apps in Testing mode lose access after 7 days
> Publish the OAuth consent screen (In production) or the refresh token stops working weekly.

Back to [Findings](findings.md). Learned 2026-11-23 while working on [Google Calendar sync](../systems/system-google-calendar.md).

## What happened
Google expires refresh tokens of apps whose consent screen is in "Testing" after 7 days, so the sync would silently fail every week (it shows as "Google access was revoked or expired" in Settings).

## What works
Set the consent screen to "In production". An unverified personal app then only shows a warning screen once when connecting.

## What didn't
Leaving it in Testing and reconnecting weekly.
