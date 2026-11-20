# PeerRelay

A standalone local MCP integration for two people using Codex or Claude Code.
A keeps a minimal requester conversation; B's authorized active agent does the
substantive reasoning. No provider credentials are copied or read. Upstream is not
a dependency, and running upstream services are unaffected.

This first version supports scoped text reads, directory listing, literal search,
proposed file creation/replacement, and A-approved application. The write path has
been exercised through two real stdio MCP processes and HTTPS. Native Codex/Claude
mode persistence and actual requester token savings still need live acceptance.

For step-by-step installation, follow the [two-laptop setup guide](docs/setup.md).

## What each side does

- A enables requester mode once per chat. New tasks and follow-ups are forwarded
  unchanged. A receives a short receipt, asks for status when wanted, and reviews
  proposed changes. Full reports are fetched only on request.
- B enables response mode, waits for work, uses remote filesystem tools, proposes
  changes and returns a brief summary. B can disconnect at any time.
- Transport, polling inside a wait, and heartbeat are ordinary code with no model
  calls. The surrounding client still uses its model to select tools and reply.
  MCP does not hard-disable native host tools or enforce a local-token ceiling.

The native agent on B is the worker. Both clients can use the same protocol; this
is not a separate unattended Claude/Codex CLI worker. Keep B's response chat active.
A idle/status interaction and B idle waits may still cost native model tokens.

## Install on each laptop

Python 3.13+ and `uv` are required. From this repository:

```sh
uv sync --frozen
```

Use the absolute path to `.venv/bin/peer-relay` in MCP configuration. Do not ship
one laptop's virtual environment to the other laptop.

## Pair a project on A

Start with a disposable project to test writes. The pairing directory must be
outside the registered project. Choose A's reachable LAN IP, not the example IP.

```sh
.venv/bin/peer-relay init \
  --project /absolute/path/to/project \
  --pair-dir /absolute/path/to/private-peerrelay-pair \
  --host 192.168.1.100 --port 9443
.venv/bin/peer-relay serve \
  --config /absolute/path/to/private-peerrelay-pair/server.json
```

Keep that server running. `init` creates separate requester/responder credentials
and a private TLS certificate valid for 30 days. Transfer only the generated
`responder/` directory to B through a private channel. It contains `client.json`,
`responder.token` and `gateway.crt`; paths are relative so the directory is portable.
Keep file permissions private (`chmod 700` directory, `chmod 600` its three files
if transfer changed them). Never send B the server key or requester token.

The registered root is identity-bound. Keep private state and journals. Changing
projects requires a fresh pair; do not delete a journal to bypass root binding.

## Connect the native client

On A use `requester/client.json`. On B use the transferred `responder/client.json`.
The command below is the same on both; substitute the local executable and config.

Codex CLI:

```sh
codex mcp add peerrelay -- /absolute/path/PeerRelay/.venv/bin/peer-relay \
  mcp --config /absolute/path/to/role/client.json
```

Claude Code:

```sh
claude mcp add --transport stdio peerrelay -- \
  /absolute/path/PeerRelay/.venv/bin/peer-relay \
  mcp --config /absolute/path/to/role/client.json
```

For desktop clients, add the same command and arguments through their MCP settings.
A JSON configuration example is in `examples/mcp.json`; it contains no secrets.
Reconnect/restart the client after configuring. A exposes requester tools only;
B exposes responder tools only.

Install the skills from `plugins/codex/skills` into Codex's supported skill location
(for example `.agents/skills` in the selected project), or load the skills-only
plugin with the client's plugin installer. For Claude Code, load
`plugins/claude` as a local plugin, or install its skill folders in `.claude/skills`.
Both packages use the same mode instructions; MCP configuration is separate so
no credentials or machine-specific paths are embedded in a distributable plugin.

- Codex: invoke `$requester-mode` on A or `$response-mode` on B, or select the skill
  through `/skills`. Custom skills are not guaranteed to be bare slash commands.
- Claude standalone skills: `/requester-mode` and `/response-mode`.
- Claude plugin skills: normally `/peerrelay:requester-mode` and
  `/peerrelay:response-mode`.

The MCP server also exposes `requester-mode`/`response-mode` prompt templates if
that client's prompt browser supports them. Do not enable response mode implicitly.

## Verify writes

1. On B activate response mode. On A activate requester mode.
2. Ask A: "Read hello.txt and propose replacing its contents with 'hello from B'."
3. The file must remain unchanged while the proposal is pending.
4. Ask A to show the proposal. Review every diff page and explicitly approve the
   identified file. The requester tool takes the proposal ID, digest and an exact
   confirmation string; keep native client approval enabled for `approve_change`.
5. Verify the file changed. Repeating the same approval returns its receipt without
   writing again. A local edit before apply produces a conflict.
6. Tell B to disconnect. New tasks and remote tools must fail; no local fallback.

Only the requester role can apply. A's credential can authorize writes, so the
MCP approval string is a confirmation guard, not cryptographic proof that a human
clicked a button. Do not allowlist approval for unattended use. B's text or project
files cannot supply user approval. Unknown writes require local inspection; never
create a new operation ID to blindly retry them.

## Disconnect and recovery

Either side may stop. B's explicit disconnect revokes its lease immediately.
Lost MCP processes stop heartbeat; access expires within 60 seconds. Closing a
chat may leave a shared MCP process running, so explicitly disconnect B before
leaving response mode. Server restart revokes all prior leases. Reconnecting does
not automatically resume interrupted tasks.

Revocation stops remote filesystem access and unapplied proposals. It cannot kill
an independently running native model process; B's skill must stop reasoning when
instructed or denied. A file apply already executing may finish before disconnect
is acknowledged; completed writes are not rolled back. Completed results remain
inspectable. Keep the durable journals for replay protection.

## Scope and limits

One paired responder and one project; 32 pending tasks, 128 remote tool calls per
task, 32 proposals per task, 512 KiB NUL-free UTF-8 files, and 32,768-character full answers.
Each file is applied independently. No shell/tests, deletion, renaming, binary
editing, automatic dependency installation, or multi-file atomic transaction.
Symlinks, hardlinks, path escapes, and mount crossings are refused. The project
grant includes its regular files; it is not a secret-file classifier.

Version checks preserve detected local edits. External editors do not share the
relay's lock, so avoid editing a file during apply: ordinary filesystem rename
cannot atomically compare-and-swap against arbitrary concurrent external writers.
Private state contains task text, results and journaled file contents. This first
version has no automatic retention cleanup; protect that directory and backups.

`usage` reports relay HTTP requests (including heartbeat/polling) separately from
model tokens. Native requester/responder token totals are `null`, not invented
zeros. Use each client's own usage reporting for a comparable token benchmark.

## Development

```sh
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
```

The test suite launches localhost TLS and two SDK MCP subprocesses and writes only
disposable fixture projects. See `docs/verification.md` for evidence and remaining
acceptance work. MIT-licensed filesystem foundations were adapted from upstream;
see `NOTICE` and `LICENSE`.
