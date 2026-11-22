# Fresh native client sessions

Roles and clients are independent. All four combinations are supported by the
relay protocol: Claude → Claude, Codex → Codex, Claude → Codex and Codex → Claude.
Each person uses their own signed-in native client. This does not mean all four
combinations have passed native UI, elicitation and security acceptance.

Changing client preserves the role's bundle. Reversing people requires a pair
registered to the new requester's workspace and the corresponding role bundles.
Do not exchange requester and responder credentials to simulate a role change.

## Prepare either role

Disconnect an active responder before replacing its session. After pairing, run
these commands in the installed PeerRelay source checkout. Use a **new directory**
outside any shared project; setup refuses to overwrite an existing directory.

Alice:

```sh
.venv/bin/peer-relay client-setup --client claude \
  --config "$PAIR/requester/client.json" \
  --directory "$HOME/PeerRelayRequesterSession"
```

Bob:

```sh
.venv/bin/peer-relay client-setup --client claude \
  --config "$BUNDLE/client.json" \
  --directory "$HOME/PeerRelayResponderSession"
```

Use `--client codex` on either side for Codex configuration. The command derives
the role from the bundle; it installs only that role's skill. It writes private
MCP configuration, `setup.json` and `NEXT-STEPS.txt`. It references existing token
files without copying their contents. It does not contact the relay, activate a
mode, change global client settings, or restart an existing chat.

The dedicated chat directory is **not** Alice's remote working directory. The
relay's registered checkout and branch still control all remote operations.
Verify that workspace in requester diagnostics before submitting work.

## Claude Code launcher

Run the generated `start` executable from a human terminal, for example:

```sh
"$HOME/PeerRelayResponderSession/start"
```

It starts a new interactive native Claude Code process using the current sign-in.
It removes all built-in tools (`--tools ""`), limits MCP servers to the generated
file (`--strict-mcp-config`), disables Chrome integration and uses restricted mode
to ignore ordinary user/project/local settings. Only PeerRelay MCP tools receive
automatic permission; other permission requests are denied (`dontAsk`). No
additional launcher arguments are accepted. Missing required CLI flags stop launch.
Do not resume this chat through an ordinary unrestricted `claude` command.

Invoke `/response-mode` on Bob or `/requester-mode` on Alice in the new chat.
Inspect `/mcp`; only the intended PeerRelay role should be connected. Native
elicitation and the model's ability to use the installed slash skill still need
the acceptance test below. If unavailable, stop and report the limitation; do not
re-enable built-in tools as a workaround.

This limits model-accessible tools; it is **not an OS sandbox for Claude itself**.
The native client still manages its own login, settings and transcripts. Managed
policy, client changes, plugins and UI actions require inspection in the actual
installation. The setup report deliberately says `launch_controls_configured`,
not `isolation_verified`. CLI help checks detect missing switches, not security
regressions. Administrators and the human can still change the client configuration.

## Codex checkpoint

Setup installs `$requester-mode` or `$response-mode` and writes `mcp.json`.
`NEXT-STEPS.txt` gives the CLI MCP installation command, which changes the global
PeerRelay entry only if you run it. Desktop users can import the same command and
arguments through their client's MCP configuration. Restart and open the generated
chat directory to load the skill and MCP server.

**Complete local-tool restriction is not verified for Codex.** `client-setup`
does not generate a Codex launcher. The separate experimental `codex-responder`
command below provides dedicated native delivery with the documented limitations. `features.shell_tool=false` removes command tools, but it
does not establish removal of every local read, edit, app, browser or delegated
tool. A read-only sandbox allows local reads. Hooks have incomplete coverage and
failure behavior that prevents treating them as a complete enforcement boundary.
Use verified native policy or a restricted OS environment before accepting an
untrusted peer; otherwise mark the restriction checkpoint unresolved. This applies
to both requester and responder roles. An already-open chat is not retroactively
restricted by installing a skill or changing files.

## Acceptance after a fresh start

Record these as separate checks on each laptop; do not infer later checks from
earlier ones:

1. **Installed:** correct role skill, MCP executable and existing bundle path.
2. **Started:** a new native client loaded those files; inspect its tool inventory
   and ensure no unintended MCP server, shell, file, browser or delegation tool is
   available. Configuration generation alone is not evidence of this.
3. **Restricted:** before connecting a peer, use disposable markers outside the
   chat directory. Ask the agent to read a marker, overwrite another and execute a
   harmless local command. All must be unavailable/denied. The human checks the
   markers independently. Never use real credentials as test fixtures. Passing
   these probes is evidence for those paths, not proof against every client bug.
4. **Connected:** explicitly activate Bob first. Then activate Alice and verify
   her diagnostics reports the expected registered project and intended execution
   backend. Do not silently upgrade permissions or switch backend.
5. **Functional:** in a disposable registered project, create/edit a file through
   remote execution, inspect command exit codes, ask a JSON/CSV question, answer on
   Alice, and verify the requested result independently. Confirm the files changed
   on Alice's workspace, not Bob's chat directory.
6. **Disconnected:** Bob explicitly says “Disconnect PeerRelay now”; verify access
   is revoked. Closing the chat alone is not the disconnect test.

Setup tests exercise artifact generation and launcher arguments without model
calls. They do not certify the native client's enforcement, restart, question UI,
or unattended background delivery. Keep those results separate in a test report.

References: [Claude CLI controls](https://code.claude.com/docs/en/cli-reference),
[Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference),
and [Codex hook coverage and errors](https://learn.chatgpt.com/docs/hooks).

## Dedicated automatic Codex responder (experimental)

Bob can explicitly launch a new native terminal using an existing responder bundle:

```sh
peer-relay codex-responder --config /absolute/path/responder/client.json \
  --directory /private/tmp/peerrelay-bob-unique
```

Run this in Bob's human terminal with Codex CLI **0.171.0** already installed and
signed in normally. Choose a new, short private directory each time. The launcher
uses Bob's existing native sign-in; it does not copy credentials, create an API
worker, change global Codex configuration, or attach to an existing chat. Alice can
use the prepared Claude requester session and its existing requester-mode flow.
This command activates Bob's response lease after native startup checks; setup
alone does not activate anything. Start Bob before Alice sends work.

The launcher creates one owned app-server, exact thread, native terminal and lease.
It registers the existing responder MCP task handlers as native dynamic tools.
Calls from another thread, with another lease, or to activation/background tools
are rejected. Code watches for new work and answered questions; only those events
start native turns. Idle/pending task and answer waits return immediately so the
native model can end its turn. Closing the terminal, disconnecting, losing transport,
or an approval request stops delivery and revokes the owned lease where reachable.
An uncertain native turn start is recorded and never automatically replayed.

Startup checks enforce read-only sandbox writes, no approval escalation, disabled
shell/execution, apps, browser, web search, plugins and delegation features. Other
configured MCP servers cause startup to fail unless the command override clears
them. These controls **do not establish total local-read isolation**: native file
read facilities may remain. Bob must use a suitable OS account/container if local
read isolation is required. The responder instructions require requester work to
use the remote tools. The human native terminal retains its own controls; changing
its permissions is outside the validated configuration.

Validation includes a disposable TLS relay flow through the real MCP handlers and
an outbound-denied, credential-free native metadata probe with zero model turns.
Live model tool compliance, native human-turn concurrency and a signed-in TUI remain
manual acceptance checks. No provider inference is part of the automated suite.
