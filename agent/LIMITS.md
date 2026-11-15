# Providers: cost, free limits, quirks

Status 2026-11-13, measured on the template author's accounts. Prices and limits change: re-check before relying on a number.
Which model to pick per role: `MODELS.md`. What `delegate.py` enforces: `models.json`.

## Money and limits at a glance

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

## Notes per provider

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
