# Native client setup implementation plan

> **For agentic workers:** Use superpowers:executing-plans for inline execution.

**Goal:** Repeatable, honest post-pairing setup for both roles and native clients.
**Architecture:** A CLI setup module writes new private directories with MCP
configuration, copied role skills and a Claude launcher. Codex gets configuration
without an unsupported security claim or automatic launch.
**Tech Stack:** Python standard library, existing CLI, pytest, native client CLIs.
**Spec:** `docs/superpowers/specs/2026-11-21-client-setup.md`

## Global constraints

- No provider login copying, global configuration replacement, or auto-connection.
- No overwrite of existing chat directories or private state.
- Native startup/tool restrictions and pairing are separate acceptance checks.

## Review focus

- Paths containing spaces/quotes must not become shell code.
- Existing or symlinked destinations must remain untouched.
- Installed wheels must include the role instructions.
- Missing client flags must fail closed before launch.
- User configuration, native version changes, and desktop clients must not acquire
  an unsupported isolation claim.

## Task 1: Setup, launcher and documentation

- [x] Add behavior tests for CLI setup, validation, artifacts and launcher boundary.
- [x] Run tests: observed failure for absent client-setup command.
- [x] Implement `client_setup.py`, CLI dispatch and packaged skills.
- [x] Update setup/security docs with native acceptance and Codex limitation.
- [x] Run targeted tests, full pytest and Ruff: passing.
- [ ] Review, commit and push the existing feature branch without merging.

Ruling: Codex setup remains available, but a complete native tool restriction is
unverified. Do not equate a shell toggle or hooks with isolation; next Codex live
test remains gated on externally verified controls. Cost: additional setup before
Codex can be represented as a restricted responder.

Independent review: no important findings; eight targeted tests independently
passed and wheel contents inspected. Native controls remain pending acceptance.
Deferred minor: timeout while obtaining Claude CLI help fails closed but can show
a traceback instead of the normal CLI error message.

Final validation also reproduced and fixed launcher argument forwarding: even
recognized extra flags must be rejected. Targeted tests returned to green.
