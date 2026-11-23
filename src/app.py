"""Flask web application for Finance app."""

import datetime
import os
import sys
from flask import (
    Flask,
    abort,
    current_app,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from src import auth, calc, config, db


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


def _estimate(row) -> dict:
    """Pay breakdown for an income row, using the settings snapshot stored on that row."""
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
    raw_incomes = db.list_income(conn)
    incomes = []
    for r in raw_incomes:
        item = dict(r)
        item["estimate"] = _estimate(r)["total"]
        item["used"] = "actual" if r["actual_cents"] is not None else "estimate"
        incomes.append(item)
    today = datetime.date.today()
    default_wm = calc.default_work_month(today)
    default_pm = calc.pay_month(default_wm)
    return (
        render_template(
            "income.html",
            incomes=incomes,
            default_wm=default_wm,
            default_pm=default_pm,
            error=error,
        ),
        status_code,
    )


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


def create_app(cfg: dict) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.config.update(cfg)
    app.secret_key = cfg["SECRET_KEY"]

    # Jinja filters
    app.jinja_env.filters["money"] = calc.format_money
    app.jinja_env.filters["hours"] = calc.format_hours
    app.jinja_env.filters["percent"] = calc.format_percent_bp

    # Request database teardown
    @app.teardown_appcontext
    def close_db(exception=None):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    # Install authentication and CSRF guard
    auth.install(app, cfg["PASSWORD"])

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
            breakdown = _estimate(income_row)
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
            r_est = _estimate(r)["total"]
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
                calc.pay_month(work_month)
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
                if action == "save":
                    days_str = request.form.get("days", "").strip()
                    try:
                        days = int(days_str)
                    except (ValueError, TypeError):
                        raise ValueError(f"Days must be an integer, got {days_str!r}")
                    override_str = request.form.get("hours_override", "").strip()
                    override = calc.parse_hours(override_str) if override_str else None
                    refresh = bool(request.form.get("refresh_snapshot"))
                    sundays, holiday_minutes = _parse_extras(request.form)
                    db.save_income(
                        conn,
                        work_month=income_row["work_month"],
                        days=days,
                        minutes_override=override,
                        refresh_snapshot=refresh,
                        sundays=sundays,
                        holiday_minutes=holiday_minutes,
                    )
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
                    return redirect(url_for("income"), code=303)
                else:
                    raise ValueError(f"Invalid action: {action!r}")
            except ValueError as err:
                refreshed_row = db.get_income(conn, pay_month)
                shown = refreshed_row or income_row
                return render_template("income_edit.html", income=shown, breakdown=_estimate(shown), error=str(err)), 400

        return render_template("income_edit.html", income=income_row, breakdown=_estimate(income_row))

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
                sunday_str = request.form.get("sunday_premium", "").strip()
                sunday_premium_cents = calc.parse_money(sunday_str) if sunday_str else 0
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
                    sunday_premium_cents=sunday_premium_cents,
                    rsz_bp=rsz_bp,
                )
                return redirect(url_for("settings"), code=303)
            except ValueError as err:
                current_settings = db.get_settings(conn)
                return render_template("settings.html", settings=current_settings, error=str(err)), 400

        current_settings = db.get_settings(conn)
        return render_template("settings.html", settings=current_settings)

    return app


if __name__ == "__main__":
    try:
        cfg = config.load()
    except config.ConfigError as err:
        print(f"Configuration error: {err}", file=sys.stderr)
        sys.exit(1)
    app = create_app(cfg)
    app.run(host=cfg["HOST"], port=cfg["PORT"], debug=False)
