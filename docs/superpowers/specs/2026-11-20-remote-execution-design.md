# Remote execution and questions

User-approved direction: Bob's active native agent does full coding work in Alice's
existing worktree, including commands and file operations, and routes questions back
to Alice's original chat. Alice authorizes the session, not each edit. Keep PeerRelay
standalone, existing pair data unchanged, and requester model work minimal.

## Approach
Use an executor on Alice, reached through Bob's existing MCP transport. A cloned
workspace on Bob would need synchronization and would not use Alice's dependencies
or working tree, so it does not satisfy the requested behavior. Keep the tested
proposal/local-decision flow for old ro/rw pairs; add explicit `exec` trusted-host
pair permission and per-requester-mode `execution=true` activation. Existing pairs
never gain execution automatically. Replacements, creation, deletion, renaming,
Git, dependencies and tests are available via remote commands without human
per-file approval. Pair activation selects the project directory; commands run as
Alice's relay OS user, not Bob. This is trusted-host execution, not a sandbox or a
promise of project-only access. Normal OS permissions apply. The relay supplies a
minimal environment; never copies Bob's environment or provider credentials.

## Execution contract
Durably record each exact command, cwd, timeout and terminal setting before spawn.
Stable task+operation IDs return the original job, reject different payloads, and
never replay after restart or uncertainty. Return job IDs immediately; stream bounded
combined output through offset reads/waits. Support stdin and optional PTY for
interactive commands. Use process groups for cancellation, timeout, disconnect,
mode exit and shutdown; report best-effort termination, never roll back effects or
claim control of escaped/detached processes. No unattended restart/resume.
No new job starts after revoked authority. At most 4 active jobs per task, timeout
1..86400 seconds, 1 MiB retained output per job, 16 KiB output pages, 16 KiB input.
Commands start in the registered root or a resolved descendant directory. This is
cwd validation, not shell confinement. Pin root identity and Git branch/worktree at
requester activation; refuse new commands after branch/root changes, requiring a
new mode. Commits on the same branch are allowed. An intentional branch switch can
run, but further commands need a new mode. External writers still can race checks.

## Questions
Bob submits durable task-bound questions with stable IDs and optional choices.
Alice's requester tools alone can answer accept/decline/cancel; Bob retrieves the
same result and resumes. Pending questions block new execution and task completion.
Replies do not broaden exec grants. Cancellation/revocation invalidates outstanding
questions. No secrets requested in forms. A's wait tool offers MCP form elicitation
only when advertised; otherwise returns the exact question for a minimal chat reply.
Native UI approval dialogs cannot be intercepted by generic MCP: Bob must use the
relay question tool. Elicitation is delivered during an active A tool call; an idle
chat cannot be reliably awakened. B's own account/host permissions remain with B.

## Validation and delivery
Real subprocess tests for edits/create/rename/delete, tests/Git, interactive input,
output bounds, timeout/revocation and idempotency; durable question ownership/replay;
real TLS and two MCP sessions including elicitation accept/decline and fallback.
Update both role skills and setup instructions. Existing manual flow stays tested.
No live rollout or provider calls. Native client support/token savings still require
acceptance. Commit and publish only to the existing private repository.
