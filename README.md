# Multi-Agent: a template for working with an AI team

Status: 2026-11-13. A starting folder for any project (code, writing, data, research) where Claude leads
and cheaper AI models do the work. Clone it for each new project.

## The idea in one paragraph

Claude (in Claude Code) is the **boss**. It is smart but expensive, so it does the thinking: plan the
work, write exact instructions, check every result, and save the work in git.
The actual making (code, text, data) is handed to **cheap or free AI models** ("workers"). Workers are fast
but make mistakes, so nothing they do is trusted until Claude has checked it.

## Starting a new project with this template

1. Make an empty repo on GitHub for the new project, then:
   ```bash
   git clone https://github.com/rookmelder/Multi-Agent.git my-project
   ```
   ```bash
   cd my-project
   ```
   ```bash
   git remote set-url origin https://github.com/<you>/my-project.git
   ```
2. Copy `.env.example` to `.env` and fill in your API keys (never committed).
3. Open the folder in Claude Code and tell it what you want to make. Claude sees that `PROJECT.md`
   isn't filled in yet, asks you a few questions, and fills it in: what the project is, whether it's
   private, and how to check the work (for code: the test commands).
4. Put the project's files inside this folder (e.g. `src/`, `docs/`). Then just ask Claude for work.

**What you need installed:** git, PowerShell, agy (Google Antigravity CLI, with a Google AI Pro plan),
GitHub Copilot CLI, and optionally Ollama for private work.

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
| **Builder** | Make one piece from a spec: code + its tests, a text section, a data file | Gemini 3.8 Flash Low (agy) | Mistral Large 4 (NVIDIA) | Nemotron Ultra (OpenRouter, free) |
| **Builder+** | Bigger or trickier changes over several files | Gemini 3.8 Flash Medium (agy) | Mistral Large 4 (NVIDIA) | Nemotron Ultra (OpenRouter, free) |
| **Tester** | Write tests/checks for work it did *not* make | Gemini Flash-Lite (Gemini API) | Mistral Large 4 (NVIDIA) | Nemotron Ultra (OpenRouter, free) |
| **Reviewer** | Read a change and look for mistakes (never edits) | Claude Sonnet 4.6 (agy) | o3 (OpenAI, free tokens) | gpt-oss-120b (agy) |
| **Scribe** | Write docs like README (only `.md` files) | Nemotron Lightning (NVIDIA) | Gemini 3.8 Flash Low (agy) | Nemotron Ultra (OpenRouter, free) |
| **Private** | Work that must never leave this PC | Gemma 4 on this PC (Ollama, slow, text only) | Claude does it itself | |

Why this order: quality first, then free before paid, then speed. All of it is free or already paid for
(your subscriptions). The full list of every model, with ratings, is in `MODELS.md`.

Why the Tester and Reviewer use a different model from the Builder: a model tends to miss its own
kind of mistakes. A different "family" catches different bugs.

Why OpenAI is only used as a Reviewer: in our test, OpenAI's small models kept trying to write files in
a way the safety sandbox blocks, and one got stuck in a loop. Reviewing only needs reading, so there it works.

## What happens when Claude hands out a job

1. Claude writes a short task: the goal, which files, the exact spec, what must be covered.
2. `delegate.ps1` glues the **role rules** (`roles/builder.md` etc.) and the **project notes** from
   `PROJECT.md` in front of it, and pastes in any files the worker needs (so it doesn't waste steps opening them).
3. It starts the worker with **only the tools that role needs** (a reviewer can't write, a scribe can only
   write `.md` files) inside a **sandbox** (it can't touch things outside this project, can't use git,
   can't browse the web). The only command a worker may run is the project's check command from
   `PROJECT.md`; projects without one (writing, research) get no commands at all.
4. The worker does its piece, checks it, and ends with a 4-line report:
   what it changed, what it did, how it checked, what it was unsure about.
5. Claude checks: did it only touch the allowed files? Does the **full** check pass? Is the work right?
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

Measured on the same coding job (checked with hidden tests), before vs after these savings:
- agy Gemini Flash Low: same quality (2/2 correct), **38% fewer tokens**.
- NVIDIA Mistral: same quality (2/2 correct), **59% fewer tokens**, about 35% faster, no more timeouts.

## The files

| File / folder | What it is |
|---|---|
| `CLAUDE.md` | The rulebook Claude reads at the start of every session. |
| `PROJECT.md` | The one file you change per project: what it is, how to check the work, notes for workers. |
| `README.md` | This explanation. |
| `MODELS.md` | Every model you can use: cost, limits, rating, and the pick order per role. |
| `NOTES.md` | Measurements and lessons learned (starts empty; rules at the top). |
| `delegate.ps1` | The script that runs a worker. |
| `roles/*.md` | The short rules each kind of worker gets. |
| `.agents/agents/w-*.md` | Trimmed tool sets for agy workers. Your own agy chats are not affected. |
| `.env` | Your API keys. **Never shared, never committed to git.** |
| `.env.example` | Empty template that says where to get each key. |
| `.worker/` | Scratch space for tasks, results and the OpenAI usage counter (not saved in git). |

## Running a worker yourself

```bash
powershell -NoProfile -File delegate.ps1 -Role builder -TaskFile .worker/t1.md -Include src/shop.py
```

- `-Role` picks the job: builder, builderplus, tester, reviewer, scribe.
- `-Include` pastes files into the task.
- `-Model` (an agy model) or `-Provider` + `-ProviderModel` force one specific worker (no fallback).
- `-Local` uses Ollama on this PC (private).
- Result codes: 0 OK, 2 no task given, 3 took too long, 4 tried something forbidden, 5 empty answer, 6 provider problem.

## Known weak spots

- Workers make mistakes; that's expected, Claude checks everything.
- agy is sometimes busy ("no capacity"); the script then moves on to the next worker.
- The OpenAI count is an estimate, and one call can push slightly past the line.
- If a file called `AGENTS.md` or `GEMINI.md` is in this folder, agy loads it into **every** worker as
  extra rules. Keep worker instructions in `roles/` instead.
