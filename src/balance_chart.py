"""SVG line chart of the bank balance evolution."""

from __future__ import annotations

import datetime
import math
from typing import Callable
from xml.sax.saxutils import escape, quoteattr

_MONTHS = [
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]

_XML_ENTITIES = {'"': "&quot;", "'": "&apos;"}


def _escape_xml(text: str) -> str:
    return escape(str(text), entities=_XML_ENTITIES)


def _euro(cents: int, cents_digits: int | None = None) -> str:
    """Format integer cents as a euro string.

    Thousands separator ',', '.' decimals, minus sign '-', prefix '€'.
    """
    if cents_digits is None:
        cents_digits = 2

    cents_abs = abs(cents)
    if cents_digits == 0:
        euros = (cents_abs + 50) // 100
        sign = "-" if cents < 0 and euros != 0 else ""
        return f"{sign}€{euros:,}"

    euros = cents_abs // 100
    rem = cents_abs % 100
    sign = "-" if cents < 0 and (euros != 0 or rem != 0) else ""
    return f"{sign}€{euros:,}.{rem:02d}"


def _parse_date(d: str | datetime.date) -> datetime.date:
    if isinstance(d, datetime.date):
        return d
    return datetime.date.fromisoformat(str(d))


def _get_grid_ticks(y_min: int, y_max: int) -> list[int]:
    """Find 3 to 5 nice round values in cents within [y_min, y_max]."""
    span = y_max - y_min
    if span <= 0:
        return [y_min]

    candidates: list[int] = []
    for exp in range(0, 11):
        base = 10**exp
        for mult in (1, 2, 2.5, 5):
            val = int(mult * base)
            if val > 0 and val not in candidates:
                candidates.append(val)
    candidates.sort()

    best_ticks: list[int] | None = None
    best_dist = 999

    for step in candidates:
        first = math.ceil(y_min / step) * step
        last = math.floor(y_max / step) * step
        if first > last:
            continue
        count = (last - first) // step + 1
        if 3 <= count <= 5:
            ticks = [first + i * step for i in range(count)]
            dist = abs(count - 4)
            if dist < best_dist:
                best_dist = dist
                best_ticks = ticks
                if dist == 0:
                    break

    if best_ticks is not None:
        return best_ticks

    for step in candidates:
        first = math.ceil(y_min / step) * step
        last = math.floor(y_max / step) * step
        if first > last:
            continue
        count = (last - first) // step + 1
        dist = abs(count - 4)
        if dist < best_dist:
            best_dist = dist
            best_ticks = [first + i * step for i in range(count)]

    if not best_ticks or len(best_ticks) < 3:
        step = max(1, span // 3)
        best_ticks = [y_min + i * step for i in range(4)]

    return best_ticks


def chart_svg(
    points: list[dict],
    today: datetime.date,
    width: int = 640,
    height: int = 240,
    euro: Callable[..., str] | None = None,
) -> str:
    """Draw an inline SVG chart of the bank balance evolution.

    points: list of dicts oldest first:
      {"date": "YYYY-MM-DD", "amount": int cents, "delta": int, "kind": str, "label": str}
    today: datetime.date.
    Returns "" when points is empty.
    """
    if not points:
        return ""

    if euro is None:
        euro = _euro

    today_date = _parse_date(today)
    d0 = _parse_date(points[0]["date"])
    d_last = _parse_date(points[-1]["date"])
    d_end = max(today_date, d_last)

    total_days = (d_end - d0).days
    if total_days <= 0:
        total_days = 1

    amounts = [p["amount"] for p in points]
    min_amount = min(amounts)
    max_amount = max(amounts)

    if min_amount < 0 and max_amount < 0:
        raw_min = min_amount
        raw_max = 0
        zero_line_visible = True
    elif min_amount <= 0 <= max_amount:
        raw_min = min_amount
        raw_max = max_amount
        zero_line_visible = True
    else:
        raw_min = min_amount
        raw_max = max_amount
        zero_line_visible = False

    raw_span = raw_max - raw_min
    if raw_span == 0:
        pad = max((abs(raw_min) * 8 + 99) // 100, 100)
        y_min = raw_min - pad
        y_max = raw_max + pad
    else:
        pad = max((raw_span * 8 + 99) // 100, 1)
        y_min = raw_min - pad
        y_max = raw_max + pad

    if not zero_line_visible:
        y_min = max(0, y_min)

    cents_digits = 0 if raw_span > 20000 else 2

    # Dimensions and margins
    pad_left = 60.0
    pad_right = 20.0
    pad_top = 20.0
    pad_bottom = 28.0

    plot_w = max(1.0, width - pad_left - pad_right)
    plot_h = max(1.0, height - pad_top - pad_bottom)
    x_left = pad_left
    x_right = pad_left + plot_w
    y_top = pad_top
    y_bottom = pad_top + plot_h

    y_span = max(1, y_max - y_min)

    # Grid lines and labels
    grid_ticks = _get_grid_ticks(y_min, y_max)
    grid_elements: list[str] = []
    zero_drawn = False

    for tick in grid_ticks:
        ty = y_bottom - ((tick - y_min) / y_span) * plot_h
        if tick == 0 and zero_line_visible:
            grid_elements.append(
                f'<line class="zero" x1="{x_left:.1f}" y1="{ty:.1f}" x2="{x_right:.1f}" y2="{ty:.1f}" stroke="var(--text)" stroke-opacity="0.4" />'
            )
            zero_drawn = True
        else:
            grid_elements.append(
                f'<line class="grid" x1="{x_left:.1f}" y1="{ty:.1f}" x2="{x_right:.1f}" y2="{ty:.1f}" stroke="var(--border)" />'
            )
        tick_label = euro(tick, cents_digits)
        escaped_label = _escape_xml(tick_label)
        grid_elements.append(
            f'<text class="label y-label" x="{x_left - 8.0:.1f}" y="{ty:.1f}" text-anchor="end" dominant-baseline="middle" fill="var(--muted)" font-size="11">{escaped_label}</text>'
        )

    if zero_line_visible and not zero_drawn:
        y_zero = y_bottom - ((0 - y_min) / y_span) * plot_h
        grid_elements.append(
            f'<line class="zero" x1="{x_left:.1f}" y1="{y_zero:.1f}" x2="{x_right:.1f}" y2="{y_zero:.1f}" stroke="var(--text)" stroke-opacity="0.4" />'
        )

    # X axis labels
    x_label_left = f"{d0.day} {_MONTHS[d0.month]}"
    x_label_right = f"{d_end.day} {_MONTHS[d_end.month]}"
    escaped_x_left = _escape_xml(x_label_left)
    escaped_x_right = _escape_xml(x_label_right)
    x_elements = [
        f'<text class="label x-label" x="{x_left:.1f}" y="{height - 8:.1f}" text-anchor="start" fill="var(--muted)" font-size="11">{escaped_x_left}</text>',
        f'<text class="label x-label" x="{x_right:.1f}" y="{height - 8:.1f}" text-anchor="end" fill="var(--muted)" font-size="11">{escaped_x_right}</text>',
    ]

    # Step line path commands
    path_cmds: list[str] = []
    prev_y = y_bottom

    for i, pt in enumerate(points):
        pt_date = _parse_date(pt["date"])
        days = (pt_date - d0).days
        x = x_left + (days / total_days) * plot_w
        y = y_bottom - ((pt["amount"] - y_min) / y_span) * plot_h

        if i == 0:
            path_cmds.append(f"M {x:.1f} {y:.1f}")
        else:
            path_cmds.append(f"L {x:.1f} {prev_y:.1f}")
            path_cmds.append(f"L {x:.1f} {y:.1f}")
        prev_y = y

    # Last segment extends flat to the right edge (today)
    path_cmds.append(f"L {x_right:.1f} {prev_y:.1f}")
    line_d = " ".join(path_cmds)

    # Area fill
    if zero_line_visible:
        y_baseline = y_bottom - ((0 - y_min) / y_span) * plot_h
    else:
        y_baseline = y_bottom

    area_d = f"{line_d} L {x_right:.1f} {y_baseline:.1f} L {x_left:.1f} {y_baseline:.1f} Z"

    # Dots for points (capped at last 120 points)
    dot_points = points[-120:] if len(points) > 120 else points
    dots_svg: list[str] = []

    for pt in dot_points:
        pt_date = _parse_date(pt["date"])
        days = (pt_date - d0).days
        cx = x_left + (days / total_days) * plot_w
        cy = y_bottom - ((pt["amount"] - y_min) / y_span) * plot_h

        label = pt.get("label", "")
        delta = pt.get("delta", 0)
        amount = pt.get("amount", 0)
        date_str = (
            pt["date"]
            if isinstance(pt["date"], str)
            else pt["date"].isoformat()
        )

        delta_sign = "+" if delta >= 0 else ""
        signed_delta = delta_sign + euro(delta, cents_digits)
        balance_str = euro(amount, cents_digits)

        raw_title = f"{label}: {signed_delta} -> {balance_str} on {date_str}"
        escaped_title = _escape_xml(raw_title)

        dots_svg.append(
            f'<circle class="dot" cx="{cx:.1f}" cy="{cy:.1f}" r="3.5" fill="var(--accent)"><title>{escaped_title}</title></circle>'
        )

    # aria-label and svg title
    last_amount_str = euro(points[-1]["amount"])
    aria_text = f"Bank balance over time, now {last_amount_str}"
    quoted_aria = quoteattr(aria_text)
    escaped_aria = _escape_xml(aria_text)

    svg_parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label={quoted_aria} class="balance-chart">',
        f"<title>{escaped_aria}</title>",
        *grid_elements,
        *x_elements,
        f'<path class="area" d="{area_d}" fill="var(--accent)" fill-opacity="0.12" />',
        f'<path class="line" d="{line_d}" fill="none" stroke="var(--accent)" stroke-width="2" />',
        *dots_svg,
        "</svg>",
    ]

    return "".join(svg_parts)
