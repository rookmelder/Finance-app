# Multi-agent setup: Claude leads, workers execute

You (Claude Code) are the **Lead**. You plan, write specs, split the work, hand pieces to cheap worker
models, check everything they do, and commit. Workers are fast and cheap but make mistakes: you own the result.
Works for any project: software, games, research, automation, writing.

**New project?** If `PROJECT.md` says "NOT FILLED IN YET" or `GOAL.md` says "NOT WRITTEN YET", read
`agent/START.md` and follow it first. It also lists what every file in this setup is for.

## Roles and workers
A worker = a **model** + a **harness** that lets it read and write files here: **agy** (Google Antigravity CLI) or
**Copilot CLI** (all API models: NVIDIA, Gemini API, OpenRouter, OpenAI). **Ollama** (`--local`) is text only: you write its files.
No worker runs commands: `delegate.py` runs the project's check itself.

- A role is `agent/roles/<name>.md`: `base` (builder | tester | reviewer | scribe), `models` (the chain, first = main;
  unavailable workers are skipped, busy/quota errors move on to the next), `does`. Format: `templates/roles/ROLE-TEMPLATE.md`.
- Default roles: **builder** (one piece + its tests), **builderplus** (multi-file, tricky), **tester** (tests for work
  it didn't make), **reviewer** (checklist review), **scribe** (docs).
- Tester and reviewer must use a different model than the builder (its model is in the summary line). Force one with
  `--model <agy model>` or `--provider <name> [--provider-model <id>]`.
- **Private project** (`private:` in `PROJECT.md`; anything but `no` counts as private): only agy workers run, cloud
  APIs are refused. Otherwise use `--local` or do it yourself.
- OpenAI models only in reviewer or scribe roles. Never agy `claude-opus-4-6-thinking`; agy `claude-sonnet-4-6` only for reviewers.
- Models for new roles: `agent/MODELS.md`. Models, providers and limits the script knows: `agent/models.json`.

## Team size
Max 4 workers at once: 2 on agy + 1 NVIDIA + 1 Gemini API (fewer if `SETUP.md` has less). Never two workers on the same files.
- **Tiny** (<30 lines, or faster to write than to explain): do it yourself.
- **Small** (1 file): 1 worker → you check and review.
- **Medium** (2-4 files): spec → 2 builders in parallel (different harnesses) → full check → tester + reviewer in parallel → fix, commit.
- **Large** (5+ files): write `SPEC.md` first → builders in waves of max 4 → integrate → tester/reviewer per part → scribe → final review.

## Running a worker (from the project root, in the background: 1-8 minutes)
```bash
python agent/delegate.py --role builder --task-file .worker/t1.md --part shop --files src/shop.py tests/test_shop.py --include src/shop.py
```
- `--part <name>`: fills `{name}` in `check_one`. The script runs that check after a builder/tester; a failure goes
  back to the worker (`--fix-rounds`, default 2; round 2 only if round 1 changed the failure). For a reviewer the check runs first and is pasted into its task.
- `--files <files or globs>`: what the task may change. Always give it: the summary then lists this run's own files,
  and anything else under `NOT IN --files` (another worker's files, or this worker left its scope: check which).
- `--include <files>`: pastes files into the task, so the worker spends no calls opening them.
- Reviewer with `--include`, or scribe with `--files <one .md>` (new, or also in `--include`): an API model then runs
  as ONE plain call without tools (cheapest). `--no-direct` turns that off.
- `--conversation <id>`: follow-up on a result (max 2, then do it yourself). All other flags: `python agent/delegate.py --help`.
- Exit: 0 ok, 2 bad role/no task, 3 timeout (files often exist), 4 blocked action, 5 empty, 6 provider error /
  refused / stopped by a cap, 7 check still failing. Full result: the `.worker/run-<id>.json` named in the summary.

## Writing a task (base rules, role rules and PROJECT notes are added automatically)
- **Goal** (1 sentence), **Files** (exact paths to create/change), **Spec** (exact: signatures, formulas, messages,
  wording, edge cases), **Must cover:** (cases the tests or the text must handle).
- Paste the spec and `--include` the files it needs: each extra call resends ~5-20k tokens.
- Create new folders yourself before delegating: workers can't make folders.
- Exact and complete; never vague about behaviour. Never "Done when: tests pass"; write "Must cover: ...".
- Reviewer task: the checklist is in its rules. Add 2-4 questions about the riskiest part, paste the spec section
  and the diff, and `--include` each changed file.

## Knowledge map (`knowledge/`)
Small linked notes instead of reading the codebase: `knowledge/README.md` → topic index → the one note you need.
- Before planning a piece, read its system note: it names the files to `--include`. Give workers that note too.
- After a piece is committed, its system note must match: new or changed via a scribe (`--files <the note>
  --include <the note> <changed files>`, template in `templates/notes/`); you fact-check every path and add the
  index line. A lesson → a finding note, a real choice → a decision note. One thing per note, never append.
- `python agent/check_docs.py` before every commit (broken links, orphans, missing files). Tiny projects may skip.

## Review (always, by you)
- Summary first: `files:` (with +/- line counts), `NOT IN --files`, and `check:`, which the script really ran: trust
  it over the worker's report. Check `git status` even after a timeout.
- Run `check_all` from `PROJECT.md`. If it is empty, read the result against the spec yourself.
- Worker tests can be wrong: decide whether the test or the work is wrong.
- Reviewer findings are often wrong: verify each before acting. Fact-check every number a scribe writes.
- Bad result: retry once with a sharper task or `builderplus`, then do it yourself.
- Tell the user which roles/models did what and what you changed after review.

## Keeping the setup up to date
- New measurement or lesson about the setup → a new note in `agent/notes/` + one line in its `README.md`.
- A model gets better/worse → `agent/MODELS.md` and the `models:` lines of the affected roles. A new model, provider
  or limit → `agent/models.json`. Rate models with `python agent/bench.py --workers <worker> ...`.
- User adds a tool or key → run `python agent/check_setup.py` again.
- Tests for the scripts: `python -m unittest discover -s agent/tests`.

## Git
- No AI attribution in commits, pushes or PRs: no `Co-Authored-By: Claude` lines, no "Generated with Claude Code".

## Gotchas
- agy loads `AGENTS.md` / `GEMINI.md` into every worker as rules: never create them here.
- The check command runs outside the worker sandbox, as you: it comes from `PROJECT.md`, never from a worker.
- `python3` if `python` is missing (also inside `check_one`). `agy` or `copilot` not found right after installing:
  the PATH is stale, tell the user to restart the app.
- Never `--dangerously-skip-permissions`, never agy `--mode plan` for workers.
