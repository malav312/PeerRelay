# PeerRelay: requester and response modes

The user selected a standalone Codex/Claude MCP integration, explicitly asked to
start building, and deferred review-coverage optimization. No upstream runtime or
the unrelated application changes are part of this work.

## Intent and chosen approach
A is nearly out of usage; B explicitly offers their own authenticated agent.
A enters requester mode once per chat, then forwards tasks and follow-ups until
exit. B enters response mode and handles substantive reasoning using scoped
remote filesystem tools. Either party can disconnect. There is no local-model
fallback and no credential sharing.

Approaches considered: a standalone UI would eliminate requester model calls but
the user rejected leaving Codex/Claude. A pure skill cannot enforce transport,
authorization, or durability. The chosen approach is an MCP core with small
client-specific skill packages. Mode instructions guide the client; the MCP
service enforces role permissions and connection state. We do not promise that
client instructions hard-disable host tools or hidden local reasoning.

## First deliverable
A runs an HTTPS relay and filesystem service for one registered project. Two
separate private bearer credentials authorize requester and responder roles.
B connects over HTTPS with a pinned CA; local MCP processes speak stdio to each
client. No provider tokens are read. B's existing agent receives a task via MCP,
reads/lists/searches the remote project, proposes creates/replacements, and
returns a concise summary plus an optional full answer. The transport is ordinary
code and performs no model calls. Native client token usage remains unknown
unless measured externally; counters distinguish MCP calls from model tokens.

Modes are scoped to opaque chat/session IDs. One active responder per pair, one
claimed task at a time, bounded prompts/files/results. B explicitly activates a
lease; a deterministic heartbeat maintains it. Disconnect invalidates the lease,
cancels queued work and marks active work interrupted. Stale tools/completion are
refused. Completed results and proposals remain inspectable; applying proposals
requires the same live responder lease, so disconnect revokes unapplied changes.
Reconnect creates a new lease and never resumes interrupted work automatically.

A's tools expose only compact status by default; full results and proposal diffs
are explicitly fetched. B gets the latest task plus bounded recent context for
that requester chat. All file tools stay scoped to the claimed task/root.

## Writes
B can only propose; A explicitly approves an identified proposal and its digest.
Each proposal binds task, lease, relative path, base version, new content and hash.
Creates require absence. Replacements require unchanged content and identity.
Apply uses a private journal, a process lock, safe descriptor-relative traversal,
symlink/hardlink refusal, a synced temporary file, and atomic installation.
Repeated apply returns the stored receipt; interrupted apply becomes unknown and
is never automatically retried. Conflicts preserve the current file. Each file
is independently approved; no multi-file transaction, shell, deletion, rename,
binary edit, or test execution in v1. External editors do not participate in the
relay lock: avoid editing during apply; optimistic checks cannot provide a
filesystem-wide compare-and-swap against uncooperative writers.

## Packaging and verification
Standalone Python package, SQLite durability, FastAPI/HTTPS, official MCP Python
SDK, two role-specific MCP tool sets, Codex and Claude skill packages. Reuse the
MIT-licensed upstream filesystem traversal/journal foundation with attribution,
not a runtime dependency. Build in a disposable staging repository and deliver
at /path/to/PeerRelay unless the user chooses otherwise.
Test write approval, replay, conflict, restart, role isolation, path escapes,
revoked/expired leases, per-chat isolation, compact outputs, real TLS, and actual
stdio MCP calls using the SDK client. A real two-client model session remains a
separate acceptance test; protocol success is not evidence of client instruction
compliance or exact token savings.
