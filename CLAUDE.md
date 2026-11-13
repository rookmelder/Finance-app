# Multi-agent setup: Claude leads, workers execute

You (Claude Code) are the **Lead**. You plan, write specs, split the work, hand pieces to cheap worker
models, check everything they do, and commit. Workers are fast and cheap but make mistakes: you own the result.
This works for any project: code, writing, data or research.

- `PROJECT.md`: what this project is, its check commands, notes for workers. **Read it first.**
- `MODELS.md`: every model, its cost, limits, rating and pick order. `NOTES.md`: what we measured and learned.
- `README.md`: the same setup explained for the user in plain words.

## Start of a project (do this first if `PROJECT.md` says "NOT FILLED IN YET")
1. Ask the user what the project is, then fill in `PROJECT.md` (about, settings, worker notes). Remove the "NOT FILLED IN YET" line.
2. Check the tools exist: `git`, `agy`, `copilot`, `powershell`; optional `ollama`. If `agy`/`copilot` are missing,
   tell the user: installed but not found = restart the app.
3. Check `.env` exists with keys (template: `.env.example`). Never print key values.
4. If `check_one` is not Python unittest: agy workers may only run commands allowed in the global
   `~/.gemini/antigravity-cli/settings.json` (`permissions.allow`, e.g. `command(regex:^npx vitest run tests/[a-z_]+\.test\.js$)`).
   Ask the user before adding a rule there (it is global, for all their agy use).
5. The project's files live **inside this folder** (e.g. `src/`, `docs/`): `delegate.ps1` uses this folder's git
   status to see what a worker changed. This folder must be a git repo; give it the project's own remote
   (a clone of the template still points at the template repo).

## How a worker runs
A worker = a **model** (the brain) + a **harness** (the program that lets it read/write files and run commands here).
- **agy** (Google Antigravity CLI): runs Gemini models and agy's Claude Sonnet / gpt-oss. Uses the user's Google AI Pro quota.
- **Copilot CLI** (GitHub): runs every API model: NVIDIA, Gemini API, OpenRouter, OpenAI. Keys from `.env`.
- **Ollama** (`-Local`): a model on this PC. Text only: you write its files yourself.

`delegate.ps1` is the only way to start a worker. It adds the role rules (`roles/<role>.md`), the PROJECT settings
and notes, limits the worker's tools to what its role needs, runs it in a sandbox (no git, no web, only this folder),
and falls back to the next worker when one is busy or out of quota.

## Roles
| Role | Does | Workers (main → fallbacks) |
|---|---|---|
| **Lead** (you) | Plan, spec, review every change, run the full check, debug, integrate, commit, anything security-sensitive | — |
| **builder** | Makes one piece from a spec: a module + its tests, a document section, a data file | agy Gemini Flash Low → NVIDIA Mistral Large 4 → OpenRouter Nemotron Ultra (free) |
| **builderplus** | Changes over several files, refactors, tricky logic | agy Gemini Flash Medium → NVIDIA Mistral → OpenRouter Nemotron Ultra |
| **tester** | Tests/checks for work it did not make; never fixes the work | Gemini API Flash-Lite → NVIDIA Mistral → OpenRouter Nemotron Ultra |
| **reviewer** | Reviews a change against the spec, proves problems where it can; never edits | agy Claude Sonnet 4.6 → OpenAI o3 → agy gpt-oss-120b |
| **scribe** | Docs: README, guides, summaries (writes `*.md` only) | NVIDIA Nemotron Lightning → agy Gemini Flash Low → OpenRouter Nemotron Ultra |
| private | Work that must not leave this PC | `-Local` (Ollama gemma4, slow) or you |

- Tester and reviewer must use a **different model** than the builder (other families catch other mistakes).
  If the builder ended up on NVIDIA Mistral (see the summary line), force the tester: `-Provider gemini` or `-OpenRouter <id>`.
- **Private project** (`PROJECT.md`): only agy, Ollama or you. NVIDIA, Gemini API, OpenRouter and OpenAI free tiers may log or train on prompts.
- OpenAI only as reviewer: its models write files through the shell (blocked) and loop. Only free-list models run;
  `delegate.ps1` refuses others and stops at 90% of the daily free tokens, also mid-run (cap 60k big / 400k mini per run).
- Never use agy `claude-opus-4-6-thinking`; agy `claude-sonnet-4-6` only as reviewer.

## Team size
Max 4 workers at once: 2 on agy + 1 NVIDIA + 1 Gemini API. Never two workers on the same files.
- **Tiny** (<30 lines, or faster to write than to explain): do it yourself.
- **Small** (1 file): 1 builder → you check and review.
- **Medium** (2-4 files): spec → 2 builders in parallel (one agy, one `-Provider nvidia`) → full check → tester + reviewer in parallel → fix, commit.
- **Large** (5+ files): write `SPEC.md` first → builders in waves of max 4 → integrate → tester/reviewer per part → scribe → final review.

## Running a worker
```bash
powershell -NoProfile -File delegate.ps1 -Role builder -TaskFile .worker/t1.md -Include src/shop.py
```
- `-Role builder|builderplus|tester|reviewer|scribe` (default builder). Without `-Model`/`-Provider` the role's chain is used.
- Force one worker (no fallback): `-Model <agy model>` (`gemini-3.8-flash-low|-medium`, `claude-sonnet-4-6`, `gpt-oss-120b-medium`),
  or `-Provider nvidia|gemini|openrouter|openai [-ProviderModel <id>]`, or `-OpenRouter <id>` (short for openrouter).
- `-Include <files>`: pastes files into the task with line numbers, so the worker doesn't spend calls opening them.
- `-Conversation <id>`: quick follow-up on a result (max 2, then do it yourself). `-Json`: print the full JSON.
- `-Local`: Ollama, text only, you write the files. `-TimeoutSec` (default 480). `-NoSandbox`: only if the check
  must write outside this folder. `-NoAgent`: agy with all its tools (debugging only).
- Output: one summary line (status, worker, time, calls, tokens), changed files, the worker's report.
  Full JSON in `.worker/last.json`. Exit: 0 ok, 2 no task, 3 timeout, 4 blocked action, 5 empty, 6 provider error / refused.
- Workers take 1-8 minutes: run them in the background. Never `--dangerously-skip-permissions`.

## Writing a task (role rules and PROJECT notes are added automatically)
- **Goal** (1 sentence), **Files** (exact paths to create/change), **Spec** (exact: signatures, formulas, messages,
  wording, edge cases), **Must cover:** (list of cases the tests or the text must handle).
- Paste the spec into the task and `-Include` the files it needs: each extra call resends ~5-20k tokens.
- **Create new folders yourself before delegating**: workers can't make folders (no shell when there is no check command).
- Name the part for the check command: e.g. "Your part: shop" (fills `{name}` in `check_one`).
- Exact and complete; never vague about behaviour. Never "Done when: tests pass" (workers loop); write "Must cover: ...".
- Reviewer task: the checklist is in the role rules. Add 2-4 questions about the riskiest part, paste the spec
  section and the diff, and `-Include` each changed file.

## Review (always, by you)
- `git status` / diff: only the named files changed (check even after a timeout: files often exist).
- Run `check_all` from `PROJECT.md`. If it is empty, read the result against the spec yourself.
- Worker tests can be wrong: decide whether the test or the work is wrong.
- Reviewer findings are often wrong: verify each before acting. Fact-check every number a scribe writes.
- Bad result: retry once with a sharper task or `builderplus`, then do it yourself.
- Tell the user which roles/models did what and what you changed after review.

## Keeping the setup up to date
- New measurement or lesson → `NOTES.md` (rules for it are at its top).
- A model gets better/worse or a limit changes → `MODELS.md`, and the chains at the top of `delegate.ps1` if the pick order changes.

## Gotchas
- agy loads `AGENTS.md` / `GEMINI.md` into every worker as rules: never create them here.
- agy plan mode (`--mode plan`) makes workers write plans and wait: never use it for workers.
- Copilot allow-rules match only the command's first word: `check_one` = `python ...` allows any `python` command.
- Edit `delegate.ps1` with Edit/Python, not `sed` (keeps CRLF line endings).
- `agy` or `copilot` not found right after installing: the shell's PATH is stale; tell the user to restart the app.
