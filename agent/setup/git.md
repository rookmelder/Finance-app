# git

1. Windows: `winget install --id Git.Git -e` (or git-scm.com). Restart the terminal.
2. Set your name once: `git config --global user.name "Your Name"` and `git config --global user.email "you@example.com"`.
3. Optional, to push to GitHub: `winget install --id GitHub.cli -e`, then `gh auth login`.

Why it is required: `delegate.ps1` uses `git status` to see exactly which files a worker changed.
