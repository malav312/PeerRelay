# Verification, 2026-11-19

The transport and approved write path run without any provider calls. The current
suite includes real TLS certificate validation and two independent MCP stdio
subprocesses driven by the official SDK client. It confirms unchanged files before
approval, exact digest approval, a successful replacement, idempotent replay, and
B disconnect refusing further tasks. Filesystem tests also cover create, conflict,
symlink/hardlink/traversal refusal, restart, and uncertainty after an applied effect.

Fresh-agent wording probes are recorded separately in skill-evaluation.md. They
are simulations, not actual Codex/Claude product runs and not a token benchmark.

Pending acceptance: install the role config and mode skills on two native clients;
confirm persistent routing across turns, owner interruption, approval UX and actual
requester/worker token totals. Both Codex and Claude use the standard MCP package,
but no claim of full native-client acceptance is made before these tests.

Final staging suite: **33 passed**. Ruff lint, formatting and whitespace checks pass.
The extra regressions cover configured credential/configuration overlap with the
project, NUL-containing reads/proposals, complete large-diff pagination, explicit
missing-newline markers, concurrent MCP wait/disconnect, and cancelled waits.
Every review regression was observed failing before its correction.

Fresh whole-branch review found four Important issues and no Critical or Minor
issues. All four were corrected in one fix pass. See progress.md for findings,
decisions and limits. No second review or native-model acceptance is implied.

Claude's installed plugin validator passed with optional missing-author/icon
warnings. The suite emits one upstream Starlette/httpx deprecation warning.
These do not affect the verified protocol and fixture-write results.
