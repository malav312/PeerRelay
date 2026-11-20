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

Final suite and independent review results will be recorded after review fixes.
