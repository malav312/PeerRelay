# Follow-through and security implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement task by task.

**Goal:** Preserve conversation authority and reduce requester polling turns while tightening peer boundaries.
**Architecture:** Existing Python relay plus bounded MCP follow controller, additive filesystem policy, transport boundary and opt-in client notification adapter.
**Tech Stack:** Python, SQLite, FastMCP, FastAPI, optional Docker.
**Spec:** ../specs/2026-11-21-follow-security-design.md

## Global Constraints

No provider API calls, silent permission changes, automatic replay, live reconnection,
or Git history rewrite. Retain stable IDs and deny unsafe capability assumptions.

## Review Focus

Cross-chat ID reuse; reconnect after interrupted effects; concurrent followers answering
one question; altered ignore files/symlinks; notification duplication and cancellation.

### Task 1: Portable diagnostics and follow-through
- [x] Add failing tests in tests/test_follow.py and tests/test_follow_mcp.py for mode resume, permission/context preservation, closed-mode rejection, repeated submission, two elicitation replies, deadline/fallback and cancellation.
- [x] Add mode_status RPC to service.py; add diagnostics and follow controller in follow.py, register requester_diagnostics, resume_requester_mode, submit_and_follow and follow_task in mcp_server.py. Keep old tools.
- [x] Run focused tests, record results and commit.

### Task 2: Peer and transport boundaries
- [x] Add failing tests in tests/test_security.py for ignored reads/list/search/proposals, risky target warnings, secret answers and unauthenticated body rejection.
- [x] Add policy.py, apply it in filesystem.py/approvals.py; wrap peer results, label forms and block credential-shaped replies. Authenticate before bounded body reads with deadlines and bounded sanitized logging. Require interactive approval confirmation with an honest limitation.
- [x] Run security and existing approval/transport suites; record results and commit.

### Task 3: Execution isolation
- [x] Add failing contract tests for a Docker backend: fixed argument construction, only project mounted, cancellation/restart cleanup, no host fallback. Test explicit host acknowledgement and temporary HOME.
- [x] Add execution backend configuration, pass to Executor, expose backend in diagnostics; wire lifecycle cleanup and safe failure.
- [x] Run execution regression suite and Docker integration if available; record unavailable checks accurately and commit.

### Task 4: Optional meaningful background delivery
- [x] Add tests for actionable change detection, duplicate suppression/acknowledgement, bounded retries, watch cancellation and unsupported clients.
- [x] Add explicit Claude channel adapter, requester/responder watches and acknowledgement; no arbitrary peer text in notifications. Portable flow remains default; no unsupported Codex push claims.
- [x] Run SDK protocol tests, document native-client limits and commit.

### Task 5: Documentation and verification
- [x] Update both requester/responder skills and setup/security docs with permission reuse, follow timeout, client opt-in, restricted local tools, trust tradeoffs and unresolved findings.
- [x] Full pytest and lint/format; independent whole-branch review; fix important findings; final report separates verified behavior from runtime-dependent checks.

## Delivery record

Implementation and automated verification completed as one integrated change;
task-by-task commits were consolidated because the mode binding, execution and
MCP boundary changes share regression coverage. The suite passes 166 tests; Ruff
lint/format pass. Independent review findings were fixed and rechecked.

Docker daemon execution and native client acceptance remain pending because no
Docker runtime or paired native-client test was available. These are explicit
acceptance gaps, not passing integration claims. See ../../verification.md.
