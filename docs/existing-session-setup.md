# Use PeerRelay in your existing project

Install once per project, then invoke a role skill in your usual signed-in client.
You do not need a separate chat directory or a PeerRelay launcher for this path.
This installs configuration; it does not take over a running conversation or open
a second model session. Reloading MCP settings or reopening the project may be
necessary for your client to discover it.

## 1. Install the checkout

On each laptop, use the test branch:

```sh
git clone --branch codex/seamless-requester https://github.com/malav312/PeerRelay.git
cd PeerRelay
uv sync --frozen
```

For an existing clone, check `git status` first, then fetch and switch to
`codex/seamless-requester` without discarding local changes. Keep this checkout and
its `.venv` at the installed location; MCP uses that Python interpreter.

## 2. Register in the project you already use

Run from the PeerRelay checkout. Replace the project path. On Alice, select the
actual project/worktree Bob should edit. On Bob, select the project open in his
client; this does not share Bob's project with Alice.

Claude Code (including its desktop Code surface where project MCP/skills are supported):

```sh
uv run --frozen peer-relay install --client claude --project /absolute/path/to/project
```

Codex:

```sh
uv run --frozen peer-relay install --client codex --project /absolute/path/to/project
```

Claude gets `.mcp.json` plus both `.claude/skills/` entries. Codex gets a managed
entry in `.codex/config.toml` plus both `.agents/skills/` entries. Existing unrelated
configuration is preserved. Rerun the same command to update owned skills; edited
skills or a conflicting PeerRelay entry cause an error before files change.

Private setup state defaults to `~/.local/share/peerrelay/projects/<project-id>/<client>`.
`--state-dir /private/path` overrides it; it must be outside the project. Credentials,
pairing codes and journals are not installed in project files. The local config
contains machine-specific paths: review before committing it to a shared repository.
This command does not change native trust, permissions, model selection or sign-in.

## 3. Reload and enter a role

Keep the actual project open. Reload MCP configuration using your client, or reopen
that project if the server/skills have not appeared. Accept the client's normal
project and MCP trust prompts. Codex skips project configuration in untrusted projects.
If the existing session still exposes an older PeerRelay server, reconnect/reload it
before activating a role; do not run the old and new modes simultaneously.

- Alice in Claude Code: `/requester-mode`
- Bob in Claude Code: `/response-mode`
- Alice in Codex: `$requester-mode`
- Bob in Codex: `$response-mode`

These skills are for Claude **Code**, not a claim that general Claude Desktop chat
loads Claude Code project skills. Native desktop discovery remains an acceptance test.

The role opens a local pairing page. Alice's installed project is prefilled and
editable before pairing; she chooses the access level/backend. Bob enters her
spoken code. Compare check numbers and confirm on both laptops. With **Start relay
after pairing** selected, Alice's relay starts automatically after confirmation.
A startup error keeps the saved pairing and offers retry; do not re-pair to recover.

Return to the chat and say "Pairing done" if it is waiting. Activate Bob's role first,
then Alice's. They verify relay readiness and the registered project. Existing bundles
can be selected in the pairing page; they keep their original workspace and backend.
An installed project is only a pairing default, not authorization to retarget a bundle.

While requester mode is active, ordinary messages and follow-ups go through PeerRelay.
Questions are normal short questions. Success uses a compact receipt. `show changes`
points to the native diff/review UI when the edited Git project is open; the task-only
browser diff remains available explicitly. Exit requester mode or disconnect Bob
explicitly when finished. The installer does not disable existing local tools; role
routing is skill guidance, while relay permissions are enforced by the server.

## Automatic updates: separate from installation

| Session | Current automatic delivery |
|---|---|
| Ordinary Codex desktop/CLI chat | Not implemented/verified. Bounded follow works; subsequent work may need a user resume. |
| Claude Code with channel explicitly enabled | Implemented through bootstrap; native approval and live acknowledgement required. |
| Dedicated native Codex requester/responder | Existing optional launcher path; not the same running chat. |

For Claude Code channel testing, rerun installation with explicit opt-in:

```sh
uv run --frozen peer-relay install --client claude \
  --project /absolute/path/to/project --background claude-channel
```

Then open Claude Code in that project with its native preview flag:

```sh
claude --dangerously-load-development-channels server:peerrelay
```

Complete Claude's own confirmation. This is a native startup requirement; a skill
cannot retroactively enable that flag in an already-running session. Organization
policy may prevent channels. Alice's role runs a harmless probe; only an acknowledged
event establishes delivery. Bob's channel watch starts with response mode. A bootstrap
event carries its exact setup and binding IDs, and all acknowledgements stay in that
binding. Waiting happens in code; meaningful model turns still consume usage.

## Acceptance test

1. Install into a disposable **existing** Git project containing an unrelated MCP
   entry and an uncommitted file. Confirm both remain intact. Rerun installation.
2. Reload both native clients and confirm each discovers the two role skills.
3. Pair through the skills with the intended root and Docker permissions. Confirm
   both pairing and relay readiness, then activate roles. No manual `serve` command.
4. Send "Create relay-demo.txt containing first line and verify it". With ordinary
   Codex sessions, explicitly resume Bob if needed; record this as manual delivery.
5. Send "Ask me which second line to add, then append my answer and verify both".
   Confirm the custom answer reaches Bob once and remains in the same branch/root.
6. Send a normal follow-up without mentioning PeerRelay. Check native diff/review and
   independently verify the file. Repeat in the other client/role combinations.
7. For channel-enabled Claude, delay the task beyond the 50-second foreground wait;
   verify question and completion arrive without status prompts. A setup/probe success
   alone is not evidence of task delivery. Record any channel policy restriction.
8. Cancel and disconnect; verify no new task or notification starts after exit. Reopen
   explicitly and recover the same task/setup rather than resubmitting uncertain work.

Local automated tests cover installer preservation/conflicts, real pairing and relay
startup, stdio role dispatch/forms, channel probe/ack binding, and watch shutdown.
Live native desktop/CLI acceptance is still required. Automatic wake in an arbitrary
existing Codex chat remains an unresolved client integration; it is not promised by
installation or by invoking the skill.

References: [Codex project configuration](https://learn.chatgpt.com/docs/config-file/config-basic),
[Claude Code project MCP](https://code.claude.com/docs/en/mcp),
[Claude channels](https://code.claude.com/docs/en/channels-reference).
