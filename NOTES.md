# Notes: measurements, benchmarks and history

This file is the setup's memory. `CLAUDE.md` holds the rules; this file holds what we **learned**.
Keep `CLAUDE.md` short by moving history and numbers here.

## What goes in here
- **Measurements**: tokens, time, calls and quality per worker/model, with the date and the task.
- **Benchmarks**: same task run on several workers, scored with hidden tests. Note the setup used.
- **Lessons**: things that broke or surprised us, and the fix (e.g. "model X writes files via the shell: blocked").
- **Model findings**: what worked, what failed (404, timeouts, loops). Update `MODELS.md` ratings to match.

## What does NOT go in here
- Rules for the Lead or workers: those go in `CLAUDE.md` or `roles/<role>.md`.
- API keys or anything secret.
- Long logs: summarise in a few lines, keep raw output in `.worker/` (git-ignored).

## How to add an entry
- New section per topic: `## <Topic> (YYYY-MM-DD)`, newest at the bottom.
- Short and factual: numbers, model ids, what changed. One line per finding where possible.
- Mark untested claims as untested.
- When a finding changes which model a role should use, also update `MODELS.md` and the chains in `delegate.ps1`.

## Entries

(none yet)
