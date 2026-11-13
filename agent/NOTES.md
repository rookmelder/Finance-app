# Notes: measurements, benchmarks and history

This file is the setup's memory. `CLAUDE.md` holds the rules; this file holds what we **learned**.
Keep `CLAUDE.md` short by moving history and numbers here.

## What goes in here
- **Measurements**: tokens, time, calls and quality per worker/model, with the date and the task.
- **Benchmarks**: same task run on several workers, scored with hidden tests. Note the setup used.
- **Lessons**: things that broke or surprised us, and the fix (e.g. "model X writes files via the shell: blocked").
- **Model findings**: what worked, what failed (404, timeouts, loops). Update `MODELS.md` ratings to match.

## What does NOT go in here
- Rules for the Lead or workers: those go in `CLAUDE.md`, `agent/bases/` or `agent/roles/<role>.md`.
- API keys or anything secret.
- Long logs: summarise in a few lines, keep raw output in `.worker/` (git-ignored).

## How to add an entry
- New section per topic: `## <Topic> (YYYY-MM-DD)`, newest at the bottom.
- Short and factual: numbers, model ids, what changed. One line per finding where possible.
- Mark untested claims as untested.
- When a finding changes which model a role should use, also update `MODELS.md` and the `models:` line of that role in `agent/roles/`.

## Entries

## Scripts ported to Python (2026-11-13)
- `delegate.ps1` / `check-setup.ps1` replaced by `agent/delegate.py` / `agent/check_setup.py` (stdlib only) so the
  setup runs on Linux/macOS too. Same behaviour; flags are now `--role`, `--task-file`, ... Unit tests: `agent/tests/`.
  Reason besides Linux: on this PC `powershell -File` was blocked by the execution policy.
- Smoke test on Windows, one small task each, all OK: agy gemini-3.8-flash-low builder 12s; NVIDIA Mistral Large 4
  builder 281s (slow day); agy claude-sonnet-4-6 reviewer 12s; NVIDIA Nemotron lightning scribe 26s; Gemini API
  tester 22s; `--conversation` follow-up 10s; OpenAI gpt-5.4-mini 16s (usage logged); timeouts give exit 3 on agy
  and Copilot with no copilot.exe left over; a tester ran `check_one` via Copilot's shell tool; Ollama gemma4:12b 85s.
- Not tested yet: a real Linux machine (there Copilot's shell tool should be `bash,read_bash`: unverified).
- Lesson: a reviewer without `--include` (OpenAI gpt-5.4-mini, no check command) did not open the file and answered
  "Unproven" for everything. Always `--include` the files a reviewer must judge.
- NVIDIA's 1-token key test took 15-30+ s; check_setup.py waits up to 60 s for it.
