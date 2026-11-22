# Existing-session installation implementation plan

> **For agentic workers:** Use superpowers:executing-plans task by task.

**Goal:** Install both PeerRelay roles into an existing project and connect bootstrap delivery.
**Architecture:** Project-scoped installer plus the existing bootstrap and role MCP servers.
**Tech Stack:** Python, JSON/TOML, MCP, existing Claude channel adapter.
**Spec:** docs/superpowers/specs/2026-11-22-existing-session-design.md

## Constraints and review focus
Preserve unrelated settings and edited skills. Never store private state inside shared
projects. Reject symlinked installation targets. Do not fabricate native wake support.
Channel metadata must select exactly the setup that created it; cleanup cancels watches.

### Task 1: Installer
- [x] Add tests/test_install.py: both formats, repeat/update, conflict before writes,
  malformed settings, paths with spaces/quotes, symlinks and private state separation.
- [x] Run tests red, implement install(client, project, state_dir=None, background='off')
  in src/agent_relay/install.py and CLI `install`; run focused tests green.

### Task 2: Bootstrap integration
- [x] Add bootstrap tests for project default, configured channel capability, bound
  role notifications and async shutdown. Run red.
- [x] Add CLI bootstrap project/background options; propagate options into Setups,
  roles, notification binding metadata and stdio lifecycle. Prefill local view once.
- [x] Run bootstrap/channel tests and real stdio test green.

### Task 3: Docs and verification
- [x] Update role entry instructions and add exact existing-project setup/test guide.
- [x] Run full suite, lint, package build/skill verification, independent review.
- [x] Commit and publish test branch/PR; do not merge without live acceptance.

Validation: 361 passed, 8 skipped; Ruff and wheel/skill checks passed. Independent
review found no outstanding defects. Live native acceptance remains pending.
