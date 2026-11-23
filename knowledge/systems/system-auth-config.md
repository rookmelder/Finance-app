# System: Auth and Config
> Authentication, session security, CSRF protection, and runtime configuration.

Back to [Systems](systems.md).

## What it does
Loads and validates runtime configuration from environment variables at startup.
Guards Flask application endpoints with mandatory session authentication.
Protects state-changing requests with CSRF tokens and sets security response headers.
Handles user login, rate limiting for failed password attempts, and logout.

## Files
- `src/auth.py`: session checks, CSRF validation, rate limiting, and `/login` and `/logout` routes.
- `src/config.py`: environment variable loader `load` and `ConfigError` exception.
- `tests/test_auth.py`: tests for login, lockout, CSRF enforcement, security headers, and configuration loading.

## How it connects
- Uses: none
- Used by: [system-web-app.md](system-web-app.md), [system-google-calendar.md](system-google-calendar.md)
- Data: reads environment variables `FINANCE_PASSWORD`, `SECRET_KEY`, `FINANCE_PORT`, `FINANCE_DB`, `FINANCE_HOST`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REDIRECT_URI`. Reads and writes Flask session keys `auth` and `csrf`. Validates CSRF tokens from form field `csrf_token` or header `X-CSRF-Token`.

## Why it is built this way
- `config.load` validates settings on startup and raises `ConfigError` if `FINANCE_PASSWORD` is missing, `SECRET_KEY` is under 32 characters, `FINANCE_PORT` is not 1-65535, or Google OAuth keys are incomplete.
- `GOOGLE_REDIRECT_URI` must use `https://` unless pointing to `localhost` or `127.0.0.1`.
- `auth.install` registers a `before_request` hook that redirects unauthenticated requests to `/login`, skipping only `static` and the `/login` and `/logout` endpoints.
- Every POST request requires a CSRF token verified with `hmac.compare_digest`. This includes `/login` and `/logout`.
- `/logout` requires POST to prevent logout via cross-site GET requests.
- `check_password` uses `hmac.compare_digest` to prevent timing attacks.
- Failed logins track timestamps in memory per client IP in `_fails`; reaching 5 attempts (`MAX_FAILS`) within 300 seconds (`FAIL_WINDOW`) blocks further attempts.
- Sets strict response headers (`Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`) and session cookie flags (`HttpOnly`, `SameSite=Lax`, 14-day lifetime).

## Known issues
- In-memory `_fails` rate-limit dictionary resets when the application restarts and is not shared across multi-process workers.
