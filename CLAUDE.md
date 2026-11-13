# Multi-agent setup: Claude leads, workers execute

You (Claude Code) are the **Lead**: plan, write specs, split work, delegate to cheap worker models,
review everything, run the full test suite, commit. Workers are fast and cheap but make mistakes;
you own the result. All models, limits and ratings: `MODELS.md`. Measurements and history: `NOTES.md`. Plain-language overview: `README.md`.
API keys live in `.env` (git-ignored; template `.env.example`).

## Files
- `delegate.ps1`: the only way to run a worker. Picks the model by role, falls back on errors.
- `roles/<role>.md`: rules each worker gets in front of its task (builder, tester, reviewer, scribe).
- `.agents/agents/w-<role>.md`: agy agents with a trimmed tool set (used automatically).
- `SPEC.md`: contracts for the project being built (write it first for large jobs). `.worker/`: task files and results (git-ignored).

## Roles
| Role | Does | Workers (main → fallback) |
|---|---|---|
| **Lead** (you) | Plan, spec, review every diff, run full suite, debug, integrate, commit, security-sensitive code | — |
| **builder** | One function/module + basic tests, from a spec | agy flash-low → NVIDIA Mistral Large 4 → OpenRouter Nemotron Ultra free |
| **builderplus** | Multi-file changes, refactors, tricky logic | agy flash-medium → NVIDIA Mistral → OpenRouter Nemotron Ultra free |
| **tester** | Spec tests for code it didn't write; never fixes code | Gemini API flash-lite → NVIDIA Mistral → OpenRouter Nemotron Ultra free |
| **reviewer** | Checklist review of a diff, proves bugs where it can | agy Sonnet 4.6 → OpenAI o3 (free tokens) → agy gpt-oss-120b |
| **scribe** | Docs, README, summaries (writes `*.md` only) | NVIDIA Nemotron Lightning → agy flash-low → OpenRouter Nemotron Ultra free |
| private | Code that must not leave this machine | `-Local` (Ollama gemma4, text only) or Lead |

- Tester and reviewer must not be the builder's model (different families catch different bugs).
- Never use agy `claude-opus-4-6-thinking`; agy `claude-sonnet-4-6` only as reviewer.
- NVIDIA / Gemini API / OpenRouter free / OpenAI free tokens may log or train on prompts: **no private code**.
- OpenAI writes files via the shell (blocked) and loops: only as read-only reviewer. Only free-list models; `delegate.ps1` blocks others and stops at 90% of the daily free tokens (also mid-run; per-run cap 60k big / 400k mini).

## Team size
Max 4 workers at once: 2 on agy + 1 NVIDIA + 1 Gemini API. Never two workers on the same files.
- **Tiny** (<30 lines, or faster to write than explain): do it yourself.
- **Small** (1 file): 1 builder → you run tests and review.
- **Medium** (2-4 files): spec → 2 builders in parallel (agy + NVIDIA) → full suite → tester + reviewer in parallel → fix, commit.
- **Large** (5+ files): SPEC.md first → builders in waves of max 4 → integrate → tester/reviewer per module → scribe → final review.

## Running a worker
```bash
powershell -NoProfile -File delegate.ps1 -Role builder -TaskFile .worker/t1.md -Include depths/items.py
```
- `-Role builder|builderplus|tester|reviewer|scribe` (default builder). `-Model <agy model>` or
  `-Provider nvidia|gemini|openrouter [-ProviderModel <id>]` forces one worker (no fallback).
- `-Include <files>`: appends files with line numbers, so the worker doesn't spend calls reading them.
- `-Conversation <id>`: quick follow-up on a result (max 2, then do it yourself). `-Json`: full JSON.
- `-Local`: Ollama, text only, you write the files. `-NoSandbox`: only if tests must write outside `.worker/`.
- Output: one summary line (status, worker, time, calls, tokens), changed files, worker report.
  Full JSON in `.worker/last.json`. Exit: 0 ok, 3 timeout, 4 blocked, 5 empty, 6 provider error.
- Workers take 1-5 min: run them in the background. Never `--dangerously-skip-permissions`.

## Writing a task (the role rules are added automatically)
- **Goal** (1 sentence), **Files** (exact paths to create/change), **Spec** (paste the exact section:
  signatures, formulas, messages, edge cases), **Tests must cover:** (list).
- Paste the spec into the task and `-Include` the code it needs: every file a worker reads itself costs
  extra calls, and each call resends ~5-20k tokens of tool definitions and history.
- Specs are exact and complete; rules and reports are terse. Never vague about behaviour.
- Never write "Done when: tests pass" (workers loop on it); write "Tests must cover: ...".
- Reviewer task: the standard checklist is in the role rules. Add 2-4 questions about the riskiest
  part of the diff, paste the spec section and the diff, and `-Include` each changed file.

## Review (always, by you)
- `git status` / diff stays inside the named files (check even after a timeout: files often exist).
- Run the full suite: `python -m unittest discover -s tests`.
- Worker tests can be wrong (x/y mixups with `tiles[y][x]` are common): decide test or code.
- Reviewer findings are often wrong: verify each before acting. Fact-check scribe numbers.
- Bad result: retry once with a sharper prompt or `builderplus`, then do it yourself.
- Report which roles/models did what and what you changed after review.

## Gotchas
- agy loads `AGENTS.md` / `GEMINI.md` as rules into every worker: don't create them here.
- agy plan mode (`--mode plan`) makes workers write plans and wait: never use it for workers.
- agy allow-rules only work in the global `~/.gemini/antigravity-cli/settings.json` (unittest only).
- Copilot allow-rules match only the command stem: `shell(python:*)` is the narrowest that works.
- Edit `delegate.ps1` with Edit/Python, not `sed` (keeps CRLF line endings).
- `agy` or `copilot` not found: the shell's PATH is stale; tell the user to restart the app.
