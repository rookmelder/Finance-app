"""Login, session and CSRF protection for the finance app (Flask).

Every route requires a logged-in session except /login and static files.
Every POST requires a valid CSRF token (form field "csrf_token" or header "X-CSRF-Token").
"""
import hmac
import secrets
import time

from flask import abort, redirect, request, session, url_for

MAX_FAILS = 5          # failed logins allowed per client in the window
FAIL_WINDOW = 300      # seconds
_fails = {}            # client ip -> list of failure timestamps


def check_password(supplied, expected):
    """Constant-time password comparison."""
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def csrf_token():
    """Return the session's CSRF token, creating it when needed (use in templates)."""
    tok = session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["csrf"] = tok
    return tok


def _too_many_fails(ip, now):
    recent = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
    _fails[ip] = recent
    return len(recent) >= MAX_FAILS


def install(app, password):
    """Attach auth, CSRF and the /login, /logout routes to a Flask app."""
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14,
    )
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def _guard():
        if request.endpoint == "static":
            return None
        if request.method == "POST":
            sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
            real = session.get("csrf", "")
            if not real or not hmac.compare_digest(sent.encode(), real.encode()):
                abort(400, "Invalid or missing CSRF token")
        if request.endpoint in ("login", "logout"):
            return None
        if not session.get("auth"):
            return redirect(url_for("login"))
        return None

    @app.after_request
    def _headers(resp):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            ip = request.remote_addr or "?"
            now = time.time()
            if _too_many_fails(ip, now):
                error = "Too many attempts. Wait a few minutes."
            elif check_password(request.form.get("password", ""), password):
                session.clear()
                session["auth"] = True
                session.permanent = True
                csrf_token()
                return redirect(url_for("overview"))
            else:
                _fails.setdefault(ip, []).append(now)
                error = "Wrong password."
        csrf_token()
        return _login_page(error)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))


def _login_page(error):
    from flask import render_template
    return render_template("login.html", error=error)
