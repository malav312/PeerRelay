# PeerRelay session experience

PeerRelay connects two known people through their own coding clients. The intended
routine is: add the integration, activate a role, pair, and send ordinary messages.
Requester mode stays on until the person exits it. Responder work should begin when
a task arrives, without anyone asking it to check the queue.

## 1. Normal questions and short replies

Conversation behavior:

```text
Which separator behavior should I use?

1. Any string (recommended)
2. One character

Your answer: …
```

Questions no longer contain serialized JSON or repeated peer warnings. The reply
remains free text, so the person can choose an option or specify something else.
The native client controls the form's layout. Chat fallback shows the same question
and choices without internal IDs. An answered native form is not repeated in chat.

While requester mode is active, “Add a dark theme” is forwarded directly. “Any
progress?” follows the existing task; an answer resolves its pending question;
cancel and mode exit remain controls. No repeated “use PeerRelay” prefix is needed.
This is specified in the native role instructions; deterministic input interception
is a separate integration task.

Routine status uses the returned display: “Working.” or “Done.”. Completed execution
tasks with an available review add changed-file/line counts on the same line.
Review links stay in tool state until requested. No routine summary retelling,
verification disclaimer, workspace recap or follow-up offer. Connection checks
return “Bob is connected.” / “Bob is disconnected.”; exit returns “Requester mode off.”
“Done.” acknowledges relay completion; it does not assert independent requester
verification. The requester does not narrate command counts or reconfirm an accepted
native answer. A question is shown only when input is needed. Failed
commands, rejected answers and uncertain effects still receive a concise explanation.
Full answers, command history and diffs are on request, including requests in the
original task. Summary-reported limitations are still surfaced; proposals awaiting
approval say “Proposal ready for review.” Changes remain subject to
the existing project/backend permissions.

The MCP status view aggregates successful command history rather than sending it
back on every wait. `task_status(details=true)` retrieves the full history. This
reduces routine tool text; native token savings have not been measured.

The [read-only changes viewer](changes-review.md) shows a frozen task-interval diff
in the browser. Ordinary status returns counts and a link, not filenames or patch
text. For “show changes”, prefer native `/diff` when the edited project is open.
For a separate setup folder, explain that prerequisite and offer the task-only link.
Explicit task-only/browser viewing returns the link. No panel is opened by the skill.
Requests to explain or review changes still go to Bob through the relay.

## 2. Pairing from mode commands

Implemented setup layer: a stable bootstrap MCP starts before a pair exists,
accompanied by both role skills. Its local pairing view selects the project and
execution settings, performs the existing pairing exchange and can start the relay.
Pairing and authenticated relay readiness are reported separately from role
connection. Existing bundles remain usable; explicit chat bindings preserve setup
on resume. See [mode-driven pairing](mode-pairing.md) for setup and remaining
native-client acceptance.

A native dialog or local pairing view can present the code and confirmation. MCP
does not choose a top-right location inside another app; the host controls display.
A raw MCP add also does not portably install bare slash commands, so integration
packaging must include the entry skills. See the
[MCP elicitation contract](https://modelcontextprotocol.io/specification/2025-11-25/client/elicitation)
and [Claude MCP prompt conventions](https://code.claude.com/docs/en/mcp).

## 3. Automatic responder and requester updates

Claude has a native channel mechanism. PeerRelay already has an opt-in adapter;
setup still needs to enable it in the native client, verify an acknowledgement and
keep its lifetime aligned with the session. A notification write is not proof that
the client processed it. See [Claude channels](https://code.claude.com/docs/en/channels-reference).

For Codex, the chosen route is a dedicated native terminal session using the
person's own sign-in. A local controller binds to that specific session and wakes
it for meaningful events; it must not attach to an arbitrary existing chat. The
initial isolated transport probe established a shared native thread between a
controller and Codex terminal on CLI 0.171.0 without a model call. Task delivery,
turn ownership, approvals and reconnect recovery still need implementation and
acceptance. See [Codex app-server](https://learn.chatgpt.com/docs/app-server).

The [owned-session controller stage](automatic-delivery.md) now implements exact
Codex session lifecycle, durable meaningful-event delivery and code-only relay
monitoring, with a runnable scripted acceptance demo and a zero-turn native metadata
probe. Native MCP/launcher bindings and approval/tool-restriction acceptance remain
the next stage; installed chats do not gain automatic delivery from this layer.

Both sides should wait in code, without idle model polling. A task, needed answer,
completion or disconnect triggers delivery. Preserve operation IDs, reconcile
uncertain delivery and stop on explicit disconnect. All four client combinations
remain in scope. The current setup layer does not yet provide unattended Codex
delivery or native automatic session binding.

## Test the conversation slice

### Native diff shortcut

To view edits without asking the model to reproduce the patch, use `/diff` in
Claude Code or Codex CLI, or the Review pane in Codex desktop. Open the actual
registered Git project and select the working-tree/Current view. A requester chat
in a separate setup directory does not automatically point its viewer at that
project. PeerRelay's role instructions explain this shortcut when asked; they
cannot invoke a native panel or inject a built-in edited-files card. Native `/diff`
is a client command, not another task for the responder. Asking for analysis is
separate and still uses the relay. Routine completion does not repeat the hint.

See [Claude's diff viewer](https://code.claude.com/docs/en/interactive-mode#review-changes-with-diff),
[Codex CLI commands](https://learn.chatgpt.com/docs/reference/slash-commands), and
[Codex's review pane](https://learn.chatgpt.com/docs/code-review).

### Conversation acceptance

Use a fresh pair of native chats with this branch's code and updated role skills;
do not silently replace a running session. In requester mode, submit an ordinary
task without mentioning PeerRelay. Have the responder ask a short two-choice
question, enter a custom answer, then finish the task. Verify the form and fallback
contain no JSON/IDs/warning paragraph, the custom answer reaches the same task,
and the final reply stays short. Ask for details once to verify they remain
available. Existing delivery adapters may still require a manual resume during
this first-slice test. Do not label that unattended delivery acceptance.
