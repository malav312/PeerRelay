# Remote commands and questions

This mode lets Bob's native agent act on Alice's existing workspace with full
command execution. Alice keeps her original chat as a compact task/question UI.
It supersedes proposal-only editing only for explicitly authorized exec sessions.

## Enabling it

For a new pair use `peer-relay init ... --grant exec`. Docker is the default
backend; preinstall/start Docker and pre-pull a trusted image. See [security](security.md)
for mount, network, dependency and Git worktree compatibility limits.

For an existing exec pair, disconnect Bob and stop the relay before upgrading.
Preserve root, keys, tokens and all journals. Set an explicit `execution_backend`
in server.json; legacy exec configurations now fail closed rather than silently
retain unrestricted host access. Choose one deliberately:

```json
{"kind": "docker", "image": "python:3.13-slim"}
```

Or, only if Alice accepts access to her relay/provider credentials and host persistence:

```json
{"kind": "host", "allow_host_credentials": true}
```

Keep `grant: "exec"` for command access. ro/rw pairs do not gain exec automatically.
Update both installed skills and restart native clients so the new tool catalog is
loaded. Existing exec modes from before backend binding require one deliberate new
activation after this upgrade. Subsequent reconnects reuse that new mode normally.
Do not rerun init over an existing pair or delete journals to bypass binding.

In Alice's chat explicitly say: "Enable requester mode with command execution on
my registered workspace using the configured backend." The skill calls
requester_mode(execution=true). Verify its returned workspace matches the checkout
used by that conversation. Bob activates response mode in his own chat. A task
claim reports writes=commands only for this execution-enabled requester mode.

For ro/rw or execution=false, the old proposal workflow remains available; no shell
is admitted. A text reply to a question cannot upgrade these permissions.

## Command lifecycle

Bob uses remote_execute(command, cwd, timeout, terminal) with lease/task/operation
IDs. cwd defaults to the project root; subdirectories are resolved and checked.
The same operation ID and exact payload returns the original command ID. A changed
payload under the same ID is refused. Durable intent precedes process spawn.

Commands use `/bin/sh -c` in the selected backend with the environment described
in the [security guide](security.md). Host mode uses a disposable HOME; container
mode mounts only the project and uses temporary HOME/tmp. For interactive programs request terminal=true; otherwise
stdin/stdout pipes are used. Optional PTYs support terminal-aware input/output;
this is not a complete terminal emulator, desktop session, or login shell.

wait_for_command returns combined stdout/stderr, state, exit_code, next_offset,
truncated and detail. Follow byte offsets; each page is at most 16 KiB of raw output
rendered as UTF-8 with replacement for invalid/split sequences. A running command
can return output before exit. Drain remaining output after exit when needed.
Check terminal state and exit code before reporting success. Logs retain the first
1 MiB, then continue draining/discarding excess output to avoid blocking the process.

remote_input sends up to 16 KiB and optional EOF with its own stable operation ID.
Receipts report sent/partial/uncertain and bytes_written; repeat of the same ID does
not resend. Inspect a partial receipt before intentionally sending only the remaining
suffix. Uncertain input must not be automatically retried under a fresh ID.
PTY EOF sends Ctrl-D; a program's terminal discipline determines its interpretation.

Cancellation requests SIGTERM then SIGKILL for the process group. Timeout does the
same. Stops revoke new effects immediately; process termination completes asynchronously.
A command's already-made edits, network requests or installs are not rolled back.
Escaped process groups/privileged children can survive. A server crash cannot be
promised to stop its children; unfinished jobs become uncertain after restart.
No persisted PID is blindly killed later and no interrupted command is replayed.

## Worktree and branch behavior

Activation captures the registered root's path/device/inode and Git worktree/branch.
Commands start there, not in Bob's checkout. No branch is created or selected for
Alice. Commits on the same branch are allowed; a branch switch/root replacement
blocks subsequent commands and input until deliberate activation of a new mode.
An authorized command can switch branches, but does not silently rebind the mode.
Existing external writers and arbitrary commands do not obey the filesystem guard;
these checks are not a transactional guarantee against concurrent branch changes.

## Question lifecycle

Bob calls ask_requester with a stable operation ID, message and up to eight
suggested choices. One question can be pending per task, with at most 32 total.
The pending question blocks new commands/input and task completion. It does not
suspend an already-running program: Bob should cancel that program if the decision
requires stopping it. Command timeouts continue while waiting for Alice.

Alice's `submit_and_follow` submits once and continues waiting in code through
consecutive native questions until completion or deadline. `follow_task` resumes
the existing task. Diagnostics reports advertised form capability and optional
connection-test response. Unsupported clients return a labeled question for Alice
to answer through `answer_question`; no automatic choice is made. The compatibility
`wait_for_update` tool remains available. Replies stay bound to task/mode and cannot
be changed by a later retry. Bob retrieves them through `wait_for_answer`.
A declined action is not authorized; choose an authorized alternative or report the
block. Cancellation/revocation invalidates pending replies.

No default answer, elapsed time, model output or lost request counts as approval.
A tool cancellation during elicitation leaves the question pending for later delivery.
Only non-sensitive questions use this channel. Authenticate to external services
through their normal local flow; do not send passwords or tokens as question answers.

Optional Claude channels can deliver actionable events to an idle session; this is
opt-in, capability/client dependent, and native delivery still needs acceptance.
Codex retains bounded follow calls. See [follow-through](follow-through.md). Generic
MCP does not intercept all native permission dialogs: Bob's own account/host prompts
remain with Bob. For Docker, cleanup removes the container; failure becomes uncertain
and blocks restart until cleanup can be verified. Host escaped processes remain a risk.

## First acceptance task

Use a disposable project. Alice asks: "Create greeting.txt containing hello, run a
command that verifies it, then ask me whether the final format should be JSON or
CSV. Apply my choice and report the command exit codes." Confirm the file changes
without manual approval, the question reaches Alice and Bob receives her answer.
Then start a disposable long-running command, explicitly disconnect Bob and check
that new commands are refused and the managed process is terminated. Finally verify
that the project branch/worktree is still the one Alice selected.
