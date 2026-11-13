# The agent setup, in plain words

Status: 2026-11-13. This explains how the AI team in this folder works. (The Depths game is just
the project the team practises on; it is not explained here.)

## The idea in one paragraph

Claude (in Claude Code) is the **boss**. It is smart but expensive, so it does the thinking: plan the
work, write exact instructions, check every result, run all the tests, and save the work in git.
The actual typing of code is handed to **cheap or free AI models** ("workers"). Workers are fast but make
mistakes, so nothing they do is trusted until Claude has checked it.

## The pieces

| Piece | What it is, simply |
|---|---|
| **Lead** | Claude. Plans, checks, decides, commits. |
| **Worker** | A cheaper AI model that does one small, clearly described job. |
| **Model** | The "brain" (Gemini, Mistral, o3, ...). On its own a model can only send back text. |
| **Harness** | The program that lets a model actually *do* things on this PC: open files, write files, run tests. We use two: **agy** (Google's Antigravity CLI) and **Copilot CLI**. The harness asks the model what it wants to do, checks if that's allowed, and then does it. |
| **Provider** | The company whose computers run the model: Google (agy and Gemini API), NVIDIA, OpenAI, OpenRouter, or Ollama on this PC. |
| **`delegate.ps1`** | The one script that sends a job to a worker. It picks the model, adds the rules, runs it safely, and reports back. |

So one worker = **a model** (the brain) **+ a harness** (the hands) **+ a provider** (where the brain runs).

## The roles (who does what)

Each role has a list of workers. The first one is used; if it is busy or out of quota, the script
automatically tries the next one.

| Role | Job | 1st choice | 2nd | 3rd |
|---|---|---|---|---|
| **Builder** | Write one piece of code + its tests | Gemini 3.8 Flash Low (agy) | Mistral Large 4 (NVIDIA) | Nemotron Ultra (OpenRouter, free) |
| **Builder+** | Bigger or trickier changes over several files | Gemini 3.8 Flash Medium (agy) | Mistral Large 4 (NVIDIA) | Nemotron Ultra (OpenRouter, free) |
| **Tester** | Write extra tests for code it did *not* write | Gemini Flash-Lite (Gemini API) | Mistral Large 4 (NVIDIA) | Nemotron Ultra (OpenRouter, free) |
| **Reviewer** | Read a change and look for bugs (never edits) | Claude Sonnet 4.6 (agy) | o3 (OpenAI, free tokens) | gpt-oss-120b (agy) |
| **Scribe** | Write docs like README (only `.md` files) | Nemotron Lightning (NVIDIA) | Gemini 3.8 Flash Low (agy) | Nemotron Ultra (OpenRouter, free) |
| **Private** | Code that must never leave this PC | Gemma 4 on this PC (Ollama, slow, text only) | Claude does it itself | |

Why this order: quality first, then free before paid, then speed. All of it is free or already paid for
(your subscriptions). The full list of every model, with ratings, is in `MODELS.md`.

Why the Tester and Reviewer use a different model from the Builder: a model tends to miss its own
kind of mistakes. A different "family" catches different bugs.

Why OpenAI is only used as a Reviewer: in our test, OpenAI's small models kept trying to write files in
a way the safety sandbox blocks, and one got stuck in a loop. Reviewing only needs reading, so there it works.

## What happens when Claude hands out a job

1. Claude writes a short task: the goal, which files, the exact spec, what the tests must cover.
2. `delegate.ps1` glues the **role rules** (`roles/builder.md` etc.) in front of it, and pastes in any
   files the worker needs (so it doesn't waste steps opening them).
3. It starts the worker with **only the tools that role needs** (a reviewer can't write, a scribe can only
   write `.md` files) inside a **sandbox** (it can't touch things outside this project, can't use git,
   can't browse the web).
4. The worker writes code, runs only its own tests, and ends with a 4-line report:
   what it changed, what it did, test results, what it was unsure about.
5. Claude checks: did it only touch the allowed files? Does the **whole** test suite pass? Is the code right?
   Then Claude fixes, asks a reviewer, and commits.

## How big jobs are split

| Job size | What happens |
|---|---|
| Tiny (under ~30 lines) | Claude just does it; explaining would take longer. |
| Small (1 file) | 1 builder, then Claude checks. |
| Medium (2-4 files) | 2 builders at the same time on different files, then a tester and a reviewer, then Claude fixes and commits. |
| Large (5+ files) | Claude writes the spec first, builders work in waves (max 4 at once), then testers/reviewers, a scribe for docs, and a final check. |

At most 4 workers run at once, and never two on the same file.

## Money and limits (so nothing surprises you)

| Provider | Costs you | Free limit | Notes |
|---|---|---|---|
| Claude | Your Claude plan | Plan limits | Kept for thinking and checking. |
| agy (Google AI Pro) | Your Pro subscription | Resets every 5 hours | ~20 worker jobs use about 6-7% of one window. |
| NVIDIA | Nothing | Roughly 40 requests a minute | Slow when busy. |
| Gemini API | Nothing | ~500 requests a day | Free tier may train on your prompts. |
| OpenRouter | $11 credit, **untouched** by free models | 1000 free requests a day | We only use its free models. |
| OpenAI | $5 credit (expires after a year) | 250k tokens/day big models, 2.5M/day small models | `delegate.ps1` protects the $5, see below. |
| Ollama | Nothing | Unlimited | Runs on this PC; slow on 8 GB of video memory. |

**The OpenAI guard** (so the $5 is never touched):
- Models that aren't in the free group are refused.
- It keeps a daily count and stops at 90% of the free amount.
- It also watches *during* a job and stops it if it is about to cross that line, or if one job alone uses too
  much (60k tokens for big models, 400k for small ones). It can still go a little over in rare cases
  (one call can suddenly get big), which is why the 10% safety margin exists.
- The count is our own estimate; OpenAI's dashboard (platform.openai.com > Usage) is the real number.

**Privacy:** the free tiers of NVIDIA, Gemini API, OpenRouter and OpenAI may read or train on what you send.
Private code goes only to Claude, agy or Ollama.

## Keeping tokens (and so cost and speed) down

Every time a model takes a step, the whole conversation so far is sent again (the API has no memory).
So we keep each step small:
- Workers get only the tools their role needs (a step went from ~18.7k to ~2.6k tokens on Copilot, and
  from ~10.4k to ~3.6k on agy).
- Role rules are short and to the point.
- Needed files are pasted in up front, so the worker doesn't spend steps opening them.
- Workers run only their own tests, not the whole suite.

Measured on the same job (adding a shop module, checked with hidden tests), before vs after:
- agy Gemini Flash Low: same quality (2/2 correct), **38% fewer tokens**.
- NVIDIA Mistral: same quality (2/2 correct), **59% fewer tokens**, about 35% faster, no more timeouts.

## The files

| File / folder | What it is |
|---|---|
| `CLAUDE.md` | The rulebook Claude reads at the start of every session. |
| `README.md` | This explanation. |
| `MODELS.md` | Every model you can use: cost, limits, rating, and the pick order per role. |
| `NOTES.md` | History: measurements, benchmarks, lessons learned. |
| `delegate.ps1` | The script that runs a worker. |
| `roles/*.md` | The short rules each kind of worker gets. |
| `.agents/agents/w-*.md` | Trimmed tool sets for agy workers. Your own agy chats are not affected. |
| `.env` | Your API keys. **Never shared, never committed to git.** |
| `.env.example` | Empty template that says where to get each key. |
| `.worker/` | Scratch space for tasks, results and the OpenAI usage counter (not saved in git). |

## Running a worker yourself

```bash
powershell -NoProfile -File delegate.ps1 -Role builder -TaskFile .worker/t1.md -Include depths/items.py
```

- `-Role` picks the job: builder, builderplus, tester, reviewer, scribe.
- `-Include` pastes files into the task.
- `-Model` (an agy model) or `-Provider` + `-ProviderModel` force one specific worker (no fallback).
- `-Local` uses Ollama on this PC (private).
- Result codes: 0 OK, 3 took too long, 4 tried something forbidden, 5 empty answer, 6 provider problem.

## Known weak spots

- Workers make mistakes; that's expected, Claude checks everything.
- agy is sometimes busy ("no capacity"); the script then moves on to the next worker.
- The OpenAI count is an estimate, and one call can push slightly past the line.
- If a file called `AGENTS.md` or `GEMINI.md` is in this folder, agy loads it into **every** worker as
  extra rules. Keep worker instructions in `roles/` instead.
