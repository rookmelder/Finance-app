"""Roster parser: turns PDF text tokens of the roster into worked days."""

import calendar
import datetime
from decimal import Decimal, InvalidOperation
import re
import statistics


class RosterError(Exception):
    """Base error for roster parsing issues."""
    pass


class NotARoster(RosterError):
    """Raised when the document is not recognized as a roster (e.g. missing title)."""
    pass


MONTH_MAP = {
    "january": 1, "januari": 1,
    "february": 2, "februari": 2,
    "march": 3, "maart": 3,
    "april": 4,
    "may": 5, "mei": 5,
    "june": 6, "juni": 6,
    "july": 7, "juli": 7,
    "august": 8, "augustus": 8,
    "september": 9,
    "october": 10, "oktober": 10,
    "november": 11,
    "december": 12,
}

WEEKDAY_MAP = {
    0: "ma",
    1: "din",
    2: "woe",
    3: "don",
    4: "vrij",
    5: "za",
    6: "zon",
}


def parse_roster(tokens, name):
    """Parse roster PDF tokens for a specific person.

    tokens: list of (x, y, text) tuples or lists in PDF coordinates (y grows upward).
    name: name of person to find.

    Returns dict:
        {
            "month": "YYYY-MM",
            "days": [{"date", "minutes", "code", "start", "end"}],
            "legend": {CODE: {"start", "end", "minutes"}},
            "total_minutes": int,
            "revised": "YYYY-MM-DD" | None
        }
    """
    if not isinstance(tokens, (list, tuple)):
        raise NotARoster("tokens must be a list or tuple")

    # 1. Title: two tokens on same y (|dy| < 1): month name and 4-digit year to its right
    title_pairs = []
    for tok in tokens:
        m_text = str(tok[2]).strip().lower()
        if m_text in MONTH_MAP:
            for other in tokens:
                if abs(other[1] - tok[1]) < 1.0 and other[0] > tok[0]:
                    if re.fullmatch(r"\d{4}", str(other[2]).strip()):
                        title_pairs.append(
                            (tok, other, MONTH_MAP[m_text], int(str(other[2]).strip()))
                        )

    if len(title_pairs) == 0:
        raise NotARoster("Title missing: no month and year pair found")
    if len(title_pairs) > 1:
        raise RosterError("Multiple roster title pairs found")

    _, _, month_num, year = title_pairs[0]
    month_str = f"{year:04d}-{month_num:02d}"
    _, num_days = calendar.monthrange(year, month_num)

    # 10. Revised date: first token matching M/D/YYYY (US order) as ISO date, else None
    revised = None
    for tok in tokens:
        text = str(tok[2]).strip()
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
        if m:
            try:
                m_val, d_val, y_val = int(m.group(1)), int(m.group(2)), int(m.group(3))
                dt = datetime.date(y_val, m_val, d_val)
                revised = dt.isoformat()
                break
            except ValueError:
                pass

    # 4. Person row: name appears twice (left label x~68, right copy x~538, y differs by ~1.8)
    norm_name = " ".join(str(name).strip().split()).lower()
    if not norm_name:
        raise RosterError("name not found")

    person_matches = [
        tok for tok in tokens
        if " ".join(str(tok[2]).strip().split()).lower() == norm_name
    ]

    if not person_matches:
        raise RosterError("name not found")

    ys = [tok[1] for tok in person_matches]
    if max(ys) - min(ys) > 3.0:
        raise RosterError("Multiple rows matching name found")

    right_name_tok = max(person_matches, key=lambda t: t[0])
    anchor_y = float(right_name_tok[1])
    x_right_name = float(right_name_tok[0])

    # 2. Number row: >= 28 tokens that are integers 1..31 on one y (+-0.5).
    # Nearest number row ABOVE person's row (larger y).
    int_tokens = []
    for tok in tokens:
        txt = str(tok[2]).strip()
        if txt.isdigit():
            val = int(txt)
            if 1 <= val <= 31:
                int_tokens.append(tok)

    clusters = []
    for tok in sorted(int_tokens, key=lambda t: t[1]):
        placed = False
        for c in clusters:
            avg_y = sum(item[1] for item in c) / len(c)
            if abs(tok[1] - avg_y) <= 0.5:
                c.append(tok)
                placed = True
                break
        if not placed:
            clusters.append([tok])

    candidate_number_rows = []
    for c in clusters:
        distinct_vals = set(int(str(t[2]).strip()) for t in c)
        if len(c) >= 28 or len(distinct_vals) >= 5:
            avg_y = sum(item[1] for item in c) / len(c)
            candidate_number_rows.append((avg_y, c))

    rows_above = [nr for nr in candidate_number_rows if nr[0] > anchor_y]
    if not rows_above:
        raise RosterError("Number row not found above person row")

    chosen_nr = min(rows_above, key=lambda nr: nr[0] - anchor_y)
    number_row_y, num_tokens = chosen_nr

    if len(num_tokens) < 28:
        raise RosterError(f"Number row has fewer than 28 tokens: {len(num_tokens)}")

    colx = {}
    for tok in num_tokens:
        d = int(str(tok[2]).strip())
        if d in colx:
            raise RosterError(f"Duplicate day {d} in number row")
        colx[d] = float(tok[0])

    if 1 not in colx:
        raise RosterError("Day 1 missing from number row")

    sorted_days = sorted(colx.keys())
    diffs = [colx[sorted_days[i + 1]] - colx[sorted_days[i]] for i in range(len(sorted_days) - 1)]
    if not diffs:
        raise RosterError("Not enough days in number row to calculate step")
    step = statistics.median(diffs)
    if step <= 0:
        raise RosterError("Invalid column step in number row")

    # Cross-check weekday of day 1:
    weekday_tokens = [
        tok for tok in tokens
        if 2.0 <= (tok[1] - number_row_y) <= 12.0
        and str(tok[2]).strip().lower() in WEEKDAY_MAP.values()
    ]
    if not weekday_tokens:
        raise RosterError("Weekday row not found above number row")

    nearest_wd = min(weekday_tokens, key=lambda t: abs(t[0] - colx[1]))
    if abs(nearest_wd[0] - colx[1]) > step:
        raise RosterError("Weekday token nearest colx[1] is outside step")

    day1_weekday = datetime.date(year, month_num, 1).weekday()
    expected_wd = WEEKDAY_MAP[day1_weekday]
    actual_wd = str(nearest_wd[2]).strip().lower()
    if actual_wd != expected_wd:
        raise RosterError(f"Weekday mismatch for day 1: expected {expected_wd}, got {actual_wd}")

    if set(colx.keys()) != set(range(1, num_days + 1)):
        raise RosterError(f"Number row must contain exactly days 1..{num_days}")

    # 7. Legend: rows (cluster by y, tol 1.0) at x < colx[1]-step, above number row
    legend_tokens = [
        tok for tok in tokens
        if tok[0] < (colx[1] - step) and tok[1] > number_row_y
    ]

    legend_clusters = []
    for tok in sorted(legend_tokens, key=lambda t: t[1], reverse=True):
        placed = False
        for c in legend_clusters:
            avg_y = sum(item[1] for item in c) / len(c)
            if abs(tok[1] - avg_y) <= 1.0:
                c.append(tok)
                placed = True
                break
        if not placed:
            legend_clusters.append([tok])

    legend = {}
    for cluster in legend_clusters:
        code_candidates = [
            t for t in cluster
            if t[0] < 50.0 and re.fullmatch(r"[A-Za-z0-9]+", str(t[2]).strip())
        ]
        if not code_candidates:
            continue
        code_tok = min(code_candidates, key=lambda t: t[0])
        code = str(code_tok[2]).strip().upper()

        time_match = None
        time_tok = None
        for t in cluster:
            m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", str(t[2]).strip())
            if m:
                time_tok = t
                time_match = m
                break
        if not time_match:
            continue

        hours_tok = None
        for t in cluster:
            if t is not code_tok and t is not time_tok:
                try:
                    Decimal(str(t[2]).strip())
                    hours_tok = t
                    break
                except InvalidOperation:
                    pass
        if not hours_tok:
            continue

        start_h, start_m = int(time_match.group(1)), int(time_match.group(2))
        end_h, end_m = int(time_match.group(3)), int(time_match.group(4))
        start_min = start_h * 60 + start_m
        end_min = end_h * 60 + end_m
        if end_min <= start_min:
            raise RosterError(f"Legend shift {code}: end must be > start")

        hours_dec = Decimal(str(hours_tok[2]).strip())
        mins_dec = hours_dec * 60
        if mins_dec != int(mins_dec):
            raise RosterError(f"Legend shift {code}: hours not whole minutes")

        legend[code] = {
            "start": start_min,
            "end": end_min,
            "minutes": int(mins_dec),
        }

    # 4 & 5. HOURS line & Total
    hours_line_tokens = [
        tok for tok in tokens
        if (anchor_y - 8.0) < tok[1] < (anchor_y - 2.5)
    ]

    numeric_total_candidates = []
    for tok in hours_line_tokens:
        if abs(tok[0] - x_right_name) < 20.0:
            try:
                dec_val = Decimal(str(tok[2]).strip())
                numeric_total_candidates.append((tok, dec_val))
            except InvalidOperation:
                pass

    if not numeric_total_candidates:
        raise RosterError("Total missing")

    total_tok, total_dec = min(
        numeric_total_candidates, key=lambda pair: abs(pair[0][0] - x_right_name)
    )
    total_mins_dec = total_dec * 60
    if total_mins_dec != int(total_mins_dec):
        raise RosterError(f"Total hours {total_dec} not a whole number of minutes")
    total_minutes = int(total_mins_dec)

    # 3. Cell day matching: |x - colx[n]| <= step/2
    def find_day(x):
        best_d = None
        best_dist = float("inf")
        for d in range(1, num_days + 1):
            dist = abs(x - colx[d])
            if dist < best_dist:
                best_dist = dist
                best_d = d
        if best_dist <= step / 2.0:
            return best_d
        return None

    code_line_tokens = [
        tok for tok in tokens
        if abs(tok[1] - anchor_y) <= 1.0 and tok[0] >= (colx[1] - step / 2.0)
    ]

    day_codes = {}
    for tok in code_line_tokens:
        d = find_day(tok[0])
        if d is not None:
            code_str = str(tok[2]).strip()
            if code_str:
                if d in day_codes:
                    raise RosterError(f"Two code tokens for day {d}")
                day_codes[d] = code_str

    day_hours = {}
    for tok in hours_line_tokens:
        d = find_day(tok[0])
        if d is not None:
            try:
                hrs_dec = Decimal(str(tok[2]).strip())
            except InvalidOperation:
                raise RosterError(f"Invalid hours token {tok[2]} on day {d}")
            mins_dec = hrs_dec * 60
            if mins_dec != int(mins_dec):
                raise RosterError(f"Hours {tok[2]} on day {d} not whole number of minutes")
            if d in day_hours:
                raise RosterError(f"Two hour tokens for day {d}")
            day_hours[d] = int(mins_dec)

    # 8. Days creation
    days = []
    for d in range(1, num_days + 1):
        date_str = f"{year:04d}-{month_num:02d}-{d:02d}"
        code_raw = day_codes.get(d)
        hours_min = day_hours.get(d)

        if code_raw is not None:
            code_upper = code_raw.upper()
            if hours_min is not None and hours_min > 0:
                if code_upper not in legend:
                    raise RosterError(f"unknown code {code_upper} on {date_str}")
                days.append({
                    "date": date_str,
                    "minutes": hours_min,
                    "code": code_upper,
                    "start": legend[code_upper]["start"],
                    "end": legend[code_upper]["end"],
                })
            else:
                if code_upper in legend:
                    raise RosterError(f"legend code {code_upper} on {date_str} has no hours")
        else:
            if hours_min is not None and hours_min > 0:
                raise RosterError(f"Hours without code on day {d}")

    days.sort(key=lambda item: item["date"])

    # 9. Total check
    sum_minutes = sum(item["minutes"] for item in days)
    if sum_minutes != total_minutes:
        raise RosterError(f"total mismatch: sum of days ({sum_minutes}) != total ({total_minutes})")

    return {
        "month": month_str,
        "days": days,
        "legend": legend,
        "total_minutes": total_minutes,
        "revised": revised,
    }
