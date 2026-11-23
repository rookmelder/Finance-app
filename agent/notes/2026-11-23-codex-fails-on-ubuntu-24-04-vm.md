# Codex workers fail on Ubuntu 24.04 (AppArmor blocks bwrap)

Every Codex run exits 6 at once: `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`.
Cause: `kernel.apparmor_restrict_unprivileged_userns = 1` (Ubuntu 24.04 default), so Codex's sandbox helper cannot create a user/network namespace. `unshare -Urn true` fails the same way.

Effect: `delegate.py` falls through each chain to agy. The tester role has no usable worker (its chain is Codex plus Gemini, and Gemini is skipped for Gemini-built work), so money code got no independent tester.

Fix (needs sudo, loosens a system-wide protection, user's decision): `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`, or an AppArmor profile that allows bwrap. Until then use agy only.
