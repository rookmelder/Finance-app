# Goal

A personal finance website for me (a student living with my mom, no rent, no tax). It tracks my income and spending and tells me what I can spend my money on. Private: runs on my own webserver (VM); I reach it via the VM's IP (my NetBird VPN handles outside access, nothing to configure in the app). UI in English.

## Settings I fill in once
- Hourly rate
- Fietsvergoeding (bike allowance) per day worked (per DAY, not per hour)
- Hours per day (default 7.5; I can change it for a month, sometimes I work an hour less)
- Other benefits (an optional extra fixed amount per month). No tax or holiday pay in v1 (I pay no tax).
- The split percentages: 50% basic needs, 20% free spending, 30% invest/savings (editable)

## Monthly income estimate
Around the 20th of a month I get my schedule for NEXT month (e.g. on 20 November I get December) and enter how many days I will work in that work month (entering total hours directly is also possible, but days is the normal way).
Estimate = days x hours per day x hourly rate + days x fietsvergoeding (+ other benefits).
Hours worked in month M are paid in the first week of month M+1 (December hours = pay in early January), so each estimate belongs to the month it is paid (the pay month).
The estimate is split into 50% / 20% / 30%.

## The 50% (basic needs)
I add recurring costs (I have about 4 subscriptions), each with an amount and how often it is paid (every month, or every N months).
Non-monthly costs are set aside per month as amount / N months.
It shows the total of these costs and what is left of the 50% after them. This is what I need to set aside, and I can use the leftover elsewhere.
It is also the place where I keep my subscriptions and financial info for easy access (name, price, interval, notes, maybe next payment date).

## Real income
When my pay arrives I enter the real amount. From then on the app uses that instead of the estimate (the estimate stays visible for comparison).

## Overview / "what can I spend"
Per month, clearly shows: income (estimate or real), the 50/20/30 split, subscriptions total, what is left in the 50%, what is left of the 20% free spending, and the 30% for saving/investing.
Unspent free money (20%) carries over to the next month: a toggle in settings, default ON. Overspending carries over as a negative. Leftover of the 50% is only shown, it does not carry over.

## Expense log
To know what is left of the 20% (and for carry-over) I can log expenses: date, amount, bucket (needs / free / savings), note. This is also how "tracks my spending" works.

## Rules
- Private financial data: password login (listens on all interfaces of the VM, port 8080; the VM is not exposed to the internet).
- English UI, works well on phone and desktop (I mostly use it on phone).
- First version done = settings, expense log, monthly estimate, real income entry, recurring costs/subscriptions, overview with the three buckets and carry-over toggle.
- Not in v1: bank connections, charts of history, multiple users.
