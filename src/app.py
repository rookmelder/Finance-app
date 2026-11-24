"""Flask web application for Finance app."""

import calendar
import datetime
import hmac
import json
import os
import re
import secrets
import sys
import threading
from flask import (
    Flask,
    abort,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from markupsafe import Markup
from src import auth, balance, balance_chart, calc, config, db, gcal, gmail, roster_sync


def get_db():
    """Return request-scoped database connection, creating it if needed."""
    if "db" not in g:
        db_path = current_app.config["DB_PATH"]
        if db_path != ":memory:":
            dirname = os.path.dirname(os.path.abspath(db_path))
            if dirname:
                os.makedirs(dirname, exist_ok=True)
        g.db = db.connect(db_path)
    return g.db


def _estimate(conn, row) -> dict:
    """Pay breakdown for an income row: from its calendar days when per_day, else from its totals.

    Always uses the settings snapshot stored on the row. One function for every screen so the
    list, the overview and the carry-over chain can never disagree.
    """
    if row["per_day"]:
        return calc.estimate_from_days(
            db.list_days(conn, row["work_month"]),
            rate_cents=row["rate_cents"],
            allowance_cents=row["allowance_cents"],
            clothing_cents=row["clothing_cents"],
            sunday_cents_per_hour=row["sunday_cents_per_hour"],
            sunday_cap_minutes=row["sunday_cap_minutes"],
            rsz_bp=row["rsz_bp"],
            other_cents=row["other_cents"],
        )
    return calc.estimate_breakdown(
        rate_cents=row["rate_cents"],
        minutes_per_day=row["minutes_per_day"],
        days=row["days"],
        allowance_cents=row["allowance_cents"],
        other_cents=row["other_cents"],
        minutes_override=row["minutes_override"],
        clothing_cents=row["clothing_cents"],
        sundays=row["sundays"],
        holiday_minutes=row["holiday_minutes"],
        sunday_premium_cents=row["sunday_premium_cents"],
        rsz_bp=row["rsz_bp"],
    )


def _bank_income_amount(conn, row):
    return row["actual_cents"] if row["actual_cents"] is not None else _estimate(conn, row)["total"]


def _signed_money(cents):
    return ("-" if cents < 0 else "+") + calc.format_money(abs(cents))


def _bank_state(conn):
    today = datetime.date.today()
    iso = today.isoformat()
    fn = lambda row: _bank_income_amount(conn, row)
    try:
        balance.settle(conn, iso, fn)
    except ValueError:
        pass  # Another request may have posted the same event.
    amount = balance.current(conn, iso)
    if amount is None:
        return {"set": False}
    latest = next(e for e in reversed(db.list_balance_events(conn))
                  if e["kind"] == "set" and e["date"] <= iso)
    points = [{"date": p["date"], "amount": p["balance_cents"],
               "delta": p["delta_cents"], "kind": p["kind"], "label": p["label"]}
              for p in balance.history(conn, iso)]
    upcoming = balance.upcoming(conn, iso, balance.last_day_of_month(iso[:7]), fn)
    return {"set": True, "amount": amount, "as_of": latest["date"],
            "change": amount - latest["amount_cents"], "upcoming": upcoming,
            "costs_due": sum(-e["amount_cents"] for e in upcoming["events"]
                             if e["kind"] == "recurring"),
            "pay_due": sum(e["amount_cents"] for e in upcoming["events"] if e["kind"] == "pay"),
            "expected_end": amount + upcoming["total_cents"], "points": points,
            "chart": Markup(balance_chart.chart_svg(points, today))}


BANK_MESSAGES = {
    "saved": ("ok", "Bank balance saved."),
    "payday_saved": ("ok", "Pay day saved."),
    "bad_amount": ("error", "Enter a valid balance and a note of at most 80 characters."),
    "bad_payday": ("error", "Enter a pay day from 1 to 28."),
}


GOOGLE_MESSAGES = {
    "connected": ("ok", "Google Calendar connected. Your worked days are being added to the \"Work\" calendar."),
    "connected_error": ("error", "Connected, but the first sync failed. See the error below and try Sync now."),
    "denied": ("error", "Google access was not given."),
    "badstate": ("error", "That Google sign-in link was old or not started here. Try Connect again."),
    "failed": ("error", "Google sign-in failed. Check the client id, secret and redirect URI in the Google Calendar card, then try again."),
    "synced": ("ok", "All months synced to Google Calendar."),
    "syncfailed": ("error", "Sync failed. See the error below."),
    "disconnected": ("ok", "Google Calendar disconnected. Events already in the \"Work\" calendar stay there."),
    "disconnect_first": ("error", "Disconnect Google Calendar first, then change the credentials."),
    "creds_saved": ("ok", "Google credentials saved. You can connect now."),
    "creds_cleared": ("ok", "Google credentials removed."),
    "options_saved": ("ok", "Calendar event settings saved."),
    "options_synced": ("ok", "Calendar event settings saved and all months updated in Google Calendar."),
    "options_syncfailed": ("error", "Settings saved, but updating Google Calendar failed. See the error below."),
    "roster_saved": ("ok", "Roster settings saved."),
    "roster_bad_sender": ("error", "Enter a plain email address for the sender, like name@example.com."),
    "roster_bad_name": ("error", "Enter your name exactly as it appears in the roster (max 80 characters)."),
}

SENDER_RE = re.compile(r"^[^@\s\"'()<>{}\\,;:]{1,64}@[^@\s\"'()<>{}\\,;:]{1,190}\.[^@\s\"'()<>{}\\,;:]{2,}$")


def _gcal_creds(conn) -> tuple | None:
    cfg = db.get_google_config(conn)
    if cfg["client_id"] and cfg["client_secret"] and cfg["redirect_uri"]:
        return (cfg["client_id"], cfg["client_secret"], cfg["redirect_uri"])
    app_cfg = current_app.config
    cid = app_cfg.get("GOOGLE_CLIENT_ID")
    sec = app_cfg.get("GOOGLE_CLIENT_SECRET")
    red = app_cfg.get("GOOGLE_REDIRECT_URI")
    if cid and sec and red:
        return (cid, sec, red)
    return None


def _stored_id_with_secret(conn) -> str:
    """The client id saved in the app, or "" when no secret is saved with it."""
    cfg = db.get_google_config(conn)
    return cfg["client_id"] if cfg["client_secret"] else ""


def _gcal_configured(conn) -> bool:
    return _gcal_creds(conn) is not None


def _format_hhmm(minutes: int) -> str:
    if minutes == 1440:
        return "24:00"
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _parse_hhmm(text: str, label: str) -> int:
    s = (text or "").strip()
    parts = s.split(":")
    if len(parts) == 2:
        h_str, m_str = parts[0], parts[1]
        if len(h_str) in (1, 2) and h_str.isdigit() and len(m_str) == 2 and m_str.isdigit():
            h = int(h_str)
            m = int(m_str)
            if 0 <= h <= 23 and 0 <= m <= 59:
                return h * 60 + m
    raise ValueError(f"{label}: enter a time like 07:30")


HEX_COLORS = {
    "1": "#7986cb",
    "2": "#33b679",
    "3": "#8e24aa",
    "4": "#e67c73",
    "5": "#f6bf26",
    "6": "#f4511e",
    "7": "#039be5",
    "8": "#616161",
    "9": "#3f51b5",
    "10": "#0b8043",
    "11": "#d50000",
}


def _gcal_transport():
    return current_app.config.get("GCAL_TRANSPORT") or gcal.urllib_transport


def _gcal_sync(conn, months):
    """Push the worked days of these work months to Google. Returns None (not connected), "ok" or "error".

    Never raises for Google problems: the error is stored and shown in Settings.
    """
    creds = _gcal_creds(conn)
    if creds is None:
        return None
    client_id, client_secret, redirect_uri = creds
    look = db.get_google_config(conn)
    with gcal.sync_lock:
        row = db.get_google(conn)  # read inside the lock: another save may have just created the calendar
        if row is None:
            return None
        client = gcal.Client(client_id, client_secret, row["refresh_token"],
                             _gcal_transport())
        try:
            cal_id = row["calendar_id"]
            if not cal_id:
                cal_id = client.create_calendar()
                db.update_google(conn, calendar_id=cal_id)
            todo = list(dict.fromkeys(months))
            recreated = False
            i = 0
            while i < len(todo):
                try:
                    gcal.sync_month(client, cal_id, todo[i], db.list_days(conn, todo[i]), look=look)
                    i += 1
                except gcal.GcalError as err:
                    if err.status != 404 or recreated:
                        raise
                    # The "Work" calendar was deleted in Google: make a new one and push every month again.
                    cal_id = client.create_calendar()
                    db.update_google(conn, calendar_id=cal_id)
                    recreated = True
                    todo = list(dict.fromkeys(db.worked_months(conn) + todo))
                    i = 0
            db.update_google(conn, last_sync=datetime.datetime.now().isoformat(timespec="seconds"), last_error=None)
            return "ok"
        except gcal.GcalError as err:
            db.update_google(conn, last_error=str(err)[:300])
            return "error"
        except Exception as err:  # never let a Google problem break saving
            current_app.logger.warning("Google sync failed: %s", type(err).__name__)
            db.update_google(conn, last_error="Unexpected error talking to Google (" + type(err).__name__ + ")")
            return "error"


def _roster_view(conn):
    cfg = db.get_google_config(conn)
    state = db.get_roster_state(conn)
    try:
        report = json.loads(state["report"]) if state["report"] else None
    except ValueError:
        report = None
    return {"sender": cfg["roster_sender"], "name": cfg["roster_name"],
            "synced_at": state["synced_at"], "report": report if isinstance(report, dict) else None}


def _gmail_client(conn, row):
    """A Gmail client that uses the stored Google refresh token (the same one as the calendar)."""
    client_id, client_secret, _ = _gcal_creds(conn)
    google = gcal.Client(client_id, client_secret, row["refresh_token"], _gcal_transport())
    return gmail.Client(lambda force=False: google.access_token(force),
                        current_app.config.get("GMAIL_TRANSPORT") or gmail.urllib_transport)


def _google_view(conn):
    msg = GOOGLE_MESSAGES.get(request.args.get("google", ""))
    cfg = db.get_google_config(conn)
    app_cfg = current_app.config
    db_complete = bool(cfg["client_id"] and cfg["client_secret"] and cfg["redirect_uri"])
    env_complete = bool(
        app_cfg.get("GOOGLE_CLIENT_ID")
        and app_cfg.get("GOOGLE_CLIENT_SECRET")
        and app_cfg.get("GOOGLE_REDIRECT_URI")
    )
    if db_complete:
        source = "app"
    elif env_complete:
        source = "env"
    else:
        source = None

    row = db.get_google(conn)
    client_id = cfg["client_id"] if source != "env" else app_cfg.get("GOOGLE_CLIENT_ID", "")
    # Google allows plain http only for localhost; everything else must be https
    local = request.host.split(":")[0] in ("localhost", "127.0.0.1")
    suggested = ("http://" if local else "https://") + request.host + "/google/callback"

    return {
        "message": msg,
        "configured": _gcal_configured(conn),
        "connected": row is not None,
        "source": source,
        "client_id": client_id,
        "has_secret": bool(cfg["client_secret"]),
        "redirect_uri": cfg.get("redirect_uri") or "",
        "suggested_redirect": suggested,
        "last_sync": row["last_sync"] if row else None,
        "last_error": row["last_error"] if row else None,
        "look": {
            "weekday_from": _format_hhmm(cfg["weekday_start"]),
            "weekday_to": _format_hhmm(cfg["weekday_end"]),
            "weekend_from": _format_hhmm(cfg["weekend_start"]),
            "weekend_to": _format_hhmm(cfg["weekend_end"]),
            "work_color": cfg["work_color"],
            "holiday_color": cfg["holiday_color"],
        },
        "colors": [{"id": cid, "name": name, "hex": HEX_COLORS.get(cid, "")} for cid, name in gcal.COLORS],
        "gmail_ok": bool(row and gcal.SCOPE_GMAIL in (row.get("scopes") or "").split()),
        "roster": _roster_view(conn),
    }


def _breakdown_rows(b: dict) -> list:
    """The breakdown as display rows [{"label", "value", "kind"}] (kind: normal | neg | total)."""
    rows = [{"label": f"Hours ({calc.format_hours(b['minutes'])} h)", "value": calc.format_money(b["hours_pay"]), "kind": "normal"}]
    if b["holiday_extra"]:
        rows.append({"label": "Holiday extra", "value": calc.format_money(b["holiday_extra"]), "kind": "normal"})
    if b["sunday_pay"]:
        rows.append({"label": "Sunday premium", "value": calc.format_money(b["sunday_pay"]), "kind": "normal"})
    rows.append({"label": "Gross", "value": calc.format_money(b["gross"]), "kind": "normal"})
    rows.append({"label": "RSZ", "value": "-" + calc.format_money(b["rsz"]), "kind": "neg"})
    rows.append({"label": "Net", "value": calc.format_money(b["net"]), "kind": "normal"})
    rows.append({"label": "Fiets + kledij", "value": calc.format_money(b["allowances"]), "kind": "normal"})
    if b["other"]:
        rows.append({"label": "Other", "value": calc.format_money(b["other"]), "kind": "normal"})
    rows.append({"label": "Estimated total", "value": calc.format_money(b["total"]), "kind": "total"})
    return rows


def _parse_extras(form) -> tuple:
    """(sundays, holiday_minutes) from the income forms."""
    sundays_str = form.get("sundays", "").strip() or "0"
    try:
        sundays = int(sundays_str)
    except ValueError:
        raise ValueError(f"Sundays must be a whole number, got {sundays_str!r}")
    holiday_str = form.get("holiday_hours", "").strip()
    holiday_minutes = calc.parse_hours(holiday_str) if holiday_str else 0
    return sundays, holiday_minutes


def _month_nav(month_str: str) -> tuple[str, str]:
    """Return (prev_month, next_month) as YYYY-MM strings."""
    year = int(month_str[:4])
    month = int(month_str[5:7])
    if month == 1:
        prev_m = f"{year - 1:04d}-12"
    else:
        prev_m = f"{year:04d}-{month - 1:02d}"

    if month == 12:
        next_m = f"{year + 1:04d}-01"
    else:
        next_m = f"{year:04d}-{month + 1:02d}"
    return prev_m, next_m


def _render_income(conn, error=None, status_code=200):
    by_work_month = {r["work_month"]: r for r in db.list_income(conn)}
    default_wm = calc.default_work_month(datetime.date.today())
    first = min([calc.add_months(default_wm, -2), *by_work_month])
    months = []
    today_iso = datetime.date.today().isoformat()
    holidays_by_year = {}
    for wm in calc.month_range(first, calc.add_months(default_wm, 6)):
        row = by_work_month.get(wm)
        year, mon = int(wm[:4]), int(wm[5:7])
        if year not in holidays_by_year:
            holidays_by_year[year] = calc.belgian_holidays(year)
        worked = {d["date"]: d for d in db.list_days(conn, wm)} if row and row["per_day"] else {}
        weeks = []
        for week in calendar.Calendar(firstweekday=0).monthdayscalendar(year, mon):
            cells = []
            for i, day in enumerate(week):
                if day == 0:
                    cells.append(None)
                    continue
                iso = f"{wm}-{day:02d}"
                entry = worked.get(iso)
                cells.append({
                    "day": day,
                    "date": iso,
                    "worked": entry is not None,
                    "paid_holiday": bool(entry and entry["holiday"]),
                    "public_holiday": holidays_by_year[year].get(iso),
                    "today": iso == today_iso,
                    "sunday": i == 6,
                })
            weeks.append(cells)
        months.append({
            "year": year,
            "weeks": weeks,
            "work_month": wm,
            "pay_month": calc.pay_month(wm),
            "label": f"{calendar.month_name[mon]} {year}",
            "is_default": wm == default_wm,
            "has_income": row is not None,
            "per_day": bool(row and row["per_day"]),
            "days": row["days"] if row else None,
            "estimate": _estimate(conn, row)["total"] if row else None,
            "actual": row["actual_cents"] if row else None,
            "used": ("actual" if row["actual_cents"] is not None else "estimate") if row else None,
        })
    return (
        render_template(
            "income.html",
            months=months,
            default_wm=default_wm,
            default_pm=calc.pay_month(default_wm),
            error=error,
            roster_ready=_roster_ready(conn),
        ),
        status_code,
    )


def _roster_ready(conn) -> bool:
    """True when the roster sync can run: Google connected, Gmail granted, sender and name set."""
    row = db.get_google(conn)
    if row is None or _gcal_creds(conn) is None:
        return False
    if gcal.SCOPE_GMAIL not in (row.get("scopes") or "").split():
        return False
    cfg = db.get_google_config(conn)
    return bool(cfg["roster_sender"].strip() and cfg["roster_name"].strip())


def _do_roster_sync(conn, only_month=None, progress=None):
    """Run one roster sync plus the Google Calendar update. Returns the report dict (never raises)."""
    empty = {"months": [], "ignored": 0, "errors": [], "changed_months": []}
    row = db.get_google(conn)
    if row is None or _gcal_creds(conn) is None:
        return {**empty, "ok": False, "message": "Connect Google first (Google Calendar card)."}
    if gcal.SCOPE_GMAIL not in (row.get("scopes") or "").split():
        return {**empty, "ok": False,
                "message": "Gmail access was not granted yet. Use the \"Allow Gmail access\" button in the Roster card and tick the Gmail box."}

    def sync_progress(percent, text):
        if progress:  # the mail part fills 0-90 %, the calendar update the rest
            progress(min(90, percent * 9 // 10), text)

    try:
        kwargs = {"progress": sync_progress}
        if only_month:
            kwargs["only_month"] = only_month
        report = roster_sync.sync(conn, _gmail_client(conn, row), datetime.date.today(), **kwargs)
    except gcal.GcalError as err:
        return {**empty, "ok": False, "message": str(err)}
    except Exception as err:  # never show a stack trace for a mail problem
        current_app.logger.warning("Roster sync failed: %s", type(err).__name__)
        return {**empty, "ok": False,
                "message": "Unexpected error while reading your mail (" + type(err).__name__ + ")."}
    if report.get("changed_months"):
        if progress:
            progress(93, "Updating Google Calendar...")
        report["gcal"] = _gcal_sync(conn, report["changed_months"])
    return report


_roster_job = {"lock": threading.Lock(), "running": False, "percent": 0, "text": "", "done": False,
               "ok": None, "message": "", "gcal": None, "work_month": None}


def _job_update(**fields):
    with _roster_job["lock"]:
        _roster_job.update(fields)


def _roster_worker(app, only_month):
    """Background thread: own DB connection and app context; stores the report like the plain form does."""
    with app.app_context():
        conn = db.connect(app.config["DB_PATH"])
        try:
            report = _do_roster_sync(conn, only_month, progress=lambda p, t: _job_update(percent=p, text=t))
            db.set_roster_state(conn, datetime.datetime.now().isoformat(timespec="seconds"), json.dumps(report))
        except Exception as err:  # last resort: the job must always end
            app.logger.warning("Roster job failed: %s", type(err).__name__)
            report = {"ok": False, "message": "Unexpected error (" + type(err).__name__ + ").", "gcal": None}
        finally:
            conn.close()
    _job_update(running=False, done=True, percent=100, text="Done", ok=bool(report.get("ok")),
                message=str(report.get("message", "")), gcal=report.get("gcal"))


def _render_costs(conn, error=None, status_code=200):
    raw_costs = db.list_recurring(conn)
    costs = []
    total_monthly = 0
    for r in raw_costs:
        item = dict(r)
        set_aside = calc.monthly_set_aside(r["amount_cents"], r["every_months"])
        item["monthly_set_aside"] = set_aside
        costs.append(item)
        if r["active"]:
            total_monthly += set_aside
    total_yearly = total_monthly * 12
    return (
        render_template(
            "costs.html",
            costs=costs,
            total_monthly=total_monthly,
            total_yearly=total_yearly,
            error=error,
        ),
        status_code,
    )


def _render_expenses(conn, month=None, error=None, status_code=200):
    try:
        expenses = db.list_expenses(conn, month=month)
    except ValueError:
        expenses = []
    today_iso = datetime.date.today().isoformat()
    return (
        render_template(
            "expenses.html",
            expenses=expenses,
            month=month,
            today=today_iso,
            error=error,
        ),
        status_code,
    )


def _month_label(month: str) -> str:
    """'2026-11' -> 'November 2026' (display only); anything else is returned unchanged."""
    try:
        year, mon = int(month[:4]), int(month[5:7])
        return f"{calendar.month_name[mon]} {year}"
    except (TypeError, ValueError, IndexError):
        return month


def _pct(part, whole) -> int:
    """part/whole as a whole percentage clamped to 0-100, for progress bars (0 when whole <= 0)."""
    if not whole or whole <= 0 or not part or part <= 0:
        return 0
    return max(0, min(100, round(part * 100 / whole)))


def create_app(cfg: dict) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.config.update(cfg)
    app.secret_key = cfg["SECRET_KEY"]

    # Jinja filters
    app.jinja_env.filters["money"] = calc.format_money
    app.jinja_env.filters["signed_money"] = _signed_money
    app.jinja_env.filters["hours"] = calc.format_hours
    app.jinja_env.filters["hhmm"] = _format_hhmm
    app.jinja_env.filters["percent"] = calc.format_percent_bp
    app.jinja_env.filters["month_label"] = _month_label
    app.jinja_env.globals["pct"] = _pct

    # Request database teardown
    @app.teardown_appcontext
    def close_db(exception=None):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    # Install authentication and CSRF guard
    auth.install(app, cfg["PASSWORD"])

    @app.context_processor
    def bank_settings_context():
        if request.endpoint in {"settings", "shifts_save", "shifts_delete",
                                "google_credentials", "google_options"}:
            return {"bank_config": db.get_balance_config(get_db()),
                    "bank_message": BANK_MESSAGES.get(request.args.get("bank", ""))}
        return {}

    @app.route("/balance", methods=["GET"])
    def balance_history():
        conn = get_db()
        bank = _bank_state(conn)
        events = balance.history(conn, datetime.date.today().isoformat())
        return render_template("balance.html", bank=bank, events=list(reversed(events)))

    @app.route("/balance/set", methods=["POST"])
    def balance_set():
        text = request.form.get("amount", "").strip()
        note = request.form.get("note", "").strip()
        # Strip display decoration, then let the shared parser handle the digits.
        text = "".join(text.split())
        if text.startswith("€"):
            text = text[1:]
        elif text.startswith("-€"):
            text = "-" + text[2:]
        try:
            if not re.fullmatch(r"-?[0-9]+(?:[.,][0-9]{1,2})?", text) or len(note) > 80:
                raise ValueError("Invalid bank balance")
            negative = text.startswith("-")
            amount = calc.parse_money(text[1:] if negative else text)
            if negative:
                amount = -amount
        except ValueError:
            return redirect(url_for("settings", bank="bad_amount") + "#bank", code=303)
        conn = get_db()
        balance.set_balance(conn, amount, datetime.date.today().isoformat(),
                            lambda row: _bank_income_amount(conn, row), note=note)
        return redirect(url_for("settings", bank="saved") + "#bank", code=303)

    @app.route("/balance/payday", methods=["POST"])
    def balance_payday():
        try:
            pay_day = int(request.form.get("pay_day", ""))
            if not 1 <= pay_day <= 28:
                raise ValueError("Invalid pay day")
        except ValueError:
            return redirect(url_for("settings", bank="bad_payday") + "#bank", code=303)
        db.update_balance_config(get_db(), pay_day)
        return redirect(url_for("settings", bank="payday_saved") + "#bank", code=303)

    # 1. Overview
    @app.route("/", methods=["GET"])
    def overview():
        conn = get_db()
        month_arg = request.args.get("month", "").strip()
        if not month_arg:
            month_str = datetime.date.today().strftime("%Y-%m")
        else:
            try:
                calc.pay_month(month_arg)  # validates YYYY-MM
                month_str = month_arg
            except ValueError:
                return redirect(url_for("overview"), code=303)

        prev_month, next_month = _month_nav(month_str)

        # Income for selected pay month
        income_row = db.get_income(conn, month_str)
        if income_row is not None:
            breakdown = _estimate(conn, income_row)
            estimate = breakdown["total"]
            if income_row["actual_cents"] is not None:
                income_amount = income_row["actual_cents"]
                income_used = "actual"
            else:
                income_amount = estimate
                income_used = "estimate"
            needs, free, savings = calc.split_income(
                income_amount,
                income_row["needs_pct"],
                income_row["free_pct"],
                income_row["save_pct"],
            )
        else:
            breakdown = None
            estimate = 0
            income_amount = 0
            income_used = "none"
            needs, free, savings = 0, 0, 0

        # Fixed costs total
        active_recurring = db.list_recurring(conn, only_active=True)
        fixed_total = sum(
            calc.monthly_set_aside(r["amount_cents"], r["every_months"])
            for r in active_recurring
        )
        needs_after_fixed = needs - fixed_total

        # Spent by month
        all_spent = db.spent_by_month(conn)
        spent_in_m = all_spent.get(month_str, {"needs": 0, "free": 0, "savings": 0})
        needs_spent = spent_in_m.get("needs", 0)
        free_spent = spent_in_m.get("free", 0)
        savings_spent = spent_in_m.get("savings", 0)

        needs_left = needs_after_fixed - needs_spent

        # Free budget chain
        all_incomes = db.list_income(conn)
        if all_incomes:
            first_pay_m = min(r["pay_month"] for r in all_incomes)
            if month_str < first_pay_m:
                months = [month_str]
            else:
                months = calc.month_range(first_pay_m, month_str)
        else:
            months = [month_str]

        budget_by_month = {}
        for r in all_incomes:
            r_est = _estimate(conn, r)["total"]
            r_inc = r["actual_cents"] if r["actual_cents"] is not None else r_est
            budget_by_month[r["pay_month"]] = calc.split_income(
                r_inc, r["needs_pct"], r["free_pct"], r["save_pct"]
            )

        spent_free_by_month = {m: all_spent.get(m, {}).get("free", 0) for m in months}
        settings = db.get_settings(conn)
        carry_over = bool(settings["carry_over"])

        chain = calc.budget_chain(
            budget_by_month=budget_by_month,
            spent_free_by_month=spent_free_by_month,
            carry_over=carry_over,
            months=months,
        )

        free_info = chain[month_str]
        carry_in = free_info["carry_in"]
        free_budget = free_info["free"]
        free_available = free_info["free_available"]

        # Savings
        savings_budget = savings
        savings_diff = savings_budget - savings_spent

        return render_template(
            "overview.html",
            bank=_bank_state(conn),
            month=month_str,
            prev_month=prev_month,
            next_month=next_month,
            income_row=income_row,
            estimate=estimate,
            breakdown=breakdown,
            income_amount=income_amount,
            income_used=income_used,
            needs=needs,
            free_budget=free_budget,
            savings_budget=savings_budget,
            fixed_total=fixed_total,
            needs_after_fixed=needs_after_fixed,
            needs_spent=needs_spent,
            needs_left=needs_left,
            carry_in=carry_in,
            free_spent=free_spent,
            free_available=free_available,
            savings_spent=savings_spent,
            savings_diff=savings_diff,
        )

    # 2. Income list & add
    @app.route("/income", methods=["GET", "POST"])
    def income():
        conn = get_db()
        if request.method == "POST":
            work_month = request.form.get("work_month", "").strip()
            days_str = request.form.get("days", "").strip()
            override_str = request.form.get("hours_override", "").strip()

            try:
                existing_row = db.get_income(conn, calc.pay_month(work_month))
                if existing_row is not None and existing_row["per_day"]:
                    raise ValueError("This month uses the calendar. Edit its days there.")
                try:
                    days = int(days_str)
                except (ValueError, TypeError):
                    raise ValueError(f"Days must be an integer, got {days_str!r}")
                override = calc.parse_hours(override_str) if override_str else None
                sundays, holiday_minutes = _parse_extras(request.form)
                db.save_income(conn, work_month=work_month, days=days, minutes_override=override,
                               sundays=sundays, holiday_minutes=holiday_minutes)
                return redirect(url_for("income"), code=303)
            except ValueError as err:
                return _render_income(conn, error=str(err), status_code=400)

        return _render_income(conn)

    # 3. Income edit
    @app.route("/income/<pay_month>", methods=["GET", "POST"])
    def income_edit(pay_month: str):
        conn = get_db()
        try:
            calc.pay_month(pay_month)
        except ValueError:
            abort(404)

        income_row = db.get_income(conn, pay_month)
        if income_row is None:
            abort(404)

        if request.method == "POST":
            action = request.form.get("action", "").strip()
            try:
                if action == "refresh_snapshot":
                    db.refresh_snapshot(conn, pay_month)
                    return redirect(url_for("income_edit", pay_month=pay_month), code=303)
                elif action == "set_actual":
                    actual_str = request.form.get("actual", "").strip()
                    actual_cents = calc.parse_money(actual_str)
                    db.set_actual(conn, pay_month, actual_cents)
                    return redirect(url_for("income_edit", pay_month=pay_month), code=303)
                elif action == "clear_actual":
                    db.set_actual(conn, pay_month, None)
                    return redirect(url_for("income_edit", pay_month=pay_month), code=303)
                elif action == "delete":
                    db.delete_income(conn, pay_month)
                    _gcal_sync(conn, [income_row["work_month"]])
                    return redirect(url_for("income"), code=303)
                else:
                    raise ValueError(f"Invalid action: {action!r}")
            except ValueError as err:
                refreshed_row = db.get_income(conn, pay_month)
                shown = refreshed_row or income_row
                return render_template("income_edit.html", income=shown, breakdown=_estimate(conn, shown), error=str(err)), 400

        return render_template("income_edit.html", income=income_row, breakdown=_estimate(conn, income_row))

    # 4. Recurring costs
    @app.route("/costs", methods=["GET", "POST"])
    def costs():
        conn = get_db()
        if request.method == "POST":
            action = request.form.get("action", "").strip()
            try:
                if action == "add":
                    name = request.form.get("name", "").strip()
                    amount_cents = calc.parse_money(request.form.get("amount", ""))
                    every_months_str = request.form.get("every_months", "").strip()
                    try:
                        every_months = int(every_months_str)
                    except (ValueError, TypeError):
                        raise ValueError(f"every_months must be an integer, got {every_months_str!r}")
                    notes = request.form.get("notes", "")
                    active = 1 if request.form.get("active") else 0
                    next_due = request.form.get("next_due", "").strip() or None
                    db.add_recurring(
                        conn,
                        name=name,
                        amount_cents=amount_cents,
                        every_months=every_months,
                        notes=notes,
                        active=active,
                        next_due=next_due,
                    )
                elif action == "update":
                    id_str = request.form.get("id", "").strip()
                    try:
                        rec_id = int(id_str)
                    except (ValueError, TypeError):
                        raise ValueError(f"Invalid id: {id_str!r}")
                    name = request.form.get("name", "").strip()
                    amount_cents = calc.parse_money(request.form.get("amount", ""))
                    every_months_str = request.form.get("every_months", "").strip()
                    try:
                        every_months = int(every_months_str)
                    except (ValueError, TypeError):
                        raise ValueError(f"every_months must be an integer, got {every_months_str!r}")
                    notes = request.form.get("notes", "")
                    active = 1 if request.form.get("active") else 0
                    next_due = request.form.get("next_due", "").strip() or None
                    db.update_recurring(
                        conn,
                        rec_id=rec_id,
                        name=name,
                        amount_cents=amount_cents,
                        every_months=every_months,
                        notes=notes,
                        active=active,
                        next_due=next_due,
                    )
                elif action == "delete":
                    id_str = request.form.get("id", "").strip()
                    try:
                        rec_id = int(id_str)
                    except (ValueError, TypeError):
                        raise ValueError(f"Invalid id: {id_str!r}")
                    db.delete_recurring(conn, rec_id)
                else:
                    raise ValueError(f"Invalid action: {action!r}")
                return redirect(url_for("costs"), code=303)
            except ValueError as err:
                return _render_costs(conn, error=str(err), status_code=400)

        return _render_costs(conn)

    # 5. Expenses
    @app.route("/expenses", methods=["GET", "POST"])
    def expenses():
        conn = get_db()
        if request.method == "POST":
            action = request.form.get("action", "").strip()
            month_filter = request.form.get("month_filter", "").strip() or None
            try:
                if action == "add":
                    date_str = request.form.get("date", "").strip()
                    amount_cents = calc.parse_money(request.form.get("amount", ""))
                    bucket = request.form.get("bucket", "").strip()
                    note = request.form.get("note", "")
                    db.add_expense(
                        conn,
                        date_str=date_str,
                        amount_cents=amount_cents,
                        bucket=bucket,
                        note=note,
                    )
                elif action == "delete":
                    id_str = request.form.get("id", "").strip()
                    try:
                        exp_id = int(id_str)
                    except (ValueError, TypeError):
                        raise ValueError(f"Invalid id: {id_str!r}")
                    db.delete_expense(conn, exp_id)
                else:
                    raise ValueError(f"Invalid action: {action!r}")
                redirect_url = url_for("expenses", month=month_filter) if month_filter else url_for("expenses")
                return redirect(redirect_url, code=303)
            except ValueError as err:
                return _render_expenses(conn, month=month_filter, error=str(err), status_code=400)

        month_arg = request.args.get("month", "").strip() or None
        if month_arg is not None:
            try:
                calc.pay_month(month_arg)
            except ValueError as err:
                return _render_expenses(conn, month=None, error=str(err), status_code=400)
        return _render_expenses(conn, month=month_arg)

    # Calendar mode
    def _valid_work_month(work_month):
        try:
            calc.pay_month(work_month)
        except ValueError:
            return False
        return True

    @app.route("/calendar")
    def calendar_home():
        return redirect(url_for("calendar_page", work_month=datetime.date.today().strftime("%Y-%m")))

    @app.route("/calendar/<work_month>")
    def calendar_page(work_month):
        if not _valid_work_month(work_month):
            abort(404)
        conn = get_db()
        pay_m = calc.pay_month(work_month)
        year, mon = int(work_month[:4]), int(work_month[5:7])
        row = db.get_income(conn, pay_m)
        snap = row or db.get_settings(conn)
        per_day = bool(row and row["per_day"])
        breakdown = _estimate(conn, row) if row else None
        holidays = {k: v for k, v in calc.belgian_holidays(year).items() if k.startswith(work_month)}
        normal = snap["minutes_per_day"]
        cal = {
            "work_month": work_month,
            "pay_month": pay_m,
            "save_url": url_for("calendar_save", work_month=work_month),
            "weekday_offset": datetime.date(year, mon, 1).weekday(),
            "days_in_month": calendar.monthrange(year, mon)[1],
            "holidays": holidays,
            "entries": db.list_days(conn, work_month) if per_day else [],
            "shifts": db.list_shifts(conn),
            "presets": [
                {"id": "normal", "label": "Normal day", "minutes": normal, "holiday": False},
                {"id": "holiday", "label": "Holiday", "minutes": normal, "holiday": True},
            ],
            "rows": _breakdown_rows(breakdown) if breakdown else [],
            "total": calc.format_money(breakdown["total"]) if breakdown else calc.format_money(0),
            "days": breakdown["days"] if per_day else 0,
            "sundays": breakdown["sundays"] if per_day else 0,
        }
        legacy_note = None
        if row and not per_day:
            legacy_note = (f"This month currently uses totals ({row['days']} days, estimate "
                           f"{calc.format_money(_estimate(conn, row)['total'])}). Placing days here switches it to the calendar.")
        return render_template(
            "calendar.html",
            cal=cal,
            work_month=work_month,
            pay_month=pay_m,
            label=f"{calendar.month_name[mon]} {year}",
            prev_month=calc.add_months(work_month, -1),
            next_month=calc.add_months(work_month, 1),
            today_month=datetime.date.today().strftime("%Y-%m"),
            legacy_note=legacy_note,
        )

    @app.route("/calendar/<work_month>/save", methods=["POST"])
    def calendar_save(work_month):
        if not _valid_work_month(work_month):
            return jsonify(ok=False, error="Invalid month"), 404
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or "days" not in payload:
            return jsonify(ok=False, error="Expected JSON like {\"days\": [...]}"), 400
        conn = get_db()
        try:
            row = db.save_month_days(conn, work_month, payload["days"])
        except ValueError as err:
            return jsonify(ok=False, error=str(err)), 400
        pay_m = calc.pay_month(work_month)
        synced = _gcal_sync(conn, [work_month])
        if row is None:
            return jsonify(ok=True, pay_month=pay_m, days=0, sundays=0, total=calc.format_money(0), rows=[],
                           gcal=synced)
        b = _estimate(conn, row)
        return jsonify(ok=True, pay_month=pay_m, days=b["days"], sundays=b["sundays"],
                       total=calc.format_money(b["total"]), rows=_breakdown_rows(b), gcal=synced)

    # 6. Settings
    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        conn = get_db()
        if request.method == "POST":
            try:
                rate_cents = calc.parse_money(request.form.get("rate", ""))
                allowance_cents = calc.parse_money(request.form.get("allowance", ""))
                minutes_per_day = calc.parse_hours(request.form.get("hours_per_day", ""))
                other_str = request.form.get("other", "").strip()
                other_cents = calc.parse_money(other_str) if other_str else 0

                try:
                    needs_pct = int(request.form.get("needs_pct", ""))
                    free_pct = int(request.form.get("free_pct", ""))
                    save_pct = int(request.form.get("save_pct", ""))
                except (ValueError, TypeError):
                    raise ValueError("Percentages must be valid integers")

                carry_over = 1 if request.form.get("carry_over") else 0
                clothing_str = request.form.get("clothing", "").strip()
                clothing_cents = calc.parse_money(clothing_str) if clothing_str else 0
                current = db.get_settings(conn)
                sph_str = request.form.get("sunday_per_hour", "").strip()
                sunday_cents_per_hour = calc.parse_money(sph_str) if sph_str else current["sunday_cents_per_hour"]
                cap_str = request.form.get("sunday_cap_hours", "").strip()
                sunday_cap_minutes = calc.parse_hours(cap_str) if cap_str else current["sunday_cap_minutes"]
                rsz_bp = calc.parse_percent_bp(request.form.get("rsz", "0") or "0")

                db.update_settings(
                    conn,
                    rate_cents=rate_cents,
                    allowance_cents=allowance_cents,
                    minutes_per_day=minutes_per_day,
                    other_cents=other_cents,
                    needs_pct=needs_pct,
                    free_pct=free_pct,
                    save_pct=save_pct,
                    carry_over=carry_over,
                    clothing_cents=clothing_cents,
                    sunday_cents_per_hour=sunday_cents_per_hour,
                    sunday_cap_minutes=sunday_cap_minutes,
                    rsz_bp=rsz_bp,
                )
                return redirect(url_for("settings"), code=303)
            except ValueError as err:
                current_settings = db.get_settings(conn)
                return render_template("settings.html", settings=current_settings, error=str(err),
                                       google=_google_view(conn), shifts=db.list_shifts(conn)), 400

        current_settings = db.get_settings(conn)
        return render_template("settings.html", settings=current_settings, google=_google_view(conn),
                               shifts=db.list_shifts(conn), bank=_bank_state(conn))

    @app.route("/shifts/save", methods=["POST"])
    def shifts_save():
        conn = get_db()
        try:
            code = request.form.get("code", "")
            start_str = request.form.get("start", "")
            end_str = request.form.get("end", "")
            hours_str = request.form.get("hours", "")

            start_min = _parse_hhmm(start_str, "Start time")
            end_min = _parse_hhmm(end_str, "End time")
            minutes = calc.parse_hours(hours_str)
            db.save_shift(conn, code, start_min, end_min, minutes)
            return redirect(url_for("settings") + "#shifts", code=303)
        except ValueError as err:
            current_settings = db.get_settings(conn)
            return render_template(
                "settings.html",
                settings=current_settings,
                google=_google_view(conn),
                shifts=db.list_shifts(conn),
                error=str(err),
            ), 400

    @app.route("/shifts/delete", methods=["POST"])
    def shifts_delete():
        conn = get_db()
        try:
            code = request.form.get("code", "")
            db.delete_shift(conn, code)
            return redirect(url_for("settings") + "#shifts", code=303)
        except ValueError as err:
            current_settings = db.get_settings(conn)
            return render_template(
                "settings.html",
                settings=current_settings,
                google=_google_view(conn),
                shifts=db.list_shifts(conn),
                error=str(err),
            ), 400

    # 7. Google Calendar sync
    def _to_settings(code):
        return redirect(url_for("settings", google=code) + "#google", code=303)

    @app.route("/google/connect", methods=["POST"])
    def google_connect():
        conn = get_db()
        creds = _gcal_creds(conn)
        if creds is None:
            abort(404)
        client_id, client_secret, redirect_uri = creds
        state = secrets.token_urlsafe(32)
        session["gcal_state"] = state
        return redirect(gcal.auth_url(client_id, redirect_uri, state), code=303)

    @app.route("/google/callback")
    def google_callback():
        conn = get_db()
        creds = _gcal_creds(conn)
        if creds is None:
            abort(404)
        client_id, client_secret, redirect_uri = creds
        expected = session.pop("gcal_state", None)
        got = request.args.get("state", "")
        if not expected or not hmac.compare_digest(expected.encode(), got.encode()):
            return _to_settings("badstate")
        if request.args.get("error") or not request.args.get("code"):
            return _to_settings("denied")
        try:
            refresh, granted = gcal.exchange_code_scopes(client_id, client_secret, redirect_uri,
                                                         request.args["code"], _gcal_transport())
        except gcal.GcalError:
            return _to_settings("failed")
        db.connect_google(conn, refresh, " ".join(granted))
        result = _gcal_sync(conn, db.worked_months(conn))
        return _to_settings("connected" if result == "ok" else "connected_error")

    @app.route("/google/sync", methods=["POST"])
    def google_sync():
        conn = get_db()
        if not _gcal_configured(conn):
            abort(404)
        if db.get_google(conn) is None:
            return _to_settings("syncfailed")
        result = _gcal_sync(conn, db.worked_months(conn))
        return _to_settings("synced" if result == "ok" else "syncfailed")

    @app.route("/google/disconnect", methods=["POST"])
    def google_disconnect():
        conn = get_db()
        if not _gcal_configured(conn):
            abort(404)
        row = db.get_google(conn)
        if row is not None:
            gcal.revoke(row["refresh_token"], _gcal_transport())
            db.delete_google(conn)
        return _to_settings("disconnected")

    @app.route("/google/credentials", methods=["POST"])
    def google_credentials():
        conn = get_db()
        if db.get_google(conn) is not None:
            return _to_settings("disconnect_first")

        if request.form.get("action") == "clear":
            db.update_google_config(conn, client_id="", client_secret="", redirect_uri="")
            return _to_settings("creds_cleared")

        client_id = request.form.get("client_id", "").strip()
        redirect_uri = request.form.get("redirect_uri", "").strip()
        secret_input = request.form.get("client_secret", "")

        fields = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
        }
        if secret_input.strip():
            fields["client_secret"] = secret_input.strip()
        elif client_id and _stored_id_with_secret(conn) not in ("", client_id):
            # a new client id needs its own secret: never pair it with the stored one
            g_view = _google_view(conn)
            g_view["message"] = ("error", "Enter the client secret that belongs to the new client id.")
            return render_template("settings.html", settings=db.get_settings(conn), google=g_view, shifts=db.list_shifts(conn)), 400

        try:
            db.update_google_config(conn, **fields)
            return _to_settings("creds_saved")
        except ValueError as err:
            g_view = _google_view(conn)
            g_view["message"] = ("error", str(err))
            return render_template("settings.html", settings=db.get_settings(conn), google=g_view, shifts=db.list_shifts(conn)), 400

    @app.route("/roster/settings", methods=["POST"])
    def roster_settings():
        conn = get_db()
        sender = request.form.get("sender", "").strip()
        raw_name = request.form.get("name", "")
        name = " ".join(raw_name.split())
        if sender and not SENDER_RE.match(sender):
            return redirect(url_for("settings", google="roster_bad_sender") + "#roster", code=303)
        if len(name) > 80 or any(ord(ch) < 32 and ch not in " \t" for ch in raw_name.strip()):
            return redirect(url_for("settings", google="roster_bad_name") + "#roster", code=303)
        db.update_google_config(conn, roster_sender=sender, roster_name=name)
        return redirect(url_for("settings", google="roster_saved") + "#roster", code=303)

    @app.route("/roster/sync", methods=["POST"])
    def roster_sync_now():
        conn = get_db()
        only_month = request.form.get("work_month", "").strip() or None
        wants_json = request.headers.get("X-Requested-With") == "fetch"
        if only_month and not _valid_work_month(only_month):
            if wants_json:
                return jsonify(ok=False, message="Invalid month."), 400
            return redirect(url_for("settings") + "#roster", code=303)
        if not wants_json:  # no JavaScript: run it here and show the report on Settings
            report = _do_roster_sync(conn, only_month)
            db.set_roster_state(conn, datetime.datetime.now().isoformat(timespec="seconds"), json.dumps(report))
            return redirect(url_for("settings") + "#roster", code=303)
        if not _roster_ready(conn):
            return jsonify(ok=False, message="Connect Google, allow Gmail and fill in the sender and your name first."), 400
        with _roster_job["lock"]:
            if _roster_job["running"]:
                return jsonify(ok=False, message="A sync is already running."), 409
            _roster_job.update(running=True, percent=0, text="Starting...", done=False, ok=None, message="",
                               gcal=None, work_month=only_month)
        app_obj = current_app._get_current_object()
        if app_obj.config["DB_PATH"] == ":memory:":  # a second connection would see another database
            _roster_worker(app_obj, only_month)
        else:
            threading.Thread(target=_roster_worker, args=(app_obj, only_month), daemon=True).start()
        return jsonify(ok=True, started=True)

    @app.route("/roster/progress")
    def roster_progress():
        with _roster_job["lock"]:
            state = {k: v for k, v in _roster_job.items() if k != "lock"}
        resp = jsonify(state)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/google/options", methods=["POST"])
    def google_options():
        conn = get_db()
        try:
            weekday_start = _parse_hhmm(request.form.get("weekday_from", ""), "Weekdays from")
            weekday_end = _parse_hhmm(request.form.get("weekday_to", ""), "Weekdays to")
            weekend_start = _parse_hhmm(request.form.get("weekend_from", ""), "Weekend from")
            weekend_end = _parse_hhmm(request.form.get("weekend_to", ""), "Weekend to")
            work_color = request.form.get("work_color", "").strip()
            holiday_color = request.form.get("holiday_color", "").strip()

            db.update_google_config(
                conn,
                weekday_start=weekday_start,
                weekday_end=weekday_end,
                weekend_start=weekend_start,
                weekend_end=weekend_end,
                work_color=work_color,
                holiday_color=holiday_color,
            )
        except ValueError as err:
            g_view = _google_view(conn)
            g_view["message"] = ("error", str(err))
            return render_template("settings.html", settings=db.get_settings(conn), google=g_view, shifts=db.list_shifts(conn)), 400

        if _gcal_configured(conn) and db.get_google(conn) is not None:
            res = _gcal_sync(conn, db.worked_months(conn))
            return _to_settings("options_synced" if res == "ok" else "options_syncfailed")
        return _to_settings("options_saved")

    return app


if __name__ == "__main__":
    try:
        cfg = config.load()
    except config.ConfigError as err:
        print(f"Configuration error: {err}", file=sys.stderr)
        sys.exit(1)
    app = create_app(cfg)
    app.run(host=cfg["HOST"], port=cfg["PORT"], debug=False)
