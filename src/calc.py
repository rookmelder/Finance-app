import datetime
import re
from decimal import Decimal, ROUND_HALF_UP


def _parse_month(month_str: str) -> tuple[int, int]:
    """Validate YYYY-MM format with month 01-12 and return (year, month)."""
    if not isinstance(month_str, str):
        raise ValueError(f"Invalid month: {month_str!r}")
    m = re.fullmatch(r"([0-9]{4})-([0-9]{2})", month_str)
    if not m:
        raise ValueError(f"Invalid month format (expected YYYY-MM): {month_str!r}")
    year = int(m.group(1))
    month = int(m.group(2))
    if not (1 <= month <= 12):
        raise ValueError(f"Month out of range 01-12: {month_str!r}")
    return year, month


def pay_month(work_month: str) -> str:
    """Return pay month (work_month + 1 month) as YYYY-MM."""
    year, month = _parse_month(work_month)
    if month == 12:
        return f"{year + 1:04d}-01"
    else:
        return f"{year:04d}-{month + 1:02d}"


def default_work_month(today: datetime.date) -> str:
    """If today.day >= 15 the next calendar month, else current month."""
    if not isinstance(today, (datetime.date, datetime.datetime)):
        raise ValueError(f"Invalid date: {today!r}")
    year = today.year
    month = today.month
    if today.day >= 15:
        if month == 12:
            return f"{year + 1:04d}-01"
        else:
            return f"{year:04d}-{month + 1:02d}"
    else:
        return f"{year:04d}-{month:02d}"


def month_range(start: str, end: str) -> list[str]:
    """Inclusive list of YYYY-MM from start to end."""
    sy, sm = _parse_month(start)
    ey, em = _parse_month(end)
    if (ey, em) < (sy, sm):
        raise ValueError(f"end month {end!r} is before start month {start!r}")

    result = []
    cy, cm = sy, sm
    while (cy, cm) <= (ey, em):
        result.append(f"{cy:04d}-{cm:02d}")
        if cm == 12:
            cy += 1
            cm = 1
        else:
            cm += 1
    return result


def _half_up(numerator: int, denominator: int) -> int:
    """Integer division rounding half up, for numerator >= 0 and denominator > 0."""
    return (2 * numerator + denominator) // (2 * denominator)


def estimate_breakdown(
    rate_cents: int,
    minutes_per_day: int,
    days: int,
    allowance_cents: int,
    other_cents: int = 0,
    minutes_override: int | None = None,
    clothing_cents: int = 0,
    sundays: int = 0,
    holiday_minutes: int = 0,
    sunday_premium_cents: int = 0,
    rsz_bp: int = 0,
) -> dict:
    """Estimate a month's pay the way the payslip does (all values integer cents).

    hours_pay     = half_up(rate * minutes / 60), minutes = override or days * minutes_per_day
    holiday_extra = half_up(rate * holiday_minutes / 60)   (a holiday is paid double: the extra 1x)
    sunday_pay    = sundays * sunday_premium_cents
    gross         = hours_pay + holiday_extra + sunday_pay  (the RSZ base)
    rsz           = half_up(gross * rsz_bp / 10000)         (social security, basis points: 271 = 2.71%)
    net           = gross - rsz
    allowances    = days * (allowance_cents + clothing_cents)   (fiets + kledij, no RSZ on these)
    total         = net + allowances + other_cents          (what lands on the bank account)
    """
    values = {
        "rate_cents": rate_cents, "minutes_per_day": minutes_per_day, "days": days,
        "allowance_cents": allowance_cents, "other_cents": other_cents,
        "clothing_cents": clothing_cents, "sundays": sundays,
        "holiday_minutes": holiday_minutes, "sunday_premium_cents": sunday_premium_cents,
    }
    if minutes_override is not None:
        values["minutes_override"] = minutes_override
    for name, value in values.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{name} must be an integer >= 0")
    if not isinstance(rsz_bp, int) or isinstance(rsz_bp, bool) or not 0 <= rsz_bp <= 10000:
        raise ValueError("rsz_bp must be an integer between 0 and 10000")

    minutes = minutes_override if minutes_override is not None else days * minutes_per_day
    hours_pay = _half_up(rate_cents * minutes, 60)
    holiday_extra = _half_up(rate_cents * holiday_minutes, 60)
    sunday_pay = sundays * sunday_premium_cents
    gross = hours_pay + holiday_extra + sunday_pay
    rsz = _half_up(gross * rsz_bp, 10000)
    net = gross - rsz
    allowances = days * (allowance_cents + clothing_cents)
    return {
        "minutes": minutes,
        "hours_pay": hours_pay,
        "holiday_extra": holiday_extra,
        "sunday_pay": sunday_pay,
        "gross": gross,
        "rsz": rsz,
        "net": net,
        "allowances": allowances,
        "other": other_cents,
        "total": net + allowances + other_cents,
    }


def estimate_cents(*args, **kwargs) -> int:
    """Total estimated pay in cents; same arguments as estimate_breakdown."""
    return estimate_breakdown(*args, **kwargs)["total"]


def split_income(
    income_cents: int, needs_pct: int, free_pct: int, save_pct: int
) -> tuple[int, int, int]:
    """
    ValueError unless all >= 0 and sum == 100.
    needs = income*needs_pct//100, free = income*free_pct//100,
    savings = income - needs - free (so the three ALWAYS add up to income)
    """
    if needs_pct < 0 or free_pct < 0 or save_pct < 0:
        raise ValueError("Percentages must be non-negative")
    if needs_pct + free_pct + save_pct != 100:
        raise ValueError(
            f"Percentages must sum to 100 (got {needs_pct + free_pct + save_pct})"
        )

    needs = (income_cents * needs_pct) // 100
    free = (income_cents * free_pct) // 100
    savings = income_cents - needs - free
    return needs, free, savings


def monthly_set_aside(amount_cents: int, every_months: int) -> int:
    """ceil(amount / every_months); ValueError if every_months < 1 or amount < 0"""
    if every_months < 1:
        raise ValueError("every_months must be at least 1")
    if amount_cents < 0:
        raise ValueError("amount_cents cannot be negative")
    # Integer ceil: (amount_cents + every_months - 1) // every_months
    return (amount_cents + every_months - 1) // every_months


def parse_money(text: str) -> int:
    """
    accepts "14", "14.5", "14,50", with optional leading/trailing spaces and optional leading "€";
    rejects empty, negative, non-numeric, more than 2 decimals. Returns integer cents.
    """
    if not isinstance(text, str):
        raise ValueError("Expected string input")
    s = text.strip()
    if not s:
        raise ValueError("Empty money string")
    if s.startswith("€"):
        s = s[1:].strip()
    if not s:
        raise ValueError("Empty money string after currency symbol")

    # Replace comma with dot
    s = s.replace(",", ".")

    # Check for negative sign
    if s.startswith("-") or s.startswith("+"):
        raise ValueError("Signs not allowed in parse_money or negative value")

    # Validate regex: digits optionally followed by . and 1 or 2 digits
    if not re.fullmatch(r"[0-9]+(\.[0-9]{1,2})?", s):
        raise ValueError(f"Invalid money format: {text!r}")

    d = Decimal(s)
    cents = int(d * 100)
    return cents


def parse_hours(text: str) -> int:
    """
    decimal hours -> minutes; accept up to 2 decimals; minutes = round half up;
    reject negative/garbage/empty.
    """
    if not isinstance(text, str):
        raise ValueError("Expected string input")
    s = text.strip()
    if not s:
        raise ValueError("Empty hours string")
    s = s.replace(",", ".")

    if s.startswith("-") or s.startswith("+"):
        raise ValueError("Signs not allowed in parse_hours or negative value")

    if not re.fullmatch(r"[0-9]+(\.[0-9]{1,2})?", s):
        raise ValueError(f"Invalid hours format: {text!r}")

    d = Decimal(s)
    # Convert hours to minutes: d * 60, round half up
    minutes_dec = (d * Decimal(60)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(minutes_dec)


def format_money(cents: int) -> str:
    """
    196200 -> "€1,962.00", -5 -> "-€0.05"
    thousands separated by comma.
    """
    is_neg = cents < 0
    abs_cents = abs(cents)
    euros = abs_cents // 100
    rem_cents = abs_cents % 100
    formatted = f"€{euros:,}.{rem_cents:02d}"
    if is_neg:
        return f"-{formatted}"
    return formatted


def format_hours(minutes: int) -> str:
    """
    hours as decimal without trailing zeros:
    450 -> "7.5", 480 -> "8", 465 -> "7.75", 470 -> "7.83" (round to 2 decimals, half up).
    """
    # decimal hours = minutes / 60
    # round to 2 decimals half up
    d = (Decimal(minutes) / Decimal(60)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    # format without unnecessary trailing zeros / decimal point
    s = f"{d:.2f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def budget_chain(
    budget_by_month: dict[str, tuple[int, int, int]],
    spent_free_by_month: dict[str, int],
    carry_over: bool,
    months: list[str],
) -> dict[str, dict]:
    """
    `budget_chain`: `budget_by_month[m]` = `(needs, free, savings)` already split for that month (missing month = (0,0,0)).
    For each month in `months` (ascending, consecutive) it returns `{"free", "carry_in", "spent_free", "free_available"}`:
    `carry_in` = previous month's `free_available` if `carry_over` and the month is not the first in `months`, else 0 (may be negative);
    `spent_free` = `spent_free_by_month.get(m, 0)`; `free_available = free + carry_in - spent_free`
    """
    result: dict[str, dict] = {}
    prev_free_available = 0

    for idx, m in enumerate(months):
        budget = budget_by_month.get(m, (0, 0, 0))
        free = budget[1]
        spent_free = spent_free_by_month.get(m, 0)

        if idx == 0 or not carry_over:
            carry_in = 0
        else:
            carry_in = prev_free_available

        free_available = free + carry_in - spent_free
        result[m] = {
            "free": free,
            "carry_in": carry_in,
            "spent_free": spent_free,
            "free_available": free_available,
        }
        prev_free_available = free_available

    return result


def parse_percent_bp(text: str) -> int:
    """"2.71" or "2,71" or "2.71%" -> 271 basis points (1% = 100); up to 2 decimals, 0-100."""
    if not isinstance(text, str):
        raise ValueError("Expected string input")
    s = text.strip().rstrip("%").strip().replace(",", ".")
    if not re.fullmatch(r"[0-9]+(\.[0-9]{1,2})?", s):
        raise ValueError(f"Invalid percentage: {text!r}")
    bp = int(Decimal(s) * 100)
    if bp > 10000:
        raise ValueError("Percentage cannot be more than 100")
    return bp


def format_percent_bp(bp: int) -> str:
    """271 -> "2.71", 250 -> "2.5", 0 -> "0"."""
    s = f"{bp // 100}.{bp % 100:02d}".rstrip("0").rstrip(".")
    return s


def easter(year: int) -> datetime.date:
    """Gregorian Easter Sunday (anonymous / Meeus-Jones-Butcher algorithm)."""
    a = year % 19
    b, cc = divmod(year, 100)
    dd, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - dd - g + 15) % 30
    i, k = divmod(cc, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return datetime.date(year, month, day + 1)


def belgian_holidays(year: int) -> dict:
    """The 10 Belgian legal holidays of a year as {"YYYY-MM-DD": name}."""
    e = easter(year)
    td = datetime.timedelta
    days = {
        datetime.date(year, 1, 1): "New Year's Day",
        e + td(days=1): "Easter Monday",
        datetime.date(year, 5, 1): "Labour Day",
        e + td(days=39): "Ascension Day",
        e + td(days=50): "Whit Monday",
        datetime.date(year, 7, 21): "Belgian National Day",
        datetime.date(year, 8, 15): "Assumption Day",
        datetime.date(year, 11, 1): "All Saints' Day",
        datetime.date(year, 11, 11): "Armistice Day",
        datetime.date(year, 12, 25): "Christmas Day",
    }
    return {k.isoformat(): v for k, v in sorted(days.items())}


def estimate_from_days(
    days: list,
    rate_cents: int,
    allowance_cents: int,
    clothing_cents: int = 0,
    sunday_cents_per_hour: int = 0,
    sunday_cap_minutes: int = 0,
    rsz_bp: int = 0,
    other_cents: int = 0,
) -> dict:
    """Payslip estimate from a list of worked days: [{"date": "YYYY-MM-DD", "minutes": int, "holiday": bool}].

    Same result keys as estimate_breakdown plus "days", "sundays", "holiday_minutes".
    hours_pay: half_up(rate * total_minutes / 60), once. holiday_extra: half_up(rate * holiday_minutes / 60), once.
    sunday_pay: per Sunday half_up(sunday_cents_per_hour * min(minutes, cap) / 60), summed.
    """
    for name, value in (("rate_cents", rate_cents), ("allowance_cents", allowance_cents),
                        ("clothing_cents", clothing_cents), ("sunday_cents_per_hour", sunday_cents_per_hour),
                        ("sunday_cap_minutes", sunday_cap_minutes), ("other_cents", other_cents)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{name} must be an integer >= 0")
    if not isinstance(rsz_bp, int) or isinstance(rsz_bp, bool) or not 0 <= rsz_bp <= 10000:
        raise ValueError("rsz_bp must be an integer between 0 and 10000")
    seen = set()
    total_minutes = holiday_minutes = sunday_pay = sundays = 0
    for entry in days:
        try:
            date = datetime.date.fromisoformat(entry["date"])
            minutes = entry["minutes"]
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Invalid day entry: {entry!r}")
        if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= 1440:
            raise ValueError(f"minutes must be 1-1440 for {entry['date']}")
        if date in seen:
            raise ValueError(f"Duplicate date {entry['date']}")
        seen.add(date)
        total_minutes += minutes
        if entry.get("holiday"):
            holiday_minutes += minutes
        if date.weekday() == 6:
            sundays += 1
            sunday_pay += _half_up(sunday_cents_per_hour * min(minutes, sunday_cap_minutes), 60)
    hours_pay = _half_up(rate_cents * total_minutes, 60)
    holiday_extra = _half_up(rate_cents * holiday_minutes, 60)
    gross = hours_pay + holiday_extra + sunday_pay
    rsz = _half_up(gross * rsz_bp, 10000)
    net = gross - rsz
    allowances = len(seen) * (allowance_cents + clothing_cents)
    return {
        "minutes": total_minutes,
        "hours_pay": hours_pay,
        "holiday_extra": holiday_extra,
        "sunday_pay": sunday_pay,
        "gross": gross,
        "rsz": rsz,
        "net": net,
        "allowances": allowances,
        "other": other_cents,
        "total": net + allowances + other_cents,
        "days": len(seen),
        "sundays": sundays,
        "holiday_minutes": holiday_minutes,
    }


def add_months(month: str, n: int) -> str:
    """Shift a YYYY-MM month by n months (negative allowed)."""
    year, mon = _parse_month(month)
    index = year * 12 + (mon - 1) + n
    return f"{index // 12:04d}-{index % 12 + 1:02d}"
