# Owned native delivery

This branch implements Codex session ownership, durable event delivery and relay
waiting in code. It includes a runnable acceptance demo using a disposable real
relay and scripted native turns, plus the experimental `codex-responder` launcher
for an existing responder bundle. The `codex-requester` launcher now binds an
existing requester bundle to its own mode and registers submitted tasks in code. The launcher owns role activation and binds
task tools to its exact native thread and lease. See [client sessions](client-sessions.md#dedicated-automatic-codex-responder-experimental)
for launch instructions. Existing installed chats are not retroactively connected
to this controller; native model/TUI acceptance remains pending.

The chosen Codex experience is a dedicated native terminal using that person's
own sign-in. An arbitrary existing desktop conversation is not a delivery target.
Claude requesters can opt in through `client-setup --background claude-channel`;
mode-bound probe acknowledgement verifies notification receipt. All four
Claude/Codex requester/responder pairings remain in the product scope.

## What runs now

`OwnedCodexSession` creates a new private directory and app-server Unix socket,
records a random server generation, starts exactly one thread and returns the
terminal command containing that exact thread and socket. It never searches
thread lists or resumes an existing journal. Creating a session in an existing
directory fails. Cleanup closes only the owned processes and original socket.

The fresh no-turn thread receives a name metadata update before bounded resume
readiness. On Codex CLI 0.171.0, retrying resume alone timed out with “no rollout
found”; the metadata write made the same no-inference path resumable. Only that
specific readiness error is retried. Other errors stop startup.

`Binding` records role, mode ID or lease ID, thread ID and server generation.
`DeliveryController` records each meaningful event in a private SQLite journal.
It writes submission intent durably before `turn/start`, reads native status before
delivery, queues while busy and reconciles known turn IDs from notifications and
history. Repeated status markers produce one turn. Obsolete pending questions are
discarded. The wake message contains control metadata, not peer prompt/answer text;
the agent retrieves current task data through its bound MCP tools.

A timeout, cancellation or malformed reply during a start is uncertain delivery.
It stops dispatch and is never retried, even after an idle status or unrelated
turn completion. `clientUserMessageId` is correlation data, not a claimed native
deduplication guarantee. Existing journals are inspectable with
`DeliveryController.inspect(path)` but cannot be reopened for dispatch. A durable
`submitting` record from a crash is reported as uncertain.

`RelayMonitor` reads `response_update` and renews the exact responder lease every
15 seconds. Requesters register exact task IDs and read mode/task status. Ordinary
queued/running status does not start a model turn. New work, a ready answer, a
question or terminal task status can start one. Native loss, relay failure, changed
workspace, mode exit or lease revocation stops waiting and discards pending wakes.
Disconnect is a local controller stop, not a final model call under revoked authority.
Stopping the monitor stops heartbeat; it does not create, reactivate or disconnect
an authority it did not acquire. The dedicated launcher explicitly disconnects its
owned lease on session close; relay expiry is the fallback if that request cannot arrive.

`NativeRPC` uses the maintained `websockets` package, has bounded responses and
notification queues, and performs no automatic reconnect. Metadata operations are
the default allowlist; the dedicated launcher explicitly supplies `allow_turns=True`.
Its dynamic-tool bridge handles only registered responder tools for the exact
thread and lease. Other native server requests stop this transport with
`native_request_requires_ui`; it never invents approval or elicitation answers.
`run_delivery` joins monitor, notification, server and terminal lifetimes.

## Run the deterministic acceptance demo

From a checkout with project dependencies installed:

```sh
PYTHONPATH=src .venv/bin/python scripts/automatic_delivery_demo.py
PYTHONPATH=src .venv/bin/python -m pytest tests/test_delivery.py tests/test_native_codex.py -q
```

The demo creates temporary local relay state, activates disposable roles, waits
idle, delivers a task, asks and answers a question, completes it, checks duplicate
suppression and disconnects. It uses no network, credentials, native executable or
model provider. Expected report:

```json
{
  "idle_turns": 0,
  "responder_turns": 2,
  "requester_turns": 2,
  "question_answer": "custom separator",
  "task_state": "succeeded",
  "after_disconnect": "stopped",
  "provider_calls": 0
}
```

The scripted native turns establish controller behavior, not native model behavior
or approval enforcement. The transport test additionally uses a real local Unix
WebSocket server; restricted test environments need local socket permission.

## Run native metadata acceptance without inference

On macOS with Codex CLI available:

```sh
PYTHONPATH=src .venv/bin/python scripts/probe_owned_codex.py
```

Add `--terminal` to also attach a real native TUI through a disposable PTY. This
checks remote-resume compatibility, the same thread and policy after attachment,
and zero model turns. Only the empty disposable fixture directory is pretrusted;
the probe does not answer approvals or send model prompts.

The probe creates a credential-free native home and dummy unauthenticated localhost
provider, denies outbound IP traffic with `sandbox-exec`, and permits metadata RPCs
only. It verifies exact-thread resume, idle status, an empty turn history, exact
terminal arguments, owned-server shutdown and socket cleanup. The default probe
does not launch the terminal; neither mode makes a model call. An enclosing sandbox that disallows a nested
sandbox or a Unix listener needs permission to run this isolated probe.

The production transport passed this probe on Codex CLI 0.171.0 with zero turns.
A preceding standalone spike demonstrated a terminal observing controller metadata
on the same exact thread; that is separate evidence, not this probe's assertion.

## Exercise native tool dispatch without external inference

```sh
PYTHONPATH=src .venv/bin/python scripts/probe_codex_tool_roundtrip.py --terminal
PYTHONPATH=src .venv/bin/python scripts/probe_codex_tool_roundtrip.py --terminal --host-disabled
```

The first command uses a local scripted Responses provider and an attached native
terminal to route `exec` calls through `ResponderBridge`. It verifies `wait_for_task`,
`remote_read`, the runtime tool inventory, and rejection of a disposable local
`apply_patch` write. The second is a negative control: it reproduces
`code-mode host is disabled` and verifies that no bridge call occurs.
Each runs one synthetic turn with no external provider inference or account
credentials. Outbound connections are restricted to localhost. These checks cover
native dispatch and policy enforcement, not a live model's choice of tools.

## Requester delivery

See [automatic requester acceptance](seamless-requester-test.md) for both clients.
Codex uses immediate tool status plus owned native event delivery. Claude uses
bounded follow plus a task watch when waiting expires. Only questions and terminal
states wake the requester; tool output consumed in the foreground is not echoed.
The native requester probe is:

```sh
PYTHONPATH=src .venv/bin/python scripts/probe_codex_tool_roundtrip.py --requester --terminal
```

The historical integration checklist below predates these requester adapters.
Actual-project setup and ordinary bootstrap-to-native-launcher integration remain
future work; current launchers use existing bundles and fresh private sessions.

## Original integration checklist

The dedicated responder command now covers native startup, exact lease activation,
tool registration and owner cleanup. Remaining work includes bootstrap/role-command
launcher integration, requester delivery and Claude channel setup. The points below
describe that broader integration; they are not prerequisites for trying the
explicit experimental responder command.

1. Extend `client-setup` and its Codex launcher with the intended role-only MCP
   configuration and role skill. Keep provider authentication inside the person's
   native client. Verify all local tool restrictions before enabling peer work;
   read-only sandbox configuration alone does not establish that boundary.
2. Start `OwnedCodexSession` and its terminal, then use a private local control
   channel to bind the exact successful `requester_mode` or `response_mode` result
   to that thread and generation. Do not activate a second mode/lease in the
   controller. Construct `DeliveryController` and `RelayMonitor` from that binding.
3. On successful requester `submit`/`send_and_follow`, register the returned task ID
   with `watch_task`. Preserve the original operation ID; neither monitor nor
   dispatcher submits or retries the task. Route mode exit, responder disconnect
   and native close to controller stop and owner-authority cleanup.
4. Establish native TUI/controller ownership for approval and elicitation requests,
   terminal readiness, and user turns racing automatic starts. Observing idle is
   not an atomic reservation; native arbitration must be accepted before claiming
   exclusive turn ownership. Then connect `run_delivery` and explicitly enable
   turns. Render concise waiting/stopped state from controller state outside model
   turns, including failed or uncertain delivery.
5. Claude needs setup to pass `--background claude-channel`, explicit native
   development-channel enablement where native policy permits it, an acknowledged
   channel handshake, lease-aligned watch lifetime and visible watch failure.
   Notification write success alone is not native acknowledgement. Do not bypass
   a native policy that disallows channels.

After those bindings, test each of Claude→Claude, Claude→Codex, Codex→Claude and
Codex→Codex in fresh disposable sessions: idle consumes no model turns; a task
wakes the responder; an answer resumes that task; requester questions and completion
appear without progress prompts; repeated events do not duplicate work; active
native turns defer delivery; disconnect revokes access; transport loss during start
stops rather than replays. Record native approvals, elicitation, local-tool probes
and provider usage separately. No such inference acceptance was run for this stage.

Protocol reference: [Codex app-server](https://learn.chatgpt.com/docs/app-server).
