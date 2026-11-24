"""Bank ledger calculations. Dates are ISO strings and amounts are integer cents.

Read functions do not settle automatically. ``current`` returns cents or None;
``history`` returns chronological points with delta_cents and balance_cents.
``upcoming`` returns {events: [...], total_cents: signed total}.
"""

import calendar
from datetime import date

from src import calc, db


_KIND_ORDER = {"pay": 0, "pay_adjust": 1, "recurring": 2, "expense": 3}


def last_day_of_month(month):
    """Return the last ISO date of a YYYY-MM month."""
    first = date.fromisoformat(month + "-01")
    return first.replace(day=calendar.monthrange(first.year, first.month)[1]).isoformat()


def _shift(due, months, day):
    month = calc.add_months(due[:7], months)
    return month + "-" + str(min(day, int(last_day_of_month(month)[8:]))).zfill(2)


def _latest_set(events, today):
    return next((e for e in reversed(events)
                 if e["kind"] == "set" and e["date"] <= today), None)


def _anchor(row, events):
    """Day of month a recurring cost keeps. It is the day of next_due, unless next_due sits on the last
    day of its month (a possible month-end clamp): then the day of the latest posted event is used if larger."""
    day = int(row["next_due"][8:])
    if row["next_due"] == last_day_of_month(row["next_due"][:7]):
        prefix = str(row["id"]) + ":"
        posted = [e for e in events if e["kind"] == "recurring" and e["ref"].startswith(prefix)]
        if posted:
            day = max(day, int(max(posted, key=lambda e: e["id"])["date"][8:]))
    return day


def settle(conn, today, income_amount):
    """Post due costs, pay and actual-pay corrections after the latest set."""
    date.fromisoformat(today)
    events = db.list_balance_events(conn)
    start = _latest_set(events, today)
    if start is None:
        return
    refs = {(e["kind"], e["ref"]) for e in events}
    for row in db.list_recurring(conn):
        if not row["active"] or not row["next_due"]:
            continue
        due = row["next_due"]
        anchor = _anchor(row, events)
        while due <= today:
            ref = str(row["id"]) + ":" + due
            if due > start["date"] and ("recurring", ref) not in refs:
                db.add_balance_event(conn, due, "recurring", -row["amount_cents"],
                                     ref=ref, note=row["name"])
                refs.add(("recurring", ref))
            due = _shift(due, row["every_months"], anchor)
        if due != row["next_due"]:
            db.update_recurring(conn, row["id"], next_due=due)

    pay_day = db.get_balance_config(conn)["pay_day"]
    for row in db.list_income(conn):
        month = row["pay_month"]
        due = month + "-" + str(pay_day).zfill(2)
        if due > today:
            continue
        pay = next((e for e in events if e["kind"] == "pay" and e["ref"] == month), None)
        if pay is None:
            amount = income_amount(row)
            if due > start["date"] and amount != 0:
                db.add_balance_event(conn, due, "pay", amount, ref=month,
                                     note="Pay " + month)
            continue
        if row["actual_cents"] is None:
            continue
        adjustments = [e for e in events if e["kind"] == "pay_adjust"
                       and e["ref"].startswith(month + ":")]
        difference = income_amount(row) - pay["amount_cents"] - sum(
            e["amount_cents"] for e in adjustments)
        if difference:
            db.add_balance_event(conn, today, "pay_adjust", difference,
                                 ref=month + ":" + str(len(adjustments) + 1),
                                 note="Pay adjustment " + month)


def set_balance(conn, amount_cents, today, income_amount, note=""):
    """Set an absolute balance after settling the preceding segment."""
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool):
        raise ValueError("amount_cents must be an integer")
    if abs(amount_cents) > 100_000_000_000:  # 1 billion euros: far beyond any bank account, and SQLite-safe
        raise ValueError("amount_cents is out of range")
    date.fromisoformat(today)
    settle(conn, today, income_amount)
    return db.add_balance_event(conn, today, "set", amount_cents,
                                note=note, meta=db.max_expense_id(conn))


def history(conn, today):
    """Return points by segment, then date; sets precede that day's changes.

    Within a day ledger changes retain id order, followed by expenses in id
    order. An expense already included in a set is excluded on that set's day.
    """
    events = db.list_balance_events(conn)
    sets = [e for e in events if e["kind"] == "set" and e["date"] <= today]
    expenses = db.list_expenses(conn)
    points = []
    running = 0
    for index, start in enumerate(sets):
        end = sets[index + 1] if index + 1 < len(sets) else None
        changes = [e for e in events if e["kind"] != "set"
                   and e["id"] > start["id"] and e["date"] <= today
                   and (end is None or e["id"] < end["id"])]
        for expense in expenses:
            if not start["date"] <= expense["date"] <= today:
                continue
            if expense["date"] == start["date"] and expense["id"] <= (start["meta"] or 0):
                continue
            if end is not None and (expense["date"] > end["date"] or
                    (expense["date"] == end["date"] and expense["id"] > (end["meta"] or 0))):
                continue
            changes.append({**expense, "kind": "expense",
                            "amount_cents": -expense["amount_cents"]})
        ordered = [start] + sorted(changes, key=lambda e: (
            e["date"], _KIND_ORDER.get(e["kind"], 9), e["id"]))
        for event in ordered:
            delta = event["amount_cents"] - running if event["kind"] == "set" else event["amount_cents"]
            running += delta
            label = event["note"] or (event.get("bucket", "Expense")
                                      if event["kind"] == "expense" else "Balance set")
            points.append({**event, "label": label, "delta_cents": delta,
                           "balance_cents": running})
    return points


def current(conn, today):
    """Return the latest running balance in cents, or None before a set."""
    points = history(conn, today)
    return points[-1]["balance_cents"] if points else None


def upcoming(conn, today, until, income_amount):
    """Project pay and recurring changes in (today, until], without writes."""
    events = db.list_balance_events(conn)
    projected = []
    for row in db.list_recurring(conn):
        if not row["active"] or not row["next_due"]:
            continue
        due = row["next_due"]
        anchor = _anchor(row, events)
        while due <= until:
            if due > today:
                projected.append({"date": due, "kind": "recurring",
                                  "amount_cents": -row["amount_cents"], "label": row["name"]})
            due = _shift(due, row["every_months"], anchor)
    pay_day = db.get_balance_config(conn)["pay_day"]
    for row in db.list_income(conn):
        due = row["pay_month"] + "-" + str(pay_day).zfill(2)
        if today < due <= until:
            amount = income_amount(row)
            if amount:
                projected.append({"date": due, "kind": "pay", "amount_cents": amount,
                                  "label": "Pay " + row["pay_month"]})
    projected.sort(key=lambda e: (e["date"], e["kind"], e["label"]))
    return {"events": projected, "total_cents": sum(e["amount_cents"] for e in projected)}
