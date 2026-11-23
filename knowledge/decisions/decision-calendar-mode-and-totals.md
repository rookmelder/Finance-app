# Decision: Calendar days and totals as two modes
> A month is either entered day by day on the calendar (per_day) or as totals; saving days switches it to calendar mode.

Back to [Decisions](decisions.md). Decided 2026-11-23.

## Options
- Totals only: fast, but no Sundays/holidays per day.
- Calendar only: exact, but old months were entered as totals.
- Both, one mode per month.

## Chosen and why
Both. `save_month_days` sets `per_day = 1` and keeps the derived totals in step; the totals form refuses months in calendar mode. See [Database](../systems/system-database.md) and [Calendar UI](../systems/system-calendar-ui.md).

## Revisit when
if all old totals months are re-entered as days
