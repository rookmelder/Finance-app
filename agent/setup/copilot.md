# GitHub Copilot CLI

Gives you: the "hands" for every API worker (NVIDIA, Gemini API, OpenRouter, OpenAI).
`delegate.py` runs it in "bring your own key" mode with your keys from `.env`, a trimmed tool set and its sandbox.

1. Install GitHub Copilot CLI (see GitHub's Copilot CLI docs) and make sure `copilot` runs in a new terminal.
2. Sign in if it asks. Nothing else to configure: `delegate.py` sets everything per run, and your own
   Copilot setup is not changed.
3. Add at least one API key (see [api-keys.md](api-keys.md)) and run `agent/check_setup.py`.
