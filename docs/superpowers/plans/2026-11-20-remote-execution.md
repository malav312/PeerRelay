# Remote execution implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Full delegated command execution on Alice with bidirectional questions.
**Architecture:** Durable process manager behind explicit exec pair/session grants;
question records in relay storage; MCP polling and capability-aware elicitation.
**Tech Stack:** Existing Python 3.13+, sqlite, POSIX processes, FastMCP, HTTPS.
**Spec:** docs/superpowers/specs/2026-11-20-remote-execution-design.md

## Global Constraints
- No existing pair/config changes, live rollout, provider calls or upstream edits.
- Trusted-host exec is explicit; ro/rw semantics stay intact.
- Never rerun uncertain commands; same task/operation exact input is idempotent.
- Limits: 4 active jobs/task, 1..86400s timeout, 1 MiB log, 16 KiB page/input.
- Branch/root binding at mode activation, enforce before launch.

## Review Focus
- Disconnect racing spawn/input and blocked output must not admit new effects.
- Restart/retry must not duplicate command or stdin effects.
- Questions/replies must remain bound to the requesting mode and live task.
- Long-running commands must not block heartbeat or cancel processing.
- MCP elicitation absence/cancel must never fabricate consent or expand grants.

### Task 1: Durable remote executor
Files: new execution.py, tests/test_execution.py; service.py, setup.py, cli.py, api.py.
Interfaces: execute(lease_id,task_id,operation_id,command,cwd='.',timeout=300,terminal=False)
returns command_id; command_status(...,command_id,offset=0) returns state/output/next_offset;
command_input(...,operation_id,data,eof=False); command_cancel(...).
- [x] Write failing real subprocess tests: explicit grants, same-worktree operations,
  output/input/PTY, nonzero status, timeout, revocation, root/branch, restart/retry.
- [x] Run pytest tests/test_execution.py and observe missing feature failures.
- [x] Implement execution journal/process lifecycle and role/session admission.
- [x] Run targeted and full suite, lint, commit.

### Task 2: Durable questions and native MCP routing
Files: new questions.py, tests/test_questions.py; client.py, mcp_server.py, service.py;
new tests/test_execution_mcp.py.
Interfaces: ask_question(lease_id,task_id,operation_id,message,choices=[]),
question_status(lease_id,task_id,question_id), answer_question(mode_id,task_id,
question_id,action,answer=''); wait_for_update(mode_id,task_id,seconds=50,ctx).
- [x] Write failing tests for ownership/replay/revocation, pending-effect denial,
  actual HTTPS execution and MCP elicitation/fallback/cancellation.
- [x] Implement durable question flow and MCP tools; polling stays ordinary code.
- [x] Run targeted and full suite, lint, commit.

### Task 3: Documentation and final delivery
Files: README/setup/execution guide, role skill copies, verification/progress.
- [x] Update setup and modes for exec, session authorization, commands/questions,
  trusted-host limits, branch behavior and client fallback.
- [x] Full suite and fresh independent whole-branch review; one regression-first fix pass.
- [ ] Commit, integrate in standalone checkout, verify private remote and push.
