# PeerRelay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Ship a standalone MCP requester/responder pair with durable, explicitly approved writes.

**Architecture:** A hosts a private HTTPS relay and scoped filesystem. B's own Codex/Claude agent claims work through a separate MCP role. SQLite and lease checks enforce authorization; skills guide compact requester behavior.

**Tech Stack:** Python 3.13, FastAPI, HTTPX, SQLite, MCP Python SDK v1, pytest, Ruff.

**Spec:** docs/superpowers/specs/2026-11-19-agent-relay-design.md

## Global Constraints
- No upstream runtime changes, no the unrelated application access, no provider credential access.
- 512 KiB text files, 20,000-character prompts, 2,000-character summaries.
- Explicit approval per file; no deletion, shell, or multi-file atomicity.
- Never substitute A's local model when B is unavailable.
- Mode state scoped per chat; responder leases revoke tools and unapplied writes.

## Review Focus
- Wrong/stale role and lease identifiers must never grant another chat's access.
- Crash between filesystem effect and receipt must not cause automatic replay.
- Changed paths, symlinks, hardlinks, oversized and non-text files fail safely.
- Disconnect must revoke active tools and unapplied proposals even after completion.
- Configuration and remote URLs must not expose credentials through redirects/logs.

### Task 1: Approved filesystem writes
Files: src/agent_relay/{filesystem,journal,schemas}.py; tests/test_writes.py.
Interface: Filesystem.apply(operation_id, tool, grants) -> receipt, preserving upstream version semantics.
- [x] Write tests for unchanged-before-approval, apply/replay, conflict, create, escape and uncertain replay; observe RED.
- [x] Adopt attributed traversal/journal code and implement locked version-checked create/replace.
- [x] Run write tests GREEN, lint, commit.

### Task 2: Durable relay and lease boundaries
Files: src/agent_relay/service.py; tests/test_service.py.
Interface: Relay.call(role, method, arguments) -> bounded JSON dict; lifecycle sweep() and close().
- [x] Test requester modes, idempotent submit, B lease/claim/tools, completion, compact results and approval; observe RED.
- [x] Implement SQLite state, strict schemas, scoped task access and deterministic disconnect handling.
- [x] Test expired leases, wrong roles/chats, duplicate payload changes, revoked proposals, restart and limits; run suite GREEN, commit.

### Task 3: HTTPS and stdio MCP integration
Files: src/agent_relay/{api,client,mcp_server,cli}.py; tests/test_transport.py; tests/test_mcp.py; pyproject.toml.
Interface: peer-relay init/serve/mcp; role-specific tools map to Relay.call. Configuration owns secret file paths, never provider tokens.
- [x] Write HTTP auth, TLS rejection and SDK stdio end-to-end write tests; observe RED.
- [x] Implement pinned HTTPS client, private setup files, bounded API, heartbeat, SDK MCP tools, explicit prompt templates.
- [x] Verify disconnect and no model imports/calls in transport; run full suite GREEN, commit.

### Task 4: Client packages and handoff
Files: plugins/{codex,claude}/; README.md; docs/verification.md.
- [x] Create requester/response skills with persistent mode IDs, concise outputs, no local fallback and explicit approval instructions.
- [x] Validate package manifests, skill paths and installed SDK transport; run full suite and independent review.
- [x] Fix material review findings with RED/GREEN; deliver separate Git repository and fixture write evidence.
