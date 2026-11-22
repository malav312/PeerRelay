# Spoken-code pairing integration — November 21, 2026

[PR #2](https://github.com/malav312/PeerRelay/pull/2) is based on cleaned main, which contains the tested follow-through, security
and Podman execution implementation. The PR remains unmerged for two-laptop
acceptance. Both people must use the updated pairing branch for that test.

The integrated suite passes **197 tests**, including eight real container checks,
in 40.36 seconds. Ruff lint/format and Git whitespace checks pass. The only warning
is the upstream Starlette/httpx deprecation. Runtime validation used Podman 6.0.2,
Docker CLI 29.10.0 and cached `python:3.13-slim` through the fixed local Docker API
socket. All projects, pairing credentials and command output were disposable.

The added runtime test performs a real spoken-code exchange on loopback, starts the
HTTPS relay using the resulting credentials, authenticates both roles through
certificate pinning, then writes a project file inside an actual container and
disconnects. The other seven runtime tests retain coverage of isolation, replay,
PTY/stdin, cancellation, timeout and orphan cleanup.

Integration regressions verify Docker defaults, explicit host-credential consent,
unchanged execution configuration when re-pairing, both screens' backend disclosure,
and digest authentication before request bodies are read. The initially failing
checks passed after integration fixes.

Independent review found two important issues, both reproduced before correction:

- Re-pairing could race with relay startup. Pairing now holds the configured state's
  owner lock throughout the exchange, excluding another pair or serving process.
  A server that cached configuration before the replacement refuses stale startup
  after acquiring its lock. Cancellation releases the guard.
- Migrating a legacy bundle could retain Alice's old requester URL while changing
  the server's address. Successful pairing now updates Alice's endpoint and enables
  certificate pinning for that requester as well.

Deferred minor: relay port 65535 passes generic validation but leaves no port+1 for
pairing. Use the default 9443 or another relay port below 65535; an existing pair's
stored port wins over `--port`. A failed new pairing at 65535 needs a new pair folder.

Real multicast discovery across two laptops, native Codex/Claude user-interface
behavior and provider token savings remain separate acceptance work. The source
review was not a formal audit of SPAKE2 or dependency internals. Unchanged main
behavior was covered by its existing tests and review. No live pair was upgraded
or restarted during these checks.
