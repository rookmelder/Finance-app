# Models you have access to

Status 2026-11-13. The `models:` lines in `agent/roles/*.md` use the first entries that passed our tests. Prices and limits change: re-check before relying on a number.

**Role** = the one job each model fits best. **Priority** = pick order within that role:
**#1 is picked first**; go to #2 when #1 is busy, throttled or out of quota, and so on.
**Rating** = quality as a worker, out of 10. **Basis**: **T** = tested by us, **R** = reputation only (untested by us).
The order weighs quality first, then cost (free before paid), then speed and how much quota is left.

## 1. Money and limits at a glance

| Provider | What you pay | Free part | Resets | Watch out |
|---|---|---|---|---|
| **Claude** (Lead) | Your Claude plan | — | Plan limits | The expensive brain: keep it for planning and review |
| **agy** (Google AI Pro) | Pro subscription | Pro quota for Gemini, separate pool for Claude/GPT-OSS | Every 5 h + weekly cap | ~20 worker runs ≈ 6-7% of a 5 h window. Check with `/usage` in agy |
| **OpenAI** | Small prepaid credit (**expires after 1 year**) | **250k tokens/day** big models, **2.5M tokens/day** mini models, only with data sharing ON | 00:00 UTC | Other models and anything over the daily limit **bill the $5**. `delegate.py` refuses non-free models and stops at 90% of the daily limit, also mid-run; max 60k (big) / 400k (mini) tokens per run |
| **OpenRouter** | Optional credit | `:free` models: **50 requests/day**, or **1000/day** once ≥$10 credit was ever bought; 20/min | Daily | Free models never touch the credit. Paid models and routers do |
| **NVIDIA NIM** | Free | ~40 req/min, daily cap unclear | — | Free tier may log prompts. Some models 404/timeout for this account |
| **Gemini API** (AI Studio) | Free | Flash-Lite ~15 req/min, ~500 req/day | Daily | Free tier trains on prompts. Separate from agy's quota |
| **Ollama** (local) | Free | Unlimited | — | 8 GB VRAM: slow, text only. The only private option |

**Privacy:** OpenAI's free tokens require sharing your prompts with OpenAI. Free tiers of NVIDIA,
Gemini API and OpenRouter free models may log or train on prompts. **Private code: Claude, agy or Ollama only.**

**How the free OpenAI tokens count:** per day, across all models of a group together.
A typical worker task through Copilot uses ~15-100k tokens. So the big group (250k) is ~3-10 tasks a day,
the mini group (2.5M) ~25-150 tasks a day. The counter is in `.worker/openai-usage.jsonl` (our estimate;
OpenAI's dashboard is the truth: platform.openai.com > Usage).

## 2. Pick order per role

### Lead (plans, specs, reviews every result)
| Priority | Model | Where | Cost | Rating | Basis |
|---|---|---|---|---|---|
| #1 | Claude Opus 5.5 | Claude Code | Claude plan | 10 | T |

### Builder (one module/function + tests, from a spec)
| Priority | Model | Where | Cost | Rating | Basis | Why this spot |
|---|---|---|---|---|---|---|
| #1 | `gemini-3.8-flash-low` | agy | Pro quota | 8 | T | 11/11 on the benchmark, ~40k tokens/task, but slow (400-460 s) |
| #2 | `mistralai/mistral-large-4-1t-a52b` | NVIDIA | free | 8 | T | 2/2 correct in the 2nd benchmark, 7 calls, ~70k tokens, 250-300 s |
| #4 | `gpt-5.4-mini` | OpenAI | free (2.5M/day) | 3 | T | 0/4 via Copilot: writes files through the shell (blocked), ignores the rule |
| #5 | `gpt-5-mini` | OpenAI | free (2.5M/day, shared with #4) | 3 | R | Same family habit as #4 expected; untested |
| #3 | `nvidia/nemotron-3-ultra-550b-a55b:free` | OpenRouter | free (1000 req/day) | 6 | T | Correct, but ~60 requests per task |
| #6 | `cohere/north-mini-code:free` | OpenRouter | free | 5 | R | Code-tuned, small |
| #7 | `anthropic/claude-haiku-5.5` | OpenRouter | ~$0.012/task | 7 | R | Paid: only when all free options are throttled |
| #8 | `deepseek/deepseek-v4-flash` | OpenRouter | ~$0.010/task | 6 | R | Paid backup |
| #9 | `qwen/qwen3-coder-next` | OpenRouter | ~$0.015/task | 6 | R | Paid backup |

### Builder+ (multi-file changes, refactors, tricky logic)
| Priority | Model | Where | Cost | Rating | Basis | Why this spot |
|---|---|---|---|---|---|---|
| #1 | `gemini-3.8-flash-medium` | agy | Pro quota | 7 | T | More thinking than low, no overthinking |
| #2 | `mistralai/mistral-large-4-1t-a52b` | NVIDIA | free | 8 | T | 2/2 on the builder benchmark |
| #3 | `nvidia/nemotron-3-ultra-550b-a55b:free` | OpenRouter | free (1000 req/day) | 6 | T | Correct, but many requests |
| #4 | `gpt-5.4` | OpenAI | free (250k/day: ~3-10 tasks) | 5 | R | GPT family wrote files via the shell in our test: not in the chain |
| #5 | `gpt-5.2` | OpenAI | free (250k/day, shared with #4) | 5 | R | Same budget and habit |
| #6 | `gpt-4.1` | OpenAI | free (250k/day, shared) | 5 | R | Same budget, older |
| #7 | `openrouter/pareto-code` | OpenRouter | paid, varies | 7 | R | Router that picks a top coding model; for a rare hard task |

### Tester (spec tests for code it didn't write)
| Priority | Model | Where | Cost | Rating | Basis | Why this spot |
|---|---|---|---|---|---|---|
| #1 | `gemini-flash-lite-latest` | Gemini API | free (~500 req/day) | 7 | T | Good edge-case tests, own quota |
| #2 | `gpt-5.4-nano` | OpenAI | free (2.5M/day) | 4 | T | Fine on tiny tasks; on the builder benchmark it looped (25-47 calls, 0.6-1.4M tokens per task) |
| #3 | `gpt-4.1-mini` | OpenAI | free (2.5M/day, shared) | 6 | R | Same budget |
| #4 | `o4-mini` | OpenAI | free (2.5M/day, shared) | 6 | R | Reasoning model: good at edge cases, slower |
| #5 | `nvidia/nemotron-3.5-lightning:free` | OpenRouter | free | 5 | R | Fast, simple tests |

### Reviewer (checklist review of a diff; must differ from the builder's model)
| Priority | Model | Where | Cost | Rating | Basis | Why this spot |
|---|---|---|---|---|---|---|
| #1 | `claude-sonnet-4-6` | agy | agy Claude/GPT pool | 8 | T | Fewest false findings in our benchmark |
| #2 | `o3` | OpenAI | free (250k/day) | 8 | T | Strong reasoner, different family; read-only so no shell-write problem; smoke-tested OK |
| #3 | `gpt-5.1` | OpenAI | free (250k/day, shared) | 7 | R | Different family |
| #4 | `gpt-oss-120b-medium` | agy | agy Claude/GPT pool | 5 | T | Checklist only; makes things up otherwise; sometimes 503 |
| #5 | `openrouter/fusion` | OpenRouter | paid, several models per call | 7 | R | Panel of models: only for big, important diffs |
| #6 | `o1` | OpenAI | free (250k/day, shared) | 6 | R | Older reasoner |
| #7 | `gpt-4o` | OpenAI | free (250k/day, shared) | 5 | R | Older |

### Scribe (docs, README, summaries; Lead fact-checks)
| Priority | Model | Where | Cost | Rating | Basis | Why this spot |
|---|---|---|---|---|---|---|
| #1 | `nvidia/nemotron-3.5-lightning-30b-a3b` | NVIDIA | free | 5 | T | Fast; good text, but check numbers (3 errors in one README) |
| #2 | `gpt-5-nano` | OpenAI | free (2.5M/day) | 6 | R | Cheap budget, decent writing |
| #3 | `gpt-4.1-nano` | OpenAI | free (2.5M/day, shared) | 5 | R | Same budget |
| #4 | `gpt-4o-mini` | OpenAI | free (2.5M/day, shared) | 5 | R | Same budget |
| #5 | `o3-mini` | OpenAI | free (2.5M/day, shared) | 5 | R | Reasoning is overkill for docs |
| #6 | `baidu/ernie-5.5-flash-preview:free` | OpenRouter | free | 4 | R | Long context: summarising big files |
| #7 | `thinkingmachines/inkling-2-small:free` | OpenRouter | free | 4 | R | Untested |
| #8 | `recursive/flint-v1.0:free` | OpenRouter | free | 3 | R | Untested |
| #9 | `z-ai/glm-5.3-flash` | OpenRouter | ~$0.005/task | 5 | R | Paid, very cheap |
| #10 | `qwen/qwen3.7-flash` | OpenRouter | ~$0.004/task | 5 | R | Paid, very cheap |

### Private (code that must not leave this PC)
| Priority | Model | Where | Cost | Rating | Basis |
|---|---|---|---|---|---|
| #1 | `gemma4:12b` | Ollama (local) | free | 3 | R |
| #2 | Claude Opus 5.5 (do it yourself) | Claude Code | Claude plan | 10 | T |

### Avoid (no role)
| Model | Where | Why |
|---|---|---|
| `gemini-3.8-flash-high` | agy | Overthinks: 50k+ thinking tokens, 9 min timeouts |
| `claude-opus-4-6-thinking` | agy | Duplicates the Lead and burns the agy pool |
| `minimaxai/minimax-m3.1-flash` | NVIDIA | Ignored read-only rules, ~60 s per call, timed out |
| `gemini-3.8-flash` | Gemini API | ~20 req/day free; timed out |
| `nvidia/nemotron-3-super-120b`, `kimi-k2.6` | NVIDIA | 404: not available for this account |
| `nvidia/nemotron-3-ultra-550b` | NVIDIA | 503 (use the OpenRouter `:free` version instead) |
| `gpt-oss-20b`, `deepseek-v4.1-flash`, `glm-5.3-flash` | NVIDIA | Timeouts |
| `google/gemma-4-31b-it:free`, `gemma-4-26b-a4b-it:free` | OpenRouter | Often 429 (too popular) |
| `nemotron-3.5-super-vl:free`, `nemotron-3-nano-omni:free`, `apodex-1.1-mini:free`, `dots-3-note-preview:free` | OpenRouter | Vision/notes models; no advantage for code |
| `liquid/lfm-3-1.7b:free`, `lfm-2.5-2.6b:free` | OpenRouter | Too small for code |
| `openrouter/free`, `openrouter/auto`, `auto-code`, `auto-beta`, `typesafe/jev-router` | OpenRouter | Routers: the model can change between steps of one task |
| `openrouter/bodybuilder` | OpenRouter | Builds API requests; not a worker |
| `gpt-5.5`, `gpt-5.6-*`, `gpt-5.3-codex`, `*-pro` | OpenAI | **Paid from your credit**: blocked by `delegate.py` |
| `*:batch` variants | OpenRouter | Asynchronous: unusable for agents |

## 3. Notes per provider

- **agy**: Gemini models use the Gemini Pro quota; `claude-sonnet-4-6`, `claude-opus-4-6-thinking` and
  `gpt-oss-120b-medium` share a separate pool. `delegate.py` runs agy workers through a role agent with only
  the tools that role needs (~3.6k tokens per call instead of ~10.4k).
- **OpenAI**: the two free groups each share one daily budget across all their models. The big group (250k)
  is small: keep it for builder+ and reviewer. GPT-5 and o-series models use the responses API (handled).
- **NVIDIA**: free, ~40 req/min, but slow under load. Worked on this account: Mistral Large 4,
  Nemotron 3.5 Lightning, MiniMax M3.1 Flash.
- **OpenRouter**: `:free` models cost nothing (1000 requests/day, 20/min). Everything else, routers
  included, bills your credit. A typical worker task ≈ 100k input + 4k output tokens. Paid prices per 1M
  tokens in/out: Claude Haiku 5.5 $0.10/$0.50, DeepSeek V4 Flash $0.09/$0.18, Qwen3 Coder Next
  $0.12/$0.80, GLM 5.3 Flash $0.04/$0.08, Qwen3.7 Flash $0.03/$0.13.
- **Ollama**: `gemma4:12b` doesn't fully fit in 8 GB VRAM: slow, text only, you write the files.
