# Local-human adaptation execution record

Base: 1d6df0e. Plan: docs/superpowers/plans/2026-11-19-local-human-approval.md.
Ruling: user explicitly approved implementing the tested upstream approach; execute inline
without another design gate. Cost if wrong: compatibility choices may need revision.
Ruling: preserve legacy missing-grant rw configs; new pairs default ro with explicit rw.
No paired configuration is rewritten. Cost if wrong: an old rw grant remains wider than desired.
Ruling: old PeerRelay proposals lack complete binding evidence, so preserve but do not import
or apply them through the new CLI. Cost if wrong: users must request a fresh proposal.
Ruling: retain PeerRelay's live-lease revocation even for local approval, using relay database
transaction before filesystem guard. Cost if wrong: offline application requires B to reconnect
and create a fresh proposal after expiry/restart; inspection/rejection stay offline.
Task 1: ported approval RED 23 failures/1 pass; attributed core GREEN 32 passed.
Task 2: local adapter RED 12 failures/7 passes; remote review route and MCP exposure separately
failed before removal. Added explicit grants and local CLI without altering paired state.
Task 3: actual TLS/MCP propose to local CLI apply passes; skills/docs updated; old approval
simulation evidence marked historical. Full suite 87 passed; lint/format/diff clean.
Final independent review: two Important findings, no Critical or Minor findings.
1. Lease could expire between preliminary inspection and decision guard acquisition.
   Fixed by an optional authorization callback inside the guarded core decision,
   before durable approval intent, while preserving relay transaction -> filesystem
   guard order. Terminal replay/rejection remain available without a live lease.
2. Relay proposals.tool duplicated exact replacement content past journal purge.
   New rows store {}; canonical journal owns captured bytes. Preserve legacy rows.
Both new regressions failed before correction and then passed. Full suite: 89 passed.
No deferred review findings or second review. Reviewer declined native-agent compliance,
token savings, physical secure erasure and live two-client acceptance; these remain
outside fixture verification. No deployment or live acceptance claimed.

Delivery verification: permanent checkout fast-forwarded through 45440cd; fresh full
suite 89 passed, Ruff lint/format, whitespace and local approval CLI help passed.
Private GitHub visibility verified before publication. No paired data/config was changed.
