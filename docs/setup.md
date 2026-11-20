# Set up PeerRelay on two laptops

A owns the project and submits tasks. B offers their own active Codex or Claude
Code chat to do the work. Both people stay signed into their own accounts.
A runs the HTTPS relay; each laptop runs its own local MCP process.

Start with the disposable project below. This guide targets macOS/Linux local
clients; native Windows is not supported by the current filesystem locking code.
Both laptops need Python 3.13+, uv, Git, and an installed, signed-in native client.
They must be able to reach A's LAN address on TCP port 9443.

## 1. Install the source on both laptops

The GitHub repository is private. B needs repository access from its owner, or a
privately transferred source copy. Transfer source without virtual environments,
pairing state, or credentials.

Run on each laptop, from the folder where you want the repository:

```sh
gh repo clone malav312/PeerRelay
cd PeerRelay
uv sync --frozen
RELAY="$(pwd -P)"
"$RELAY/.venv/bin/peer-relay" --help
```

If you already have the source, enter that directory and start at `uv sync`.
Keep `RELAY` set to this laptop's absolute repository path in subsequent commands.
Each laptop builds its own virtual environment. No provider API key is required.

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
"$RELAY/.venv/bin/peer-relay" init \
  --project "$PROJECT" \
  --pair-dir "$PAIR" \
  --host 192.168.1.100 --port 9443
"$RELAY/.venv/bin/peer-relay" serve --config "$PAIR/server.json"
```

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
skills are loaded together. Keep tool approval enabled for `approve_change`;
do not grant it unattended approval.

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

## 5. Run the first task and prove writes

1. Activate B's response mode first and keep that chat active.
2. Activate A's requester mode.
3. Tell A: **Read hello.txt and propose replacing it with hello from B followed by a newline.**
4. A should return a task receipt and stop. Ask for status when wanted.
5. In A's separate terminal, `cat "$PROJECT/hello.txt"` must still print `before`.
6. Ask A to show the proposed diff. Review every page, then explicitly approve
   that specific proposal.
7. Run `cat "$PROJECT/hello.txt"` again. It should print `hello from B`.
8. Tell B: **Disconnect PeerRelay now.** A's next submission must be refused,
   without local execution.

Repeated approval of the same proposal returns its receipt without another write.
A local file edit before approval causes a conflict. Do not edit a target file
concurrently with application. For an unknown write outcome, inspect the file and
receipt; do not retry under a new operation ID.

## Stop, reconnect, or switch projects

- B disconnects explicitly before leaving response mode. Closing a chat may leave
  a shared MCP process alive. A lost process's lease expires within 60 seconds.
- A says **Exit requester mode** to resume ordinary local work in that chat.
- Stop A's relay with Ctrl-C. Restart it with the same `serve` command and state.
- Restarting the relay revokes prior leases. B must explicitly reactivate response
  mode; interrupted tasks do not resume automatically.
- For another project, create a fresh pair outside that project, transfer the new
  responder bundle, update both client configurations, and restart the clients.
  Preserve the old journals rather than deleting them to bypass root binding.
- The private certificate lasts 30 days. An expired certificate or changed LAN
  address requires a fresh pairing with the correct host in this version.

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
| Proposal conflict | Re-read the file and request a new proposal. Existing approved contents are never silently rebased. |

B cannot run shell commands or tests on A through v1. Native mode persistence,
approval UX and actual token savings still need acceptance in your clients.
See [verification](verification.md) and [scope/limits](../README.md#scope-and-limits).

Client references: [Codex repository skills](https://developers.openai.com/blog/skills-agents-sdk),
[OpenAI skill concepts](https://developers.openai.com/plugins/concepts/skills), and
[Claude Code skill locations](https://code.claude.com/docs/en/skills).
MCP command syntax was also checked against the locally installed CLI help.
