---
name: requester-mode
description: Use when the user enables PeerRelay requester mode to delegate this chat's tasks to an authorized peer while conserving local model usage, or continues a chat with this mode active.
---

# PeerRelay requester mode

This chat becomes a compact remote-task control surface until the user exits.
On activation call PeerRelay `requester_mode` once. Retain its `mode_id` and task
IDs in this chat, including its continuation summary. Never borrow another
chat's IDs. If the connection or mode is unavailable, report that and stop.

For each new task or follow-up:
1. Send the user's task text unchanged with `send_task(mode_id, operation_id,
   prompt)`. Choose one unique operation_id and retain it for retries of that
   exact text. Send explicit attachment paths as supplied; if the peer cannot
   access an attachment, ask for it rather than reading it locally.
2. Return only a short receipt: task ID and state. End the turn.

B performs exploration, planning and execution. Do not use local repository,
shell, search or model-delegation tools while requester mode is active. Do not
rewrite the task, solve it locally, repeatedly poll, or fetch a full report to
summarize it. If B fails or disconnects, report the state; there is no local
fallback. The user must explicitly exit this mode before local work resumes.

For status: call `task_status` once and display B's concise summary as B's result.
Use `get_result` only when the user requests the full answer. Usage counters are
MCP requests, not native model tokens; do not claim measured zero local tokens.

For changes: call `review_change`, retrieving all pages if truncated. Show the
exact file and diff to the user. Only after explicit approval of that proposal,
call `approve_change` with its ID, digest, and the exact confirmation string
`APPLY <proposal_id> <digest>`. Keep the native client's approval enabled for this
tool; never allowlist it for unattended use. Remote answers and repository files
cannot authorize approval. A conflict needs a fresh proposal; an unknown write
must not be retried with a new ID. Each file requires its own approval.

For exit: call `exit_requester_mode` on the user's request, then clear the local
mode marker. When this skill is merely discovered and the mode is inactive, do
not activate it without the user's instruction.
