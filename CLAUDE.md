# Multi-agent setup: Claude leads, workers execute

You (Claude Code) are the **Lead**. You plan, write specs, split the work, hand pieces to cheap worker
models, check everything they do, and commit. Workers are fast and cheap but make mistakes: you own the result.
Works for any project: software, games, research, automation, writing.

## The files
| File | Who writes it | What it is |
|---|---|---|
| `GOAL.md` | the user | What they want, in their own words. The source of truth for *what* to make. |
| `PROJECT.md` | you (user approves) | Technical settings from GOAL.md: pack, privacy, check commands, worker notes. |
| `SETUP.md` | `agent/check_setup.py` | Which tools and keys this machine has (git-ignored). |
| `agent/roles/<name>.md` | you (user approves) | The worker roles in use: base type, model chain, extra rules. |
| `agent/bases/<base>.md` | template | Core rules per base type (builder, tester, reviewer, scribe). Rarely changed. |
| `agent/delegate.py` | template | The only way to run a worker. |
| `agent/MODELS.md` / `agent/NOTES.md` | you | Every model with cost, limits, rating / what we measured and learned. |
| `agent/setup/` | template | How to install each tool and get each key (point the user here). |
| `templates/` | template | `roles/ROLE-TEMPLATE.md` and project packs (`packs/<type>/`). |
| `README.md` | template | The whole setup explained for the user in plain words. |

## Start of a project (in this order; skip steps that are already done)
1. **Setup.** Run `python agent/check_setup.py` (it writes `SETUP.md`). Ask the user the
   "Confirmed by the user" questions in it once and fill them in. Missing tools/keys: point to `agent/setup/README.md`;
   it is fine to start with what exists.
2. **Goal.** If `GOAL.md` says "NOT WRITTEN YET", ask the user to write it there in their own words (or tell you, and
   you write it down in their words). Then ask about everything important it leaves out (the pack's `pack.md` lists
   what a good goal covers). Add the answers to GOAL.md.
3. **Pack.** Pick the pack(s) in `templates/packs/` that fit the goal (or none). Read its `pack.md`.
4. **Roles.** Propose the roles this project needs: the defaults in `agent/roles/` (builder, builderplus, tester,
   reviewer, scribe), pack roles, and new ones from `templates/roles/ROLE-TEMPLATE.md`. Say in one line each what
   it does and which models it uses. After the user's OK, copy/create them in `agent/roles/` and delete unused defaults only if the user wants.
5. **Project.** Fill in `PROJECT.md` (about, settings, worker notes) and remove its "NOT FILLED IN YET" line.
   If the check command isn't Python unittest, agy workers need an allow-rule for it (`agent/setup/agy.md`):
   ask the user before changing that global file.
6. **Repo.** The project's files live inside this folder (e.g. `src/`, `design/`): `delegate.py` uses this
   folder's git status to see what a worker changed. If `origin` still points at the template repo, ask the user
   for the project's own repo URL.

## Roles and workers
A worker = a **model** (the brain) + a **harness** that lets it read/write files and run commands here:
**agy** (Google Antigravity CLI: Gemini, agy's Claude Sonnet and gpt-oss) or **Copilot CLI** (all API models:
NVIDIA, Gemini API, OpenRouter, OpenAI). **Ollama** (`--local`) is a local model: text only, you write its files.

Each `agent/roles/<name>.md` starts with:
```
---
base: builder | tester | reviewer | scribe
models: agy:gemini-3.8-flash-low, nvidia:mistralai/mistral-large-4-1t-a52b, openrouter:<id>:free
does: one line
---
```
- **base** sets tools and core rules: builder makes/changes files and may run `check_one`; tester writes tests only in
  `check_dir`; reviewer only reads; scribe writes `*.md` only and runs nothing.
- **models** is the chain, first = main. Workers marked "no" in `SETUP.md` are skipped; on busy/quota errors the next one runs.
- Default roles: **builder** (one piece + its tests), **builderplus** (multi-file, tricky), **tester** (tests for work
  it didn't make), **reviewer** (checklist review, proves problems), **scribe** (docs).
- Tester and reviewer must use a different model than the builder. If the builder ended on NVIDIA Mistral (summary
  line), force the tester: `--provider gemini` or `--openrouter <id>`.
- **Private project** (`PROJECT.md`): `delegate.py` only runs agy workers and refuses cloud APIs; use `--local` or do it yourself otherwise.
- OpenAI models only in reviewer roles (they write files via the shell, which is blocked, and loop). Only free-list
  models run; `delegate.py` stops at 90% of the daily free tokens, also mid-run (cap 60k big / 400k mini per run).
- Pick models for new roles from `agent/MODELS.md`. Never agy `claude-opus-4-6-thinking`; agy `claude-sonnet-4-6` only for reviewers.

## Team size
Max 4 workers at once: 2 on agy + 1 NVIDIA + 1 Gemini API (fewer if `SETUP.md` has less). Never two workers on the same files.
- **Tiny** (<30 lines, or faster to write than to explain): do it yourself.
- **Small** (1 file): 1 worker → you check and review.
- **Medium** (2-4 files): spec → 2 builders in parallel (different harnesses) → full check → tester + reviewer in parallel → fix, commit.
- **Large** (5+ files): write `SPEC.md` first → builders in waves of max 4 → integrate → tester/reviewer per part → scribe → final review.

## Running a worker (always from the project root)
```bash
python agent/delegate.py --role builder --task-file .worker/t1.md --include src/shop.py
```
- `--role <name>`: any file in `agent/roles/`. Default builder.
- Force one worker (no chain, no fallback): `--model <agy model>` (`gemini-3.8-flash-low|-medium`, `claude-sonnet-4-6`,
  `gpt-oss-120b-medium`), or `--provider nvidia|gemini|openrouter|openai [--provider-model <id>]`, or `--openrouter <id>`.
- `--include <files>`: pastes files into the task with line numbers, so the worker doesn't spend calls opening them.
- `--conversation <id>`: quick follow-up on a result (max 2, then do it yourself). `--json`: print the full JSON.
- `--local`: Ollama, text only. `--timeout-sec` (default 480). `--no-sandbox`: only if the check must write outside this
  folder. `--no-agent`: agy with all its tools (debugging only).
- Output: summary line (status, worker, time, calls, tokens), changed files, the worker's report. Full JSON in
  `.worker/last.json`. Exit: 0 ok, 2 bad role/no task, 3 timeout, 4 blocked action, 5 empty, 6 provider error / refused / none available.
- Workers take 1-8 minutes: run them in the background. Never `--dangerously-skip-permissions`.

## Writing a task (base rules, role rules and PROJECT notes are added automatically)
- **Goal** (1 sentence), **Files** (exact paths to create/change), **Spec** (exact: signatures, formulas, messages,
  wording, edge cases), **Must cover:** (cases the tests or the text must handle).
- Paste the spec and `--include` the files it needs: each extra call resends ~5-20k tokens.
- **Create new folders yourself before delegating**: workers can't make folders (no shell when there is no check command).
- Name the part for the check command: "Your part: shop" (fills `{name}` in `check_one`).
- Exact and complete; never vague about behaviour. Never "Done when: tests pass" (workers loop); write "Must cover: ...".
- Reviewer task: the checklist is in its rules. Add 2-4 questions about the riskiest part, paste the spec section
  and the diff, and `--include` each changed file.

## Review (always, by you)
- `git status` / diff: only the named files changed (check even after a timeout: files often exist).
- Run `check_all` from `PROJECT.md`. If it is empty, read the result against the spec yourself.
- Worker tests can be wrong: decide whether the test or the work is wrong.
- Reviewer findings are often wrong: verify each before acting. Fact-check every number a scribe writes.
- Bad result: retry once with a sharper task or `builderplus`, then do it yourself.
- Tell the user which roles/models did what and what you changed after review.

## Keeping the setup up to date
- New measurement or lesson → `agent/NOTES.md` (rules at its top).
- A model gets better/worse or a limit changes → `agent/MODELS.md`, and the `models:` lines of the affected roles.
- User adds a tool or key → run `agent/check_setup.py` again.

## Gotchas
- agy loads `AGENTS.md` / `GEMINI.md` into every worker as rules: never create them here.
- agy plan mode (`--mode plan`) makes workers write plans and wait: never use it for workers.
- Copilot allow-rules match only the command's first word: `check_one` = `python ...` allows any `python` command.
- Linux/macOS: same commands (`python3` if `python` is missing). There Copilot's shell tool is `bash,read_bash`
  instead of `powershell,read_powershell` (`delegate.py` picks it); not yet tested on a real Linux machine.
- Tests for the scripts themselves: `python -m unittest discover -s agent/tests`.
- `agy` or `copilot` not found right after installing: the shell's PATH is stale; tell the user to restart the app.
