# PeerRelay

Alice keeps a small requester chat. Bob offers his own active Codex or Claude Code
agent to do the reasoning. PeerRelay routes work to Bob and, with Alice's explicit
execution permission, runs Bob's commands on Alice's machine in her existing
project/worktree. Each person stays signed into their own account. PeerRelay
makes no provider/model calls and does not copy provider credentials.

Follow the [two-laptop setup guide](docs/setup.md). Python 3.13+, uv, Git and a
signed-in native client are required on each laptop; current support is macOS/Linux.
This is an experimental developer preview. Both people can clone the public
repository; pairing credentials and journals must stay private. Mixed Codex/Claude
native sessions still need live acceptance testing.

## Full coding sessions

1. Alice runs `peer-relay pair --grant exec` outside her project and reads the
   spoken code aloud; Bob runs `peer-relay join` and types it (same Wi-Fi,
   SPAKE2, nothing secret transferred). Alice then starts the HTTPS relay.
   `exec` explicitly permits trusted-host command execution.
2. (Legacy alternative: `init` plus a privately transferred `responder/` bundle.)
3. Each installs its role's MCP configuration and skill. Bob activates response
   mode; Alice activates requester mode with explicit execution authorization.
4. Alice gives a task. Bob reads files and uses remote commands to create/edit/
   rename/delete files, install dependencies, build, run tests and use Git on Alice.
   Routine changes are applied directly without per-file manual approval.
5. Bob sends project questions through the relay. Alice answers in her original
   chat using native MCP elicitation when supported, or a short chat reply.
6. Alice receives compact states and Bob's summary. Either person can stop.

```sh
uv sync --frozen
.venv/bin/peer-relay init \
  --project /absolute/path/to/alice-worktree \
  --pair-dir /absolute/path/to/private-pair \
  --host 192.168.1.100 --port 9443 --grant exec
.venv/bin/peer-relay serve --config /absolute/path/to/private-pair/server.json
```

Replace the host with Alice's reachable LAN address. Keep the server running.
Never put pairing data inside the project. Transfer only `responder/`, not Alice's
requester token or TLS private key. See the setup guide for client commands.

## Permissions and branches

`exec` commands run as Alice's relay OS user, with that user's filesystem/network
permissions. This is trusted-host access, **not a project sandbox**. A working
directory check does not prevent commands from accessing other host paths. Use it
only with a trusted responder and tasks whose effects Alice authorizes. For OS
isolation, run the whole relay in an appropriately restricted account/container.
Native agents' own host tools are not disabled by an MCP skill.

The mode records the exact registered root and Git worktree/branch. No branch is
created or switched automatically. Existing uncommitted changes stay in place;
ordinary command tools can modify them. Subsequent commands stop if the root or
branch changes; deliberately activate a new requester mode to continue there.
Commands themselves may perform an authorized branch switch. There is no automatic
commit, stash, reset or rollback. Register the actual conversation worktree, not
another checkout of the same repository.

New pairs still default to `ro`. Existing `rw` pairs retain replacement proposals
with [human-local approval](docs/local-approval.md); they do not gain shell access.
An `exec` pair also needs requester activation with `execution=true`. Existing
configs without a grant keep their historical `rw` behavior. No upgrade silently
rewrites private pairing state or permissions.

## Questions, waiting and stopping

Question records and accept/decline/cancel replies are bound to Alice's task.
Replies never expand execution permissions. Bob must use `ask_requester` for
Alice's decisions; generic MCP cannot intercept every native agent approval dialog.
Bob's own account/client permission prompts remain with Bob. Do not send secrets
through question forms or chat replies.

Elicitation is delivered during an active requester wait. If Alice's client lacks
form support, the question is returned for a short chat response. An idle chat
cannot reliably be awakened: after a wait times out, Alice asks for progress/resume.
Waits/heartbeat/polling are ordinary code, but model turns around them can cost
usage. Actual native token savings and client persistence still need acceptance.

Commands have stable IDs, bounded output, optional PTY/stdin, timeout and cancellation.
Disconnect, cancel, mode exit and lease expiry revoke further execution and request
process-group termination. Completed effects are not rolled back. Escaped/detached
or privileged processes may survive; process groups are not OS containment. A server
crash leaves unfinished records uncertain, never automatically replayed. Reconnect
requires explicit activation; do not resubmit uncertain effects under fresh IDs.

## Scope and limits

One project and responder per pair; 32 pending tasks, 128 admitted tools per task,
32 proposals and 32 questions per task. Commands allow four active jobs per task,
1..86400 second timeouts (default 300), 1 MiB retained combined output, 16 KiB pages
and input. Read tools handle bounded NUL-free UTF-8 text; commands can operate on
other file formats. No transaction covers multiple files or arbitrary shell effects.
Large logs are truncated; write a project log deliberately when full output matters.

The command environment includes PATH/HOME/USER/LOGNAME/LANG/TMPDIR when present,
TERM and GIT_TERMINAL_PROMPT=0; arbitrary server variables are not inherited. Use
explicit virtual-environment executables or commands such as `uv run` as appropriate.
This does not prevent trusted commands from reading Alice's host files or shell profiles.
The command timeout must cover the intended work, including interactive waiting.

Private journals contain task text, questions/replies, command text/output and file
proposals. There is no automatic retention scheduler in this version. Protect them
and their backups. The generated certificate lasts 30 days; address/certificate
changes require a fresh pairing. Keep old journals rather than bypassing root binding.

See [execution details](docs/remote-execution.md) and [verification](docs/verification.md).
The tests use disposable projects and real TLS/MCP processes, not live provider tasks.
Filesystem foundation attribution is preserved in NOTICE/LICENSE.

## Development

```sh
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
```
