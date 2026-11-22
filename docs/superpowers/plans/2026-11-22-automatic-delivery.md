# Owned Codex delivery implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement the bounded owned-session controller and deterministic relay waiting layer for automatic native delivery.

**Architecture:** Bind one role and mode/lease to an exact newly created Codex thread and server generation. A durable local journal records event submission before `turn/start`; ambiguous starts stop delivery, and read-only reconciliation never resends them. Read relay status in code and send only actionable metadata while the native thread is idle.

**Tech Stack:** Python, SQLite, asyncio, Codex app-server Unix WebSocket transport.

**Spec:** `docs/superpowers/specs/2026-11-21-seamless-session-design.md`

## Global constraints

- Work only in this isolated clone; no live installations, credentials or relay activity.
- No real provider inference in implementation tests. Dedicated Codex terminal uses its owner's native authentication when eventually enabled.
- No latest-thread lookup, automatic rebind/reconnect, remote-effect replay or inferred user consent.
- This stage is a composable controller, not a finished native product launcher. Existing Claude adapter remains unchanged; all four role/client pairings remain acceptance scope.

## Review focus

- Lost start response must never cause a duplicate model turn.
- Human TUI turns can race the controller; ambiguous ownership stops delivery.
- Revoked leases and mode exit must stop waiting, heartbeat and queued delivery.
- An unchanged relay marker must not produce repeated turns after native completion.
- Startup and cleanup must affect only owned processes and exact socket/thread identities.

### Task 1: Durable delivery controller

Files: `src/agent_relay/delivery.py`, `tests/test_delivery.py`.
Interfaces: immutable `Binding`; `DeliveryController` consumes async `call(method, params)` and exact-generation notifications; `RelayMonitor` calls existing relay read/heartbeat APIs.

- [x] Write failing tests for binding validation, meaningful event dedup, busy queueing, uncertainty, exact turn completion, restart refusal, revocation and idle waiting.
- [x] Run targeted pytest and verify missing implementation fails.
- [x] Implement journal-before-send, status/turn reconciliation and code-only monitoring.
- [x] Run targeted tests; expected all pass.

### Task 2: Owned native lifecycle

Files: `src/agent_relay/native_codex.py`, `tests/test_native_codex.py`, `pyproject.toml`.
Interfaces: `NativeRPC` routes JSON-RPC replies and notifications; `OwnedCodexSession` owns one private socket/server generation and exact thread, returns exact TUI arguments after bounded metadata readiness.

- [x] Write failing tests for bounded readiness, startup failure cleanup, requests/notifications, unexpected server requests and no-inference method boundary.
- [x] Implement lifecycle and transport using a maintained WebSocket implementation; preserve native approval ownership as an unresolved acceptance gate.
- [x] Run targeted tests; expected all pass.

### Task 3: Integration checks and handoff

Files: `docs/automatic-delivery.md`, `docs/session-experience.md`.

- [x] Document controller integration contract, staged limits and runnable no-inference/acceptance sequence.
- [x] Run full suite and ruff; report any environmental blockers by test name.
- [x] Commit on `feat/automatic-delivery`; do not push or merge.

## Verification record

- Full suite: 224 passed, 8 skipped (explicit container-runtime acceptance opt-in),
  one existing Starlette/httpx deprecation warning. Ruff and diff whitespace checks pass.
- Real native metadata probe on Codex CLI 0.171.0: exact thread resumable, idle,
  zero turns, owned server stopped, socket removed. A name metadata write is
  required before readiness in the fresh no-turn path tested here.
- Independent review found stale-event admission across an awaited read, a cwd
  override and an incorrect reply-tool name. Each was reproduced with a failing
  test and fixed before the final suite.
- Product launcher/MCP registration, native approval ownership, tool isolation,
  Claude channel integration and all four native inference pairings remain the
  explicit next stage in `docs/automatic-delivery.md`.
