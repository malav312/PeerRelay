# Set up PeerRelay on two laptops

A owns the project and submits tasks. B offers their own active Codex or Claude
Code chat to do the work. Both people stay signed into their own accounts.
A runs the HTTPS relay; each laptop runs its own local MCP process.

Start with the disposable project below. This guide targets macOS/Linux local
clients; native Windows is not supported by the current filesystem locking code.
Both laptops need Python 3.13+, uv, Git, and an installed, signed-in native client.
They must be able to reach A's LAN address on TCP port 9443.

## 1. Install the source on both laptops

The source repository is public; both people can clone it without a GitHub account.
No separate PeerRelay account is needed. Each person uses their own signed-in Codex
or Claude Code client. Pairing data and credentials stay private.

This is an experimental developer preview. Start with the disposable project below;
live mixed Codex/Claude sessions are not yet verified.

Run on each laptop, from the folder where you want the repository:

```sh
git clone https://github.com/malav312/PeerRelay.git
cd PeerRelay
uv sync --frozen
RELAY="$(pwd -P)"
"$RELAY/.venv/bin/peer-relay" --help
```

If you already have the source, enter that directory and start at `uv sync`.
Keep `RELAY` set to this laptop's absolute repository path in subsequent commands.
Each laptop builds its own virtual environment. No provider API key is required.

## Quick path: pair with a spoken code (same Wi-Fi)

This replaces steps 2 and 3 below. No file is transferred and no secret is sent
over chat. Both laptops need to be on the same network and on this pairing branch.
With `--grant exec`, install/start Docker (or the Podman setup below) and pre-pull
`python:3.13-slim` on A before serving. The default is a project-only writable
container mount, no network and no host credentials. Dependencies must be in the
image. The pairing screen tells both people which backend is selected.

For deliberate unrestricted host execution, `pair` accepts
`--execution-backend host --allow-host-credentials`, just like `init`. Read the
[security tradeoffs](security.md) first. Omit `--grant exec` for read-only access.

On A, from the `PeerRelay` folder:

```sh
"$RELAY/.venv/bin/peer-relay" pair --project /absolute/path/to/project \
  --pair-dir /absolute/path/to/private-pair --grant exec
```

A sees a code such as `7-orbit-lantern` and reads it aloud. On B:

```sh
"$RELAY/.venv/bin/peer-relay" join --dir /absolute/path/to/new-private-folder
```

B types the code. B's screen shows A's device name, the project and the access
level. Both screens show the same six-digit check number; each person confirms.
A then starts the relay with the `serve` command it prints, and B adds the MCP
snippet it prints. Continue at step 4.

How it works:

- The number before the words is a public channel. It only picks A's pairing
  advertisement (mDNS) when several people pair on one network. A avoids
  channels that are already in use.
- The two words are the password for a SPAKE2 key exchange. A wrong guess
  reveals nothing useful offline. Each code allows 3 attempts and expires after
  5 minutes; afterwards run `pair` again for a new code.
- B generates its own responder token and sends it encrypted. A stores only
  its SHA-256, so A's disk never holds B's credential.
- B pins A's certificate, so a new DHCP address does not break the pairing;
  update `url` in both client configs and the server bind address if A's address changes.
- mDNS is advertised only while the code is open. If a network blocks it
  (common on guest or office Wi-Fi), use the `join --address HOST:PORT` line
  shown on A's screen. Pairing uses the relay port + 1 (default 9444).
- Running `pair` again on an existing pair folder replaces the responder and
  keeps the access level, execution backend, image and credential acknowledgement.
  Changing these options requires a new pair folder. Stop `serve` first.

## 2. A: create a demo project and pair

In A's terminal, choose a new demo directory:

```sh
PROJECT="$HOME/PeerRelayDemo"
PAIR="$HOME/.local/share/peerrelay/demo-pair"
mkdir "$PROJECT" && printf 'before\n' > "$PROJECT/hello.txt"
```

If either directory already exists, choose a fresh name. Replace the example IP
below with A's LAN address from network settings; do not use localhost for B.
On a Mac using Wi-Fi, `ipconfig getifaddr en0` may show that address.

```sh
# Install/start Docker first, then explicitly obtain a trusted image.
docker pull python:3.13-slim
"$RELAY/.venv/bin/peer-relay" init \
  --project "$PROJECT" \
  --pair-dir "$PAIR" \
  --host 192.168.1.100 --port 9443 --grant exec
"$RELAY/.venv/bin/peer-relay" serve --config "$PAIR/server.json"
```

New exec pairs default to Docker: project-only writable mount, no network or host
credentials, and a fresh container per command. Pre-pull a trusted image; for real
projects use a prepared image (prefer a digest) through `--container-image`.
See [security and compatibility limits](security.md). The container runtime is required only on A.

Podman can provide the local Docker-compatible endpoint: start `podman machine`,
confirm it exposes `/var/run/docker.sock`, and put the real `docker` CLI on the
relay process's PATH. PeerRelay still uses `--execution-backend docker`; a shell
alias from `docker` to `podman` is not sufficient. No remote context or socket
fallback is selected. `docker --host unix:///var/run/docker.sock version` should
show the expected local Podman server. Preload the image in that same runtime.
Podman 6.0.2 with Docker CLI 29.10.0 on Apple silicon has passed the runtime tests
in [verification](verification.md). Other versions need the same acceptance check.
Docker publishes a [standalone macOS CLI](https://docs.docker.com/engine/install/binaries/);
Docker Desktop is not required for this Podman arrangement.

If A deliberately wants unrestricted host commands, add
`--execution-backend host --allow-host-credentials`. This permits access to relay
secrets, provider logins and persistence; it is not a sandbox. A disposable HOME
does not change that authority. Omit `--grant exec` for read-only or use `rw` for
replacement proposals. Existing grants never upgrade automatically.

Leave that terminal running. In another terminal, restore the same `RELAY`,
`PROJECT`, and `PAIR` values before continuing. Allow inbound connections to
the relay through A's local firewall if prompted. Do not expose it through public
router port forwarding.

A keeps the complete pairing directory. It contains private tokens, a TLS key,
and durable state. Never put it inside the shared project or commit it to Git.

## 3. Transfer only B's bundle

A sends **only** `$PAIR/responder/` to B through a private file-transfer channel.
The folder contains exactly:

- `client.json`
- `responder.token`
- `gateway.crt`

B places that folder at `$HOME/.local/share/peerrelay/responder` and runs:

```sh
BUNDLE="$HOME/.local/share/peerrelay/responder"
chmod 700 "$BUNDLE"
chmod 600 "$BUNDLE/client.json" "$BUNDLE/responder.token" "$BUNDLE/gateway.crt"
```

Keep the relative paths in B's `client.json`. B does not need A's project copy,
requester token, server configuration, or TLS private key.

## 4. Configure each local client

Do this once on each laptop. Select the appropriate role values:

| Value | A | B |
| --- | --- | --- |
| `ROLE_CONFIG` | `$PAIR/requester/client.json` | `$BUNDLE/client.json` |
| `MODE` | `requester-mode` | `response-mode` |
| `CHAT_DIR` | `$PROJECT` | `$HOME/PeerRelayResponder` |

For example, on A:

```sh
ROLE_CONFIG="$PAIR/requester/client.json"
MODE="requester-mode"
CHAT_DIR="$PROJECT"
```

On B:

```sh
ROLE_CONFIG="$BUNDLE/client.json"
MODE="response-mode"
CHAT_DIR="$HOME/PeerRelayResponder"
mkdir -p "$CHAT_DIR"
```

Then choose **one** client setup below on each laptop. A and B may choose different
clients. Run client commands from `CHAT_DIR` so project-local configuration and
skills are loaded together. In an exec session Bob can run commands and make
changes directly; Alice answers project questions in her original chat. The older
proposal-only mode still uses the human-local approval CLI.

Before either client setup, restrict local shell/file tools in the native client
or run it in a restricted OS environment. A skill cannot protect either laptop
from peer prompt injection. See [both-side restrictions](security.md#restrict-both-native-agents-local-tools).

### Codex

```sh
mkdir -p "$CHAT_DIR/.agents/skills"
cp -R "$RELAY/plugins/codex/skills/$MODE" "$CHAT_DIR/.agents/skills/"
cd "$CHAT_DIR"
codex mcp add peerrelay -- "$RELAY/.venv/bin/peer-relay" mcp --config "$ROLE_CONFIG"
codex mcp list
codex
```

In A's chat invoke `$requester-mode`; in B's chat invoke `$response-mode`.
You can also select the installed skill through the client's skill picker.
These names are chat instructions, not terminal commands.

### Claude Code

```sh
mkdir -p "$CHAT_DIR/.claude/skills"
cp -R "$RELAY/plugins/claude/skills/$MODE" "$CHAT_DIR/.claude/skills/"
cd "$CHAT_DIR"
claude mcp add --transport stdio --scope local peerrelay -- \
  "$RELAY/.venv/bin/peer-relay" mcp --config "$ROLE_CONFIG"
claude mcp list
claude
```

In A's chat invoke `/requester-mode`; in B's chat invoke `/response-mode`.
This guide installs standalone skills, so there is no plugin namespace prefix.

If a skill directory already exists, inspect it before copying a replacement.
Restart an already-open client after configuration. Desktop clients must support
local stdio MCP; use the executable/arguments in [the JSON example](../examples/mcp.json)
and open the same chat directory. A remotely hosted chat cannot launch your local
executable merely because it appears in a configuration file.

## 5. Run the first task and a question

1. Activate B's response mode first and keep that chat active.
2. Activate A's requester skill and explicitly say: **Enable requester mode with
   command execution using my configured backend on the registered workspace.**
   Verify the returned workspace is the correct conversation checkout/worktree.
3. Tell A: **Replace hello.txt with hello from B followed by a newline, create
   greeting.txt, and run commands that verify both files. Then ask me whether
   the final format should be JSON or CSV and apply my choice.**
4. B executes on A directly. No per-file manual approval is needed. A receives
   compact command states and a brief summary. Check the files in A's editor.
5. A's wait shows native elicitation if supported, otherwise a question in chat.
   Answer it; the submit-and-follow call continues through further questions. For
   longer tasks, configure timeouts or opt into Claude background delivery using
   [follow-through setup](follow-through.md). Without that adapter, a timed-out
   wait needs a resume request; do not create another task.
6. Confirm B reports actual command exit codes and the requested result.
7. Tell B: **Disconnect PeerRelay now.** Further tasks/commands must be refused.
   Disconnect also requests termination of managed command process groups; already
   completed effects are not rolled back.

For ro/rw pairings, use the [local approval workflow](local-approval.md) instead.
For exec details, interactive commands, branch changes and question delivery limits,
see [remote execution](remote-execution.md).

## Stop, reconnect, or switch projects

- B disconnects explicitly before leaving response mode. Closing a chat may leave
  a shared MCP process alive. A lost process's lease expires within 60 seconds.
- A says **Exit requester mode** to resume ordinary local work in that chat.
- Stop A's relay with Ctrl-C. Restart it with the same `serve` command and state.
- Restarting the relay revokes prior leases. B must explicitly reactivate response
  mode; interrupted tasks do not resume automatically.
- For a follow-up after reconnect, call `resume_requester_mode` with this chat's
  original mode ID; retain permissions/context. Do not call `requester_mode` again
  unless deliberately starting a new session. See [diagnostics](follow-through.md).
- For another project, create a fresh pair outside that project using `pair`/`join`
  (or transfer a legacy responder bundle), update both client configurations, and
  restart the clients.
  Preserve the old journals rather than deleting them to bypass root binding.
- The private certificate lasts 30 days. Expiry requires a fresh pair. A changed LAN
  address requires updating both client URLs and the server bind address for spoken-code
  pairs; legacy bundles require a new certificate/pair matching the new host.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| MCP cannot start | Use an absolute executable path; run `uv sync --frozen` locally; verify the selected config exists. |
| B cannot connect | A's server is running; config URL uses A's reachable IP; port 9443 is allowed; Wi-Fi guest isolation is off. |
| TLS verification fails | Use the certificate from this exact pair; check host/IP, clock, and expiry. Never disable TLS verification. |
| Private-file permission error | Restore directory mode 700 and config/token/key file mode 600; avoid symlinked config paths. |
| Responder unavailable | B explicitly activates response mode; keep its chat/MCP process running. |
| Already-connected responder | Disconnect the existing B chat, or wait for its lost lease to expire before reconnecting. |
| Wrong tools appear | A must load requester config; B must load responder config. Restart after correcting it. |
| Skill missing | Open the configured chat directory; verify its role's SKILL.md exists; restart the client. |
| Local status stale | Re-read the file and request a new proposal. Existing approved contents are never silently rebased. |

Exec sessions can create files and run commands/tests on A. Native mode persistence,
elicitation UI and actual token savings still need acceptance in your clients.
See [verification](verification.md) and [scope/limits](../README.md#scope-and-limits).

Client references: [Codex repository skills](https://developers.openai.com/blog/skills-agents-sdk),
[OpenAI skill concepts](https://developers.openai.com/plugins/concepts/skills), and
[Claude Code skill locations](https://code.claude.com/docs/en/skills).
MCP command syntax was also checked against the locally installed CLI help.

For an existing pair, follow [execution upgrade steps](remote-execution.md#enabling-it)
to deliberately enable exec, or retain the [proposal-only upgrade](local-approval.md#upgrading-an-existing-pair).
Do not rerun init or replace private state.
