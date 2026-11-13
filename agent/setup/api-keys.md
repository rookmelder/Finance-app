# API keys

Copy `.env.example` to `.env` (in the project root) and fill in the keys you have. `.env` is never committed.
Run `agent/check_setup.py` afterwards: it tests each key with a free call.

| Key in .env | Where to get it | Free limits | Watch out |
|---|---|---|---|
| `NVIDIA_API_KEY` | build.nvidia.com > API keys | ~40 requests/min | Free tier may log prompts. Some models 404 per account |
| `GEMINI_API_KEY` | aistudio.google.com > Get API key | Flash-Lite ~500 requests/day | Free tier trains on prompts |
| `OPENROUTER_API_KEY` | openrouter.ai > Keys | `:free` models: 50/day, or 1000/day once you ever bought $10 credit | Only `:free` models are free; routers and paid models use credit |
| `OPENAI_API_KEY` | platform.openai.com > API keys | With data sharing ON: 250k tokens/day big models, 2.5M/day mini | Needs a prepaid credit. Anything else bills it; `delegate.py` blocks that |

**Private projects:** the free tiers above may log or train on your prompts. For private work use agy, Ollama or Claude only (set "Private?" in `PROJECT.md`).

Details per model (ratings, pick order): `agent/MODELS.md`.
