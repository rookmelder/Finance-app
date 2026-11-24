"""Roster sync orchestration: reads roster emails, parses PDF, and updates worked days."""

import datetime
import email.utils

from src import db, roster, roster_pdf
from src.gmail import GmailError
from src.roster import NotARoster, RosterError
from src.roster_pdf import PdfError

ENGLISH_MONTHS = [
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
]


def _format_month(work_month: str) -> str:
    parts = work_month.split("-")
    if len(parts) == 2 and parts[1].isdigit():
        mon = int(parts[1])
        if 1 <= mon <= 12:
            return f"{ENGLISH_MONTHS[mon]} {parts[0]}"
    return work_month


def sync(
    conn,
    gmail,
    today: datetime.date,
    extract_tokens=roster_pdf.extract_tokens,
    parse=roster.parse_roster,
    query_days: int = 120,
    max_mails: int = 30,
    only_month=None,
    progress=None,
) -> dict:
    """Sync roster emails into work days and shifts."""
    cfg = db.get_google_config(conn)
    sender = (cfg.get("roster_sender") or "").strip()
    name = (cfg.get("roster_name") or "").strip()
    if not sender or not name:
        if progress is not None:
            progress(100, "Done")
        return {
            "ok": False,
            "message": "Fill in the sender's email address and your name first.",
            "months": [],
            "ignored": 0,
            "errors": [],
            "changed_months": [],
        }

    ignored = 0
    errors = []
    error_dates = []  # internal dates of mails whose roster could not be read
    candidates = {}

    try:
        query = f"from:{sender} has:attachment filename:pdf newer_than:{query_days}d"
        limit = max_mails
        if only_month is not None:
            first_day = datetime.date.fromisoformat(only_month + "-01")
            after = first_day - datetime.timedelta(days=90)
            query = f"from:{sender} has:attachment filename:pdf after:{after:%Y/%m/%d}"
            limit = max(max_mails, 60)
        if progress is not None:
            progress(2, "Searching your mail...")
        ids = gmail.search(query, limit=limit)

        for i, mid in enumerate(ids, 1):
            if progress is not None:
                progress(10 + 70 * (i - 1) // len(ids), f"Reading mail {i} of {len(ids)}...")
            cached = db.get_roster_mail(conn, mid, sender, name)
            if cached is not None:
                if cached["kind"] == "skip":
                    continue
                if cached["kind"] == "ignored":
                    ignored += 1
                    continue
                result = cached["result"]
                month = result["month"]
                candidate = {
                    "month": month,
                    "message_id": mid,
                    "internal_date": cached["internal_date"],
                    "result": result,
                }
                if month not in candidates or candidate["internal_date"] >= candidates[month]["internal_date"]:
                    candidates[month] = candidate
                continue

            msg = gmail.get_message(mid)
            internal_date = int(msg.get("internal_date", 0))
            from_addr = email.utils.parseaddr(msg.get("from", ""))[1]
            if from_addr.lower() != sender.lower():
                db.save_roster_mail(conn, mid, sender, name, internal_date, "skip")
                continue

            attachments = msg.get("attachments", [])
            pdf_candidates = [
                att for att in attachments
                if att.get("filename", "").lower().endswith(".pdf") or att.get("mime_type") == "application/pdf"
            ]
            label = msg.get("subject") or msg.get("id") or str(mid)
            pdf_data = []
            for att in pdf_candidates:
                data = gmail.get_attachment(msg["id"], att["attachment_id"])
                if data.startswith(b"%PDF"):
                    pdf_data.append(data)

            if not pdf_data:
                db.save_roster_mail(conn, mid, sender, name, internal_date, "skip")
                continue

            for data in pdf_data:
                try:
                    tokens = extract_tokens(data)
                except PdfError as err:
                    errors.append(f"{label}: {err}")
                    error_dates.append(int(msg.get("internal_date", 0)))
                    continue

                try:
                    result = parse(tokens, name)
                except NotARoster:
                    ignored += 1
                    if len(pdf_data) == 1:
                        db.save_roster_mail(conn, mid, sender, name, internal_date, "ignored")
                    continue
                except RosterError as err:
                    errors.append(f"{label}: {err}")
                    error_dates.append(int(msg.get("internal_date", 0)))
                    continue

                if len(pdf_data) == 1:
                    db.save_roster_mail(conn, mid, sender, name, internal_date, "roster", result=result)
                candidate = {
                    "month": result["month"],
                    "message_id": msg["id"],
                    "internal_date": int(msg.get("internal_date", 0)),
                    "result": result,
                }
                m = candidate["month"]
                if m not in candidates or candidate["internal_date"] >= candidates[m]["internal_date"]:
                    candidates[m] = candidate

    except GmailError as err:
        if progress is not None:
            progress(100, "Done")
        return {
            "ok": False,
            "message": str(err),
            "months": [],
            "ignored": 0,
            "errors": [],
            "changed_months": [],
        }

    if progress is not None:
        progress(85, "Updating your days...")
    current = today.strftime("%Y-%m")
    months = []
    changed_months = []

    for month in sorted(candidates.keys()):
        if only_month is not None and month != only_month:
            continue
        candidate = candidates[month]
        result = candidate["result"]
        message_id = candidate["message_id"]
        internal_date = candidate["internal_date"]

        if only_month is None and month < current:
            months.append({
                "work_month": month,
                "status": "skipped",
                "detail": "month is already over",
            })
            continue

        if any(d > internal_date for d in error_dates):
            # a newer roster mail exists that we could not read: it may be a revision of this month
            months.append({
                "work_month": month,
                "status": "skipped",
                "detail": "a newer roster mail could not be read, so this month was left unchanged",
            })
            continue

        prev = db.get_roster_import(conn, month)
        if prev and prev["internal_date"] > internal_date:
            months.append({
                "work_month": month,
                "status": "skipped",
                "detail": "a newer roster was already applied",
            })
            continue

        wanted = [
            {
                "date": d["date"],
                "minutes": d["minutes"],
                "code": d["code"],
                "start": d["start"],
                "end": d["end"],
            }
            for d in result.get("days", [])
        ]
        all_stored = db.list_days(conn, month)
        stored = [d for d in all_stored if not d["holiday"]]

        wanted_map = {d["date"]: (d["minutes"], d["code"], d["start"], d["end"]) for d in wanted}
        stored_map = {d["date"]: (d["minutes"], d["code"], d["start"], d["end"]) for d in stored}

        if wanted_map == stored_map:
            db.set_roster_import(conn, month, message_id, internal_date, today.isoformat() + "T00:00:00")
            months.append({
                "work_month": month,
                "status": "unchanged",
                "detail": "already up to date",
            })
            continue

        # Else: update
        added = sum(1 for dt in wanted_map if dt not in stored_map)
        removed = sum(1 for dt in stored_map if dt not in wanted_map)
        changed = sum(1 for dt in wanted_map if dt in stored_map and wanted_map[dt] != stored_map[dt])

        try:
            existing_shifts = {s["code"] for s in db.list_shifts(conn)}
            legend = result.get("legend", {})
            for code, s_info in legend.items():
                if code not in existing_shifts:
                    db.save_shift(conn, code, s_info["start"], s_info["end"], s_info["minutes"])
                    existing_shifts.add(code)

            wanted_dates = set(wanted_map.keys())
            entries = [
                {
                    "date": d["date"],
                    "minutes": d["minutes"],
                    "holiday": False,
                    "code": d["code"],
                    "start": d["start"],
                    "end": d["end"],
                }
                for d in wanted
            ]
            for d in all_stored:
                if d["holiday"] and d["date"] not in wanted_dates:
                    entries.append({
                        "date": d["date"],
                        "minutes": d["minutes"],
                        "holiday": True,
                    })

            entries.sort(key=lambda x: x["date"])

            db.save_month_days(conn, month, entries)
            db.set_roster_import(conn, month, message_id, internal_date, today.isoformat() + "T00:00:00")
            months.append({
                "work_month": month,
                "status": "updated",
                "detail": f"{added} added, {removed} removed, {changed} changed",
            })
            changed_months.append(month)
        except ValueError as err:
            months.append({
                "work_month": month,
                "status": "error",
                "detail": str(err),
            })

    updated_list = [m for m in months if m["status"] == "updated"]
    if updated_list:
        message = " ".join(f"Updated {_format_month(m['work_month'])}: {m['detail']}." for m in updated_list)
    elif only_month is not None:
        if months:
            if months[0]["status"] == "skipped":
                message = f"{_format_month(only_month)}: {months[0]['detail']}."
            else:
                message = f"{_format_month(only_month)} is already up to date."
        else:
            message = f"No roster for {_format_month(only_month)} found in your mail."
    elif months:
        message = "Everything is up to date."
    else:
        message = "No roster found in your mail."

    if progress is not None:
        progress(100, "Done")
    return {
        "ok": True,
        "message": message,
        "months": months,
        "ignored": ignored,
        "errors": errors,
        "changed_months": changed_months,
    }
