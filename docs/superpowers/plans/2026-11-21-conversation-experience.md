# Conversation experience implementation plan

> **For agentic workers:** Use superpowers:executing-plans for inline implementation.

**Goal:** Normal questions and compact persistent requester/responder conversations.
**Architecture:** A presentation helper formats questions and compacts status at the
MCP boundary, leaving durable relay semantics and authorization unchanged. Update
the role instructions in both clients together.
**Tech stack:** Existing Python MCP/Pydantic, pytest and role skills.
**Spec:** `docs/superpowers/specs/2026-11-21-seamless-session-design.md`

## Constraints and review focus

- Preserve custom answers, Unicode and accept/decline/cancel without new defaults.
- Do not suppress failed/uncertain execution or claim implemented for queued work.
- Retain recovery IDs and full status on demand; no silent native token claims.
- Routine interaction must not echo provenance warnings or JSON.
- No idle-model polling, automatic reconnect or changes to credential/backend grants.

## Task 1: Questions, compact status and session instructions

- [x] Reproduce old question rendering and verbose status with failing tests.
- [x] Implement presentation helpers and exercise both real MCP follow paths.
- [x] Update both role skills and MCP instructions for concise persistent routing.
- [x] Run fresh behavior probe and full regression suite; review the change.
- [x] Commit the first slice separately from bootstrap and delivery work.

The user's screenshots are the native baseline failure: JSON embedded in a form,
repeated peer warning, internal ID and repeated verbose fallback. Automated tests
will pin those presentation errors; a fresh probe will cover instruction behavior.

Validation: 201 passed, eight optional container tests skipped, one existing
Starlette/httpx warning. Ruff and whitespace checks passed. Seven fresh role
behavior scenarios passed as simulations, not native acceptance. Independent
review found no important introduced regression. Strengthened persisted SDK
coverage after review for exact readable Unicode questions and custom responses.

Deferred minor (pre-existing): legacy wait_for_update surfaces credential-shaped
reply rejection as a tool error; submit_and_follow returns pending context and
reply_rejected. Neither forwards the rejected reply. This consistency change is
outside the ordinary-question presentation slice.
