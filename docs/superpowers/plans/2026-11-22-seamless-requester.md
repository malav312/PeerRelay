# Seamless Requester Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement the bounded tasks below, with regression tests and review.

**Goal:** Reduce requester context and remove manual follow-up for both native clients.
**Architecture:** Structured completion projection plus Claude channel delivery and owned Codex requester delivery, reusing existing role MCP handlers.
**Tech Stack:** Python, SQLite, FastMCP, native Claude channels, Codex app-server 0.171.0.
**Spec:** docs/superpowers/specs/2026-11-22-seamless-requester.md

## Global Constraints
- No provider SDK/API worker, copied authentication, native approval answers, model polling or automatic task replay.
- Fresh private native directory; preserve existing installations and global config.
- Legacy outcomes retain limitations; new outcomes never hide failures or requested answers.
- Skills below 5,000 characters. Native acceptance distinguished from simulated delivery.

## Review Focus
- Legacy stored tasks and invalid structured outcomes preserve trustworthy status.
- Duplicate/stale notifications and answered questions do not wake repeatedly.
- Foreground follow and background watch do not race or duplicate completion.
- Native human turn or uncertain submission never causes replay.
- Exit/cancel/transport failure stops pending delivery with observable status.

## Tasks
- [ ] 1. Structured outcomes: add validated optional completion data and migration in service; compact projection; MCP completion schema; tests for legacy/malformed/partial/failed/report retrieval and payload reduction.
- [ ] 2. Claude channel: wire prepare/start background option; probe/ack/status, automatic watch transition from follow, dedup and lifecycle; tests for unsupported config, delayed event, cancellation and no replay.
- [ ] 3. Codex requester: exact-mode/thread bridge + owned session launcher and CLI; register submitted tasks automatically, force immediate follow, questions through normal user chat; tests for scope, cancellation/exit, task registration, lifecycle and scripted native roundtrip.
- [ ] 4. Integrate skills/docs/setup commands, run full suite and native credential-free probes, package skills, independent review, commit and push a new PR. Live acceptance remains required; do not merge this follow-up automatically.
