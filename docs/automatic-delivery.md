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

## Existing sessions and remaining acceptance

[Project installation](existing-session-setup.md) now registers both role skills and
the bootstrap MCP in an existing project. Claude channel opt-in propagates into bound
roles, with setup/binding metadata on notifications and asynchronous watch cleanup.
Native channel enablement and receipt acknowledgement still require the client.

Dedicated Codex sessions bind activation and submitted tasks to their owned native
thread. This controller does not attach to an arbitrary existing desktop/CLI chat.
Ordinary installed Codex sessions retain bounded follow; automatic wake there remains
unimplemented. Installing a skill or MCP configuration is not proof of that capability.

Live acceptance must distinguish the two paths. Test all four client/role combinations,
custom answers, follow-ups, native diff access, duplicates, cancellation, disconnect,
and transport loss. Record whether native channel approval was available and whether
manual wakeups were needed. Scripted native probes and local protocol tests do not
establish a real model's adherence to the role instructions or provider token savings.

Protocol reference: [Codex app-server](https://learn.chatgpt.com/docs/app-server).
