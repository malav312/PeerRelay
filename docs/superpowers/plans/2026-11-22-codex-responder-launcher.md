# Dedicated Codex responder launcher

Implement an explicit `codex-responder` command using an existing responder bundle.
It preflights the pinned native CLI and read-only permission profile before activating
a lease, creates one owned thread, registers the responder MCP schemas as dynamic
tools, and admits calls only for that thread and lease. Code polls incoming work and
answers; native turns are only meaningful wakes. The dedicated TUI owns human input.
Native approvals remain fail closed; no global configuration or authentication copies.

Verification: scripted RPC request tests, real MCP handler + disposable relay end-to-end
acceptance, launcher cleanup/failure tests, credential-free outbound-denied native
metadata/schema/policy probe with zero turns, full suite and lock consistency. Native
model tool compliance and human/TUI concurrency remain manual acceptance checks.

Implementation notes: the bridge uses the actual responder MCP schemas/handlers,
rejects activation and background tools, and binds once to the exact thread/lease.
Launcher preflights CLI 0.171.0, checks effective configuration and returned thread
policy before activation, and settles a cancelled activation before lease cleanup.
Native dynamic RPC handling stays concurrent with notifications.

Verification: disposable relay acceptance covers work -> claim -> read -> question
-> no idle wakes -> answer -> completion, with exactly two meaningful starts.
The credential-free native probe registers 13 task tools, creates/resumes an exact
idle thread, validates restrictions, and closes owned resources with zero turns.
Read-only writes and disabled execution/apps/browser/plugins/delegation are enforced;
no total local-read isolation claim. Independent review found no remaining important
issues after activation-cancellation and returned-thread-policy fixes. Actual native
dynamic-tool routing after TUI attachment and human-turn concurrency remain manual
acceptance checks; no real provider calls or live pairing mutations were performed.

Final verification: 236 passed, 8 skipped (existing environment-dependent skips),
one existing Starlette/httpx deprecation warning. Ruff and git diff --check pass;
uv lock --check --offline resolves 45 packages. Native zero-turn probe passes.
