# Project

**Status:** filled in 2026-11-23

## About (for the Lead)
- **In one sentence:** A private personal-finance website (Flask + SQLite) that estimates monthly pay from days worked, splits it 50/20/30, tracks recurring costs/subscriptions and shows what I can spend. Details: GOAL.md.
- **Pack(s) used:** software
- **Where the work lives:** `src/` (app), `tests/`, `data/` (SQLite db, git-ignored)
- **Roles in use:** builder, builderplus, tester, reviewer, security-reviewer, scribe
- **Done means (first version):** see "First version" in GOAL.md
- **Git:** local only, no remote. No AI attribution in commits (see CLAUDE.md).
- **Run:** `python3 -m src.app` on port 8080; password from env `FINANCE_PASSWORD`, never in code.

## Settings (read by delegate.py)

```settings
check_one: python3 -m unittest tests.test_{part} -v
check_all: python3 -m unittest discover -s tests
check_dir: tests
private: yes
```

## Worker notes (pasted into every worker's task: keep it short)
- Python 3, Flask and the standard library only (sqlite3, unittest). No other packages, no Node, no network in tests.
- Money is always integer cents; convert to euros only when displaying. Never floats for money.
- Dates: ISO strings; a "month" is `YYYY-MM` and means the month the pay is RECEIVED (hours worked in the previous month).
- Return errors, never print. Server-rendered HTML (Jinja templates) with simple CSS, mobile first, English text.
- Secrets (password, secret key) come from environment variables, never in code or tests.
- Tests use a temporary SQLite file or `:memory:`, never `data/`.
