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

## delegate.py runs the check, workers run nothing (2026-11-15)
- Workers lost their shell tools (Copilot: no `powershell`/`bash`; agy: no `run_command`). `delegate.py` runs `check_one`
  itself (`--part` fills `{name}`), sends a failure back to the same worker once, runs it again; exit 7 if it still fails.
  Reviewers get the check result pasted into the task. Gone: the agy global allow-rule, the Copilot "any `python`
  command" hole, the "run it max 2x" reminders.
- Tested: unit tests only (fake worker; pass, fix round, exit 7, no `--part`, `--no-check`).
- Smoke test on Windows (tiny builder task, a hidden test asked for `ValueError('empty')` that the spec did not mention):
  agy gemini-3.8-flash-low 35s incl. fix round (22.8k in); NVIDIA Mistral Large 4 via Copilot 328s incl. fix round
  (6 calls, 31k in). Both wrote files with no shell tool, got the failing output, and fixed what it showed. Both ended
  on exit 7: the first failure only said "ValueError not raised", the exact message showed up in the second run.
  So one fix round solves one layer of failure; a test with stacked failures needs the Lead (or a 2nd round: not built).
- A third run (NVIDIA, allowed to read the test) passed first time in 120s, 5 calls: workers read existing tests.
- Untested: whether quality drops on real tasks now that a worker gets one fix round instead of running the check
  itself. Re-run the builder benchmark before trusting MODELS.md ratings. Tester and reviewer roles not smoke-tested.

## Changed files per run, --files, run files (2026-11-15)
- Seen in the smoke test: two workers in parallel, and the NVIDIA run listed the agy run's `calca.py` as its own.
- `delegate.py` now compares content hashes before/after (a second edit to an already-changed file used to be
  invisible), takes `--files` (changes outside it are listed apart and no longer block the fallback chain), and
  saves each run as `.worker/run-<id>.json` (`last.json` was overwritten by parallel runs).
- Limit: without `--files` a parallel run still lists everyone's changes; the script cannot tell who wrote a file.
  Real isolation would need a git worktree per worker: not built.
- Tested: unit tests only (temp git repo). Not re-run with real parallel workers.

## Lighter guides, no-tools mode, caps, benchmark (2026-11-15)
- `CLAUDE.md` went from 9,849 to 6,108 characters: the start-of-project steps and the file table moved to
  `agent/START.md` (read once per project), the flag list shrank to what is used every run (`--help` has the rest).
- `MODELS.md` split: pick order stays, cost/limits/provider notes moved to `LIMITS.md`.
- Models, providers and limits moved from `delegate.py` to `agent/models.json`.
- No-tools mode: a reviewer with `--include`, or a scribe writing one `.md`, on an API model is ONE plain API call
  (no Copilot). agy workers still use the agy CLI.
- Call cap for every Copilot worker: `max_calls` 40 (models.json, `--max-calls`). OpenRouter Nemotron Ultra used ~60
  requests per task in the old benchmark; in the new one (next entry) it needed 6-11, so the cap does not stop it.
- `--fix-rounds 0|1|2` replaces `--no-fix`.
- Private mode fails closed: anything but `private: no` in PROJECT.md's settings means agy only.
- Copilot workers: deny rules for reading and writing `.env` (tested: a worker asked to open `.env` was denied).
  The prompt now goes in on stdin (Windows limits a command line to ~32k characters). agy has no such deny rule.
- `agent/bench.py`: two tasks (textkit: new module; inventory: fix + extend), hidden tests, table for MODELS.md.
- Real runs after these changes (Windows):
  - No-tools reviewer, Gemini flash-lite: 1 call, 1,029 tokens in, 2 s, found the real spec differences.
  - No-tools scribe, NVIDIA Nemotron lightning: 1 call, 10 s. Its first try mangled the file markers (`<<<FILE>>`),
    so the markers became `BEGIN FILE` / `END FILE` and decoration around them is ignored.
  - bench, agy gemini-3.8-flash-low: inventory 10/10 (23 s, 8.8k in), textkit 12/12 (31 s, 15.1k in), no fix round.
  - bench, NVIDIA Mistral Large 4 on textkit: 12/12 hidden tests but TIMEOUT at 480 s after 4 calls (slow NVIDIA day),
    own check failed (it timed out before its test file was right). Prompt on stdin worked.
- Untested: the call cap and a second fix round with a real worker (unit tests only); tester role; Linux.

## Builder benchmark with bench.py, chains reordered (2026-11-15)
Two tasks (inventory 10 hidden tests, textkit 12), one run each, Windows, through `agent/bench.py`.
| Worker | Hidden tests | Seconds (inventory / textkit) | Calls | Tokens in |
|---|---|---|---|---|
| agy gemini-3.8-flash-low | 22/22 | 23 / 31 | ? | 8.8k / 15.1k |
| Gemini API gemini-flash-lite-latest | 22/22 | 25 / 46 | 4 / 7 | 32k / 60k |
| OpenRouter nemotron-3-ultra:free | 22/22 | 78 / 260 | 6 / 11 | 49k / 95k |
| NVIDIA mistral-large-4 | 22/22 | 307 / 482 (timeout) | 6 / 4 | 42k / 10k |
| NVIDIA nemotron-3.5-lightning | 22/22 | 83 / 430 | 11 / 74 | 156k / 2.46M |
| OpenRouter cohere north-mini-code:free | 10/22 | 111 / 11 | 43 / 5 | 337k / 18k |
- Mistral Large 4 on NVIDIA is correct but takes 25-120 s per call: moved to the end of the builder, builderplus
  and tester chains. Gemini API flash-lite is the new builder fallback after agy.
- Nemotron Ultra needed 6-11 calls here, not the ~60 from the old benchmark: the 40-call cap does not stop it.
- Nemotron lightning loops as a builder; the cap stopped its fix round at 40, but the first run had already used 34.
  The cap now counts the whole run (a fix round only gets what is left).
- One run per cell: times vary by day (NVIDIA especially). Tester and builderplus chains were reordered by these
  builder numbers, not by a test of their own.
