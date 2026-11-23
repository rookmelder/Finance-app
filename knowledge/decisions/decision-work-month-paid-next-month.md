# Decision: Work month is paid the month after
> Days are logged per work month; income and budgets belong to the following pay month.

Back to [Decisions](decisions.md). Decided 2026-11-23.

## Options
- One month key for both: simpler, but the overview would budget money not received yet.
- Separate work month and pay month: matches how the salary arrives.

## Chosen and why
Separate keys: `income` rows are keyed by `pay_month` and keep their `work_month`; `calc.pay_month` converts. The calendar and months view use work months, the overview and income editor use pay months. See [Web app](../systems/system-web-app.md).

## Revisit when
if the employer changes the pay schedule
