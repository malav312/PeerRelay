# Local human approval adaptation

> Execute inline with superpowers:executing-plans and test-driven-development.

Goal: move all approval authority out of MCP and HTTPS into a human-operated local CLI.
Source specification: user-approved upstream integration 6234675 and its local approval docs.
Architecture: reuse attributed filesystem/journal/Approvals foundation, retain standalone imports;
add a local relay adapter that serializes live task/lease checks with decisions.

## Constraints and rulings
- No upstream edits, live rollout, pairing/config/root/journal deletion, or secret access.
- Preserve original remote proposed receipts; local decisions are separate and never reported by RPC.
- Restrict model tools to reads/list/search/replacement proposals; no creation or model approval.
- New pairs default read-only, explicit --grant rw for replacement proposals. Old missing-grant
  configurations retain their existing rw behavior; no existing config is rewritten.
- Local apply keeps PeerRelay disconnect/mode/cancel revocation. Lock order is relay transaction,
  then filesystem execution guard; never reverse this order.
- Old PeerRelay proposals lack captured bindings: preserve them, require a fresh proposal; do not
  manufacture a baseline/grant or import old applied effects into the new decision path.
- Ruling: explicit instruction to implement the tested plan authorizes inline execution without
  another design approval. Cost if wrong: compatibility decisions may need revision.

## Review focus
- Local application versus concurrent disconnect/cancellation and expired leases.
- No old HTTP/MCP approval route or creation path remains available to models.
- Additive state upgrade and old proposal handling never replay uncertain/applied effects.
- Exact-byte inspection and root/grants/baseline bindings survive tampering and purge.
- Local CLI can inspect/reject with missing roots and does not make model/gateway calls.

## Tasks
- [x] Port approval regressions first; observe missing-service failures. Adapt canonical core and
  retain NUL policy/old staging name restrictions. Run canonical crash/tamper/purge/locking tests.
- [x] Add relay/CLI tests for local apply, no RPC approval/review, replacement-only grants,
  disconnect/exit/cancel/expiry, concurrent decisions, preserved remote receipts and legacy state.
  Remove old authority routes; capture proposed operations through the shared journal; add local CLI.
- [x] Update real TLS/MCP test to propose remotely and approve through local subprocess; update
  skills and docs to reference local inspection without model-issued approval commands.
- [x] Full suite, lint, fresh independent review; fix material findings with regression tests.
  Commit and fast-forward the standalone checkout; verify the integrated result.

Delivery: publish the completed branch to the verified private remote under the existing
user authorization. No live rollout is part of this implementation.
