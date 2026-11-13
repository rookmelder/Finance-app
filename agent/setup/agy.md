# agy (Google Antigravity CLI)

Gives you: Gemini 3.8 Flash Low/Medium builders and the Claude Sonnet 4.6 reviewer (the best reviewer we tested).

1. Install the Antigravity CLI from Google (antigravity.google) and make sure `agy` runs in a new terminal.
2. Sign in with the Google account that has **Google AI Pro**. Without it the quota is very small.
3. Allow the project's check command for workers. agy only reads allow-rules from the global file
   `~/.gemini/antigravity-cli/settings.json`. Example for Python unittest:
   ```json
   { "permissions": { "allow": [
       "command(python -m unittest discover -s tests)",
       "command(regex:^python -m unittest( -v)?( tests\\.test_[a-z_]+)*( -v)?$)"
   ] } }
   ```
   Add a rule per check command in `PROJECT.md` (the Lead asks before changing this file: it is global).
4. Check usage inside an agy chat with `/usage`. About 20 worker jobs use 6-7% of one 5-hour window.

Notes: this repo's `.agents/agents/` gives agy workers a trimmed tool set. Your own agy chats are not affected.
Never put an `AGENTS.md` or `GEMINI.md` in the project: agy loads it into every worker.
