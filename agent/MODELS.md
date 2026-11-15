# Models you have access to

Status 2026-11-13. The `models:` lines in `agent/roles/*.md` use the first entries that passed our tests. Prices and limits change: re-check before relying on a number.

**Role** = the one job each model fits best. **Priority** = pick order within that role:
**#1 is picked first**; go to #2 when #1 is busy, throttled or out of quota, and so on.
**Rating** = quality as a worker, out of 10. **Basis**: **T** = tested by us, **R** = reputation only (untested by us).
The order weighs quality first, then cost (free before paid), then speed and how much quota is left.
These are the template author's ratings. Rate your own models: `python agent/bench.py --workers <worker> ...`.
Cost, free limits and provider quirks: `LIMITS.md`. Read only the table of the role you are filling.

## Pick order per role

### Lead (plans, specs, reviews every result)
| Priority | Model | Where | Cost | Rating | Basis |
|---|---|---|---|---|---|
| #1 | Claude Opus 5.5 | Claude Code | Claude plan | 10 | T |

### Builder (one module/function + tests, from a spec)
Measured 2026-11-15 with `agent/bench.py` (2 tasks, 22 hidden tests; time and tokens per task).
| Priority | Model | Where | Cost | Rating | Basis | Why this spot |
|---|---|---|---|---|---|---|
| #1 | `gemini-3.8-flash-low` | agy | Pro quota | 9 | T | 22/22, 23-31 s, 9-15k tokens |
| #2 | `gemini-flash-lite-latest` | Gemini API | free (~500 req/day) | 8 | T | 22/22, 25-46 s, 4-7 calls, 32-60k tokens. Also the tester's main model: if the builder ended here, force another tester |
| #3 | `nvidia/nemotron-3-ultra-550b-a55b:free` | OpenRouter | free (1000 req/day) | 7 | T | 22/22, 78-260 s, 6-11 calls, 49-95k tokens |
| #4 | `mistralai/mistral-large-4-1t-a52b` | NVIDIA | free | 6 | T | 22/22 but 307 s and a 480 s timeout (25-120 s per call): last resort |
| - | `nvidia/nemotron-3.5-lightning-30b-a3b` | NVIDIA | free | 2 | T | 22/22 but loops: 74 calls and 2.46M tokens on one task. Not as a builder (fine as a no-tools scribe) |
| - | `cohere/north-mini-code:free` | OpenRouter | free | 2 | T | 10/22: looped to the call cap on one task, provider error 400 on the other |
| - | `gpt-5.4-mini` | OpenAI | free (2.5M/day) | 3 | T | 0/4 via Copilot in the old benchmark: tried to write files through the shell. Not re-tested since workers lost the shell |
| - | `gpt-5-mini` | OpenAI | free (2.5M/day, shared) | 3 | R | Same family habit expected; untested |
| - | `anthropic/claude-haiku-5.5` | OpenRouter | ~$0.012/task | 7 | R | Paid: only when all free options are throttled |
| - | `deepseek/deepseek-v4-flash` | OpenRouter | ~$0.010/task | 6 | R | Paid backup |
| - | `qwen/qwen3-coder-next` | OpenRouter | ~$0.015/task | 6 | R | Paid backup |

### Builder+ (multi-file changes, refactors, tricky logic)
Not measured by `bench.py` (its tasks are single-file); the order of #2 and #3 follows their speed as builders.
| Priority | Model | Where | Cost | Rating | Basis | Why this spot |
|---|---|---|---|---|---|---|
| #1 | `gemini-3.8-flash-medium` | agy | Pro quota | 7 | T | More thinking than low, no overthinking |
| #2 | `nvidia/nemotron-3-ultra-550b-a55b:free` | OpenRouter | free (1000 req/day) | 7 | T | 22/22 as a builder, 78-260 s |
| #3 | `mistralai/mistral-large-4-1t-a52b` | NVIDIA | free | 6 | T | Correct but 5 minutes or a timeout per task |
| #4 | `gpt-5.4` | OpenAI | free (250k/day: ~3-10 tasks) | 5 | R | GPT family wrote files via the shell in our old test: not in the chain |
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
