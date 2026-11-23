# Decision: Settings snapshot per income month
> Each income month keeps a copy of the rates it was made with; changing Settings does not change past months.

Back to [Decisions](decisions.md). Decided 2026-11-23.

## Options
- Always use current settings: one place, but a raise would rewrite old payslips.
- Snapshot per month: history stays true.

## Chosen and why
Snapshot on the `income` row, refreshed only on request (`refresh_snapshot`, the button on the income edit page). See [Database](../systems/system-database.md).

## Revisit when
never
