# Persistent requester sessions and meaningful updates

Activate once with `requester_mode`, retain `mode_id` in the chat's continuation
summary, and verify the workspace. An exec pair still needs Alice's explicit
`execution=true`. Use `requester_diagnostics(mode_id)` to check relay connectivity,
workspace identity, execution backend, responder connection and the actual MCP
client name/version and advertised form support. `test_form=true` optionally sends
a harmless connection-test form; `response_received` is evidence of a protocol reply,
not proof that a particular UI was displayed or a human answered.

For each task call `submit_and_follow(mode_id, operation_id, prompt, seconds)`.
The controller submits idempotently and polls in ordinary code. Supported native
questions are presented, replies forwarded, and waiting continues through subsequent
questions until task completion. The requester model does not run between those
polls or native answers. Full results/output remain opt-in; the default is compact.
This does not make the surrounding native model turns free or measure provider usage.

The default wait is 50 seconds to fit common client limits. Configure a longer
client tool timeout and use a matching `seconds` budget (maximum 3600) for longer
work. Allow at least 15 seconds of transport margin. For Codex, for example:

```toml
[mcp_servers.peerrelay]
# Keep the existing command and args.
tool_timeout_sec = 930
```

Then use `seconds=900`. Claude Code has its own tool timeout settings; configure
those for the client/version you actually run. Do not interpret timeout as failure
or create a second task. `follow_task(mode_id, task_id, seconds)` resumes the same
record. Retrying submission uses the same operation ID and identical prompt.
Cancellation of a wait leaves remote work running; `cancel_task` explicitly stops
that task. An unanswered or timed-out form remains pending, never auto-approved.

Without form support, the call returns `needs_input` with Bob's labeled question.
Alice's agent displays it and waits for Alice, then forwards her actual response
with `answer_question` and calls `follow_task`. A rejected credential-shaped answer
stays pending. If a wait expires, it returns `waiting` and durable resume IDs.
Without a background adapter, later progress still needs a user-triggered resume.

## Follow-ups and reconnects

Use the same mode ID for later tasks. `resume_requester_mode(mode_id)` reads the
existing authority; it does not create a new mode or reset execution to false.
Bob's reconnect creates a new responder lease but does not replace Alice's mode.
New tasks in that mode keep execution permission and the last three successful
prompt/summary pairs as bounded context. This is not a full shared transcript.
Interrupted tasks stay interrupted and require reconciliation, not automatic replay.
Closed modes and changed workspace/branch bindings cannot be silently resumed.

A client must retain the explicit mode ID. PeerRelay deliberately does not infer
"the latest mode" globally: one MCP process may serve multiple chats. If the chat
loses its receipt, recover it from that chat rather than borrowing another chat's
mode or silently creating a new proposal-only session. Local skill instructions
still depend on client adherence; the protocol does not provide a portable chat ID.

## Optional Claude Code channel adapter

Start the local MCP server with `--background claude-channel` in its registration.
This is off by default and declares only `claude/channel`, never permission relay.
For an existing registration, update its arguments and restart the client.
During Claude's research preview, a custom server also requires an interactive
client launch with explicit development-channel opt-in:

```sh
claude --dangerously-load-development-channels server:peerrelay
```

This skips the channel allowlist for this named development server, not organization
policy or tool approvals. Use it only for your trusted local PeerRelay installation.
Follow the client confirmation; do not bypass it. Support varies by client surface,
version, protocol runtime and organization. The server reports configured/unverified
channel support, not guaranteed delivery. Claude can silently drop notifications
when the client has not enabled the channel.

In an opted-in Claude requester, a timed-out follow automatically registers a watch.
`watch_task(mode_id, task_id)` can also register one explicitly. On the responder,
`response_mode` registers a watch when supported; `watch_requests(lease_id)` can
re-register. Polling stays in Python. Notifications contain only generated IDs and
fixed event types: task available/reply ready for Bob, question/completion for Alice.
They never embed peer prompts, summary text, file contents or command output.

On a notification, call `acknowledge_event(watch_id, event_id)` then use `follow_task`
or `wait_for_task`/`wait_for_answer`. Acknowledgement means the agent received the
event; it is not consent. Unacknowledged events retry at most three times, 30 seconds
apart. Watches are limited to eight per MCP process and expire after one hour.
`stop_watching` stops delivery without cancelling work. Disconnect stops responder
watches. Lost authority/connection ends a watch without automatic reconnect. A
restarted MCP process needs explicit watch registration; task IDs stay durable.

Codex uses the portable bounded flow. PeerRelay does not claim idle push support
for Codex or implement a new model process to imitate it. Native Claude channel
receipt/wakeup and Codex dialog behavior still need live client acceptance tests.

References checked for this implementation:
[Claude channels](https://code.claude.com/docs/en/channels-reference),
[Claude MCP support and timeouts](https://code.claude.com/docs/en/mcp), and
[Codex MCP timeouts](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
The installed Python MCP SDK is 1.30.0; its custom channel wire payload is tested
separately from native-client rendering.
