# Decision: Money as integer cents
> All money is stored and computed as integer cents; euros only when displaying.

Back to [Decisions](decisions.md). Decided 2026-11-23.

## Options
- Floats: simple, but rounding errors on payslip math.
- Decimal everywhere: exact, but clumsy in SQLite and JSON.
- Integer cents: exact, plain INTEGER columns.

## Chosen and why
Integer cents (and integer minutes for time, basis points for RSZ). Rounding is half-up in one place (`_half_up` in `src/calc.py`) to match Belgian payslips. See [Pay calculations](../systems/system-pay-calc.md).

## Revisit when
never
