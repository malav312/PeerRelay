# Automatic requester acceptance

This follow-up supports both Claude and Codex requesters with either responder.
Use disposable data and fresh client sessions. Updating installed files does not
update a running MCP process, Claude chat or Codex session. Stop old role sessions
and the relay before changing versions; restart all participants on this branch.
Existing pairing bundles may be reused. Do not delete an existing checkout/pair.

## Install

On both laptops, choose a new checkout name:

```sh
git clone --branch codex/seamless-requester \
  https://github.com/malav312/PeerRelay.git "$HOME/PeerRelayRequesterTest" &&
cd "$HOME/PeerRelayRequesterTest" &&
uv sync --frozen
```

Alice starts the relay from the new checkout using the existing disposable pair:

```sh
uv run --frozen peer-relay serve \
  --config "$HOME/.local/share/peerrelay/integration-pair/server.json"
```

Keep that terminal open. Docker must be running for an execution pair configured
with Docker. This server performs the completion-column migration automatically;
legacy records retain their summaries. A server restart interrupts outstanding
work and invalidates old responder leases. Start Bob before enabling Alice.

Bob can use the tested automatic Codex responder on Codex CLI 0.171.0:

```sh
uv run --frozen peer-relay codex-responder \
  --config "$HOME/.local/share/peerrelay/integration-responder/client.json" \
  --directory /private/tmp/peerrelay-bob-requester-test-1
```

Choose a new short directory if it already exists. Bob signs in normally; no API
key or copied provider credentials are part of this setup. Claude responders can
use their existing response-mode workflow; their automatic delivery is a separate
native acceptance case.

## Alice using Claude Code

Prepare a new chat with explicit channel opt-in:

```sh
uv run --frozen peer-relay client-setup \
  --client claude \
  --config "$HOME/.local/share/peerrelay/integration-pair/requester/client.json" \
  --directory "$HOME/PeerRelayRequesterAlice" \
  --background claude-channel &&
"$HOME/PeerRelayRequesterAlice/start"
```

The generated command selects PeerRelay as the sole MCP server, removes built-in
tools, and passes `--dangerously-load-development-channels server:peerrelay`.
Claude's custom channels are in research preview. The human must accept Claude's
native development-channel confirmation; organization policy and provider support
still apply. PeerRelay does not answer that prompt. The flag can be hidden from
`--help`; unsupported native versions may reject it at launch.

1. Invoke `/requester-mode`. Authorize execution only for the intended disposable
   container workspace. Confirm the project/backend reported by the relay.
2. The skill uses `probe_background` once. A channel notification arrives and Alice
   acknowledges its watch/event ID. The tool response alone cannot verify delivery:
   the event ID is delivered only in the notification. `requester_diagnostics` or
   `background_status` should report `verified` for this mode afterward.
3. If delivery remains unverified or blocked, do not claim automatic acceptance.
   Use the supported native setup or the existing bounded-follow workflow. Never
   resubmit a task just because a notification did not arrive.

The normal follow budget is 50 seconds. Waiting in that tool uses code, including
native question dialogs. When it returns `waiting`, code automatically watches
that task. Later question/completion notifications cause the agent to acknowledge
and follow the same task. No manual watch registration or repeated model polling.

## Alice using Codex

Use a separate test run; exit the Claude mode first. Codex CLI 0.171.0 is required.

```sh
uv run --frozen peer-relay codex-requester \
  --config "$HOME/.local/share/peerrelay/integration-pair/requester/client.json" \
  --directory /private/tmp/peerrelay-alice-requester-test-1 \
  --execution
```

Omit `--execution` for proposals-only mode. The command opens a dedicated native
Codex terminal using Alice's own sign-in and owns exactly one requester mode.
**Do not activate another requester mode inside it.** `requester_session` retrieves
the existing mode and configured workspace. The model forwards tasks through the
bound tools; code automatically watches them and starts turns only for questions
and terminal states. Follow returns immediately. Questions appear as normal chat
questions, and the user answers normally; the bridge never invents decisions.

This does not attach automatic delivery to an arbitrary existing desktop chat.
The same restrictions as the Codex responder apply: read-only writes, no native
shell/apps/browser/delegation, other MCP registrations disabled for this session.
This is not total local-read isolation. The fresh chat directory is not Alice's
edited Git project, so use task-only links until actual-project setup is integrated.

## Test meaningful wakeups

Use a new folder name for every independent run. Send this ordinary message:

```text
Create a new folder named requester-auto-demo; stop if it exists.
First run a command that waits 65 seconds. Then ask me which filename to use:
notes.txt, summary.txt, or a custom filename. Wait for my answer.
Create that file containing "question test passed" and a newline, verify its bytes,
then run a command that waits another 65 seconds before completing.
Do not modify baseline.txt. Keep your completion brief.
```

The waits deliberately exceed Claude's foreground follow budget. Do not prompt
Alice or Bob to check progress during the waits. Answer `custom.txt` when asked.
Expect one question and one completion, with no repeated notifications for idle
or running status. A successful structured completion should omit the raw summary
and successful command history from Alice's routine tool output. Its `display`
already contains file/line counts; no repeated narration or default browser link.

Then ask to append `follow-up passed` as the second line and verify both lines.
It must use the same mode, submit once, and deliver completion automatically.
Ask for task-only changes to obtain the local viewer link. Ask a substantive
question/report and confirm `get_result` still delivers the requested answer.

For negative cases, submit a long-running disposable task and cancel it, then
verify no new task was submitted. Repeat with Bob disconnecting, and finally exit
requester mode or close the dedicated session. No post-exit wake or effects should
be initiated. Check stopped reasons in Claude's `background_status`; Codex retains
the local `delivery.sqlite` journal for diagnostics, never automatic replay.

## Scope and evidence

Structured completion v1 carries status (`completed`, `partial`, `blocked`),
verification (`passed`, `failed`, `not_run`, `not_applicable`) and up to four
limitations of 300 characters. Partial/blocked outcomes, failed/unrun checks,
limitations and failed commands remain visible. Reports use `answer`/`get_result`.
Legacy or malformed completion metadata preserves the narrative summary rather
than silently hiding warnings. Both peers and the relay should run the new version
for structured receipts; omission of the optional metadata retains the legacy RPC.

Claude allows eight concurrent watches, a one-hour watch lifetime and a 90-second
acknowledgement deadline. Expired, failed or unacknowledged watches report stopped
reasons; they do not repeatedly wake the model. Capacity exhaustion preserves an
already submitted task's resume ID and reports unavailable automatic delivery.
Codex rejects new submissions at its 128-task watch limit before remote effects.
Explicit recovery follows existing tasks; it does not automatically replay them.

Automated validation uses real relay handlers, local TLS/stdio, and scripted native
turns. A credential-free native Codex probe with a real TUI verifies requester tool
dispatch, a question wake, one submission, duplicate suppression and local write
rejection without external inference. Claude parser configuration and channel
transport are tested; the live native confirmation and idle wake require this
human test. Byte reductions in synthetic payloads are not measured provider tokens.

Record client versions, commit, probe verification, automatic question/completion,
follow-up pickup, receipt wording, cancellation/disconnect, and any manual wakes.
