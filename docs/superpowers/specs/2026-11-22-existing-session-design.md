# Existing-session installation

The user approved implementing clone/install once, then requester-mode or response-mode
inside their usual project and signed-in client. Existing configuration and project
history must survive. No separate chat folder or launcher is required for basic use.

Approach: extend the existing bootstrap, with a project installer. Dedicated sessions
remain optional for verified native Codex automatic delivery; they are not a substitute
for existing-session support. Do not invent desktop wake APIs or silently launch another
session. A client reload may be necessary to discover new MCP configuration.

`peer-relay install --client claude|codex --project PATH` registers the stable bootstrap
and both skills. Use Claude Code's .mcp.json and .claude/skills, or Codex's
.codex/config.toml and .agents/skills. Preserve unrelated config. Refuse conflicting
PeerRelay entries and edited skills; repeat installation is safe, and owned files can
be updated. Default private setup state lives outside the project. Project settings
carry only local paths, not pairing credentials. Do not change trust/approval policy.

The pairing form offers explicit automatic relay startup after both confirmations.
Startup failure preserves the saved pairing for retry.

Bootstrap carries the installed project as a user-editable pairing default. Pairing
still confirms the actual root and permissions. Existing bundles are not retargeted.
Claude channel opt-in propagates through bootstrap, with notifications bound to exact
setup/binding IDs and watches closed before clients. Acknowledgement is required before
claiming delivery. Off/unsupported clients use bounded follow with an explicit limit.
Skills keep ordinary messages in relay mode until exit and concise receipts.

Acceptance: config preservation, conflict/no partial writes, updates, symlink rejection,
path escaping, actual stdio bootstrap context/elicitation and channel binding, lifecycle
cleanup, existing suite. Native desktop/CLI skill discovery and live custom channel
approval still require human acceptance; Claude Desktop general chat is not Claude Code.
