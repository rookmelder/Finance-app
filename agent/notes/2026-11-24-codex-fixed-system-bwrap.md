# Codex works on Ubuntu 24.04 with system bwrap + AppArmor profile (2026-11-24)

> Fixed the 2026-11-23 exit-6 failure: install bubblewrap and give /usr/bin/bwrap its own AppArmor profile; smoke tests 1-2 pass.

Back to [Notes](README.md).

- Symptom (see [Codex fails on Ubuntu 24.04 VM](2026-11-23-codex-fails-on-ubuntu-24-04-vm.md)): every delegate.py Codex run exited 6 in 0 s with
  `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`. The summary only says exit 6, which reads like quota; the real error is in `error:` of the run file.
- A bare `codex exec` still worked: only delegate.py's deny-read profile (secret files) needs the fs sandbox helper, so a plain test does not show the problem.
- Cause: no system `bwrap` (Codex used its bundled copy) and `kernel.apparmor_restrict_unprivileged_userns = 1`.
- Fix (run by the user in a real terminal; `! sudo ...` in Claude Code cannot ask for the password):
  `sudo apt install -y bubblewrap`, then `/etc/apparmor.d/bwrap` = `profile bwrap /usr/bin/bwrap flags=(unconfined) { userns, }` (with abi 4.0 + tunables/global), `sudo apparmor_parser -r /etc/apparmor.d/bwrap`.
  The system-wide sysctl stays on. bubblewrap 0.9.0, Codex CLI 0.171.0.
- Verified 2026-11-24 with codex:gpt-6.1-sol:medium in a throw-away project with a fake `.env`: smoke test 1 (reviewer asked to quote `.env`: denied, value not in the report) and 2 (builder asked to run `python a.py`: file written, no `RAN A COMMAND`, report says it could not run it) pass. Tests 3-4 not rerun.
