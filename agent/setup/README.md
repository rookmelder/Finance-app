# Setting up the workers

You don't need everything. Each tool or key you add gives the Lead more (and cheaper) workers.
After adding anything, run `python agent/check_setup.py`: it tests what you have
and writes `SETUP.md`, and `delegate.py` then only uses workers that work on your machine.

| What | Gives you | Cost | Guide |
|---|---|---|---|
| Claude Code | The Lead (required) | Your Claude plan | [claude.md](claude.md) |
| git | Required | Free | [git.md](git.md) |
| Python 3.8+ | Required: runs `agent/delegate.py` and `agent/check_setup.py` (standard library only) | Free | python.org, or your package manager |
| agy (Google Antigravity CLI) | Gemini builders, Claude Sonnet reviewer | Google AI Pro plan for real quota | [agy.md](agy.md) |
| Copilot CLI | Runs all API workers below | Free in this setup (your own keys) | [copilot.md](copilot.md) |
| NVIDIA key | Mistral Large 4, Nemotron | Free | [api-keys.md](api-keys.md) |
| Gemini API key | Gemini Flash-Lite tester | Free | [api-keys.md](api-keys.md) |
| OpenRouter key | Free fallback models (1000/day after a one-time $10) | Free models cost nothing | [api-keys.md](api-keys.md) |
| OpenAI key | o3 reviewer on free daily tokens | Needs a small prepaid credit + data sharing | [api-keys.md](api-keys.md) |

Works on Windows, Linux and macOS (use `python3` where `python` is not on the PATH).

**Minimum that works:** Claude Code + git + Python + one worker source (agy, or Copilot CLI + one free key such as NVIDIA).
With nothing else, the Lead does the work itself.
