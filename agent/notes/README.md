# Notes: measurements, benchmarks and history

The setup's memory. `CLAUDE.md` holds the rules; these notes hold what we **learned**. One entry per note.

## What goes in a note
- **Measurements**: tokens, time, calls and quality per worker/model, with the date and the task.
- **Benchmarks**: same task run on several workers, scored with hidden tests. Note the setup used.
- **Lessons**: things that broke or surprised us, and the fix (e.g. "model X writes files via the shell: blocked").
- **Model findings**: what worked, what failed (404, timeouts, loops). Update `MODELS.md` ratings to match.

Not here: rules for the Lead or workers (`CLAUDE.md`, `agent/bases/`, `agent/roles/`), secrets, long logs
(summarise in a few lines, keep raw output in `.worker/`). Lessons about the *project* (not the setup) go in `knowledge/`.

## How to add an entry
- New note `agent/notes/YYYY-MM-DD-<topic>.md`: `# <Topic> (YYYY-MM-DD)`, then a one-line summary `> ...`,
  then `Back to [Notes](README.md).` Add one line for it at the bottom of the list below. Never append to an old note.
- Short and factual: numbers, model ids, what changed. One line per finding where possible. Mark untested claims.
- When a finding changes which model a role should use, also update `MODELS.md` and that role's `models:` line.

## Entries (oldest first)
- [Scripts ported to Python (2026-11-13)](2026-11-13-scripts-ported-to-python.md): PowerShell scripts became stdlib Python (delegate.py, check_setup.py); first smoke test of every worker.
- [delegate.py runs the check, workers run nothing (2026-11-15)](2026-11-15-delegate-runs-the-check.md): Workers lost their shell; delegate.py runs check_one and gives one fix round. One round fixes one layer.
- [Changed files per run, --files, run files (2026-11-15)](2026-11-15-changed-files-per-run.md): Content hashes, --files scope and one run file per run, after parallel runs mixed up their changes.
- [Lighter guides, no-tools mode, caps, benchmark (2026-11-15)](2026-11-15-lighter-guides-no-tools-mode.md): Shorter CLAUDE.md, models.json, no-tools mode, call cap, private fails closed, bench.py.
- [Builder benchmark with bench.py, chains reordered (2026-11-15)](2026-11-15-builder-benchmark.md): Two-task builder benchmark: agy flash-low best; Mistral Large 4 correct but slow, moved last.
- [Quality-neutral optimizations (2026-11-15)](2026-11-15-quality-neutral-optimizations.md): Fix round 2 only on progress, line numbers only for reviewers, incremental log reads, provider retry waits; bench 88/88 before and after.
