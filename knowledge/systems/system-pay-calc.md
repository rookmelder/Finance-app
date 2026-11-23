# System: Pay and Budget Calculations
> Pure pay calculations, budget split algorithms, calendar date arithmetic, and money formatting.

Back to [Systems](systems.md).

## What it does
Calculates net pay, RSZ deductions, Sunday premiums, holiday bonuses, and allowances from worked days or summary totals.
Splits net income across needs, free spending, and savings, and chains monthly free allowances across consecutive months.
Parses and formats money, hours, basis-point percentages, and calculates the 10 Belgian legal holidays.

## Files
- `src/calc.py`: pure calculation functions for payslips, budget chaining, parsing, and Belgian holiday dates.
- `tests/test_calc.py`: unit tests for payslip estimation, rounding rules, budget carry-over, and parsing edge cases.

## How it connects
- Uses: none (relies only on Python standard library modules `datetime`, `re`, and `decimal`).
- Used by: [system-web-app.md](system-web-app.md) (estimates, budgets, calendar holidays), [system-database.md](system-database.md) (month validation).
- Data: reads and returns in-memory dictionaries and primitives. Money is integer cents, time is integer minutes, percentages are basis points (1% = 100 bp), dates are ISO strings `YYYY-MM-DD`, and months are `YYYY-MM`.

## Why it is built this way
- Avoids floats: money is integer cents and time is integer minutes to avoid precision errors. Half-up integer division (`_half_up`) matches Belgian payslip rounding.
- Timing invariant: a work month `YYYY-MM` is paid the month after via `pay_month`. `default_work_month` rolls over to the next month on the 15th.
- RSZ base: social security (`rsz_bp`) applies only to gross pay (`hours_pay`, `holiday_extra`, `sunday_pay`). Bicycle and clothing allowances (per day) are added without RSZ.
- Legal holidays: worked legal holidays from `belgian_holidays` are paid double, calculated as standard `hours_pay` plus an extra 1x via `holiday_extra`.
- Sunday caps: `estimate_from_days` caps Sunday minutes per day at `sunday_cap_minutes` before computing Sunday premium.
- Exact budget splits: in `split_income`, savings is `income - needs - free` so the three parts always sum to income despite integer division.
- Carry-over chaining: `budget_chain` allows carry-in amounts from prior months to be negative when spending exceeds available free budget.

## Known issues
- none
