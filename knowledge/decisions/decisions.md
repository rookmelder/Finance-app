# Decisions

Back to the [knowledge map](../README.md). One line per decision, newest at the bottom.

## Decisions
- [Money as integer cents](decision-money-integer-cents.md): All money is stored and computed as integer cents; euros only when displaying.
- [Work month is paid the month after](decision-work-month-paid-next-month.md): Days are logged per work month; income and budgets belong to the following pay month.
- [Calendar days and totals as two modes](decision-calendar-mode-and-totals.md): A month is either entered day by day on the calendar (per_day) or as totals; saving days switches it to calendar mode.
- [Settings snapshot per income month](decision-settings-snapshot-per-month.md): Each income month keeps a copy of the rates it was made with; changing Settings does not change past months.
- [Google sync to an app-created calendar with a narrow scope](decision-google-sync-app-created-calendar.md): Worked days go to a "Work" calendar the app creates, with scope calendar.app.created and a month diff instead of stored event ids.
