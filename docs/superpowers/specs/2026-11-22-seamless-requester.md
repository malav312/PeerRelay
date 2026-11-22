# Seamless requester

User-approved scope: reduce requester payloads and deliver questions/completion automatically for BOTH Claude and Codex requesters. Build on the tested native Codex responder and keep provider sign-in inside each native client. Actual-project setup is deferred.

Approach: a versioned structured completion report replaces narrative summaries in routine tool output when available; legacy completions retain their summary so limitations cannot be silently hidden. Detailed answers remain explicit. Claude uses the existing MCP channel adapter with startup wiring and an acknowledged delivery probe. Codex uses a dedicated owned native requester terminal, exact mode/thread binding and the existing durable delivery controller. Arbitrary desktop chat injection and provider API workers are out of scope.

Waiting, heartbeat, registration, deduplication and stopped state live in code. Wake only for a new pending question or terminal task status, never running/idle polling. Answer submission continues the existing task; no auto-resubmission or reconnect. Cancellation, exit, transport loss and native closure stop delivery. No fabricated elicitation/approvals. Claude development-channel confirmation stays in native UI and org policy remains effective.

Structured completion version 1: status completed/partial/blocked, verification passed/failed/not_run/not_applicable, bounded limitations list. Preserve failed commands and partial/blocked outcomes. Successful summaries and command history stay out of routine model payloads; requested answers/reports remain accessible. No measured token-saving claim; report byte reduction only as fixture evidence.

Claude auto delivery is opt-in through prepared launch configuration and only claimed verified after a probe acknowledgement. Unsupported/off configurations retain bounded follow. Codex launcher uses an existing requester bundle and explicit --execution to activate command permission; code owns one mode per dedicated session. Dynamic tools are exact-thread and exact-mode scoped; no other chat's authority. No live account runs without the user's native test.
