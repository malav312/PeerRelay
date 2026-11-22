> Security update: CLI decisions now require an interactive terminal and typed
> action/digest confirmation. A PTY is automatable; this is **not** human identity
> enforcement. Restrict local agent tools or use a separately protected approval
> environment. `.git` and credential paths are denied; inspection flags common
> executable targets. See [security](security.md).

# Human-local inspection and decisions

This guide applies to proposal-only ro/rw sessions. For automatic commands and edits,
use an explicitly authorized [exec session](remote-execution.md).

PeerRelay reuses the approval foundation tested in upstream commit `6234675`.
It runs independently of upstream. The requester model forwards a prompt and returns
compact task/proposal IDs; it does not receive diffs or execute decisions.

## Inspect and decide

Run these yourself on A, using the existing server configuration:

```sh
peer-relay proposal --config /absolute/path/to/pair/server.json inspect PROPOSAL_ID
peer-relay proposal --config /absolute/path/to/pair/server.json approve PROPOSAL_ID --digest INSPECTED_DIGEST
peer-relay proposal --config /absolute/path/to/pair/server.json reject PROPOSAL_ID --digest INSPECTED_DIGEST
peer-relay proposal --config /absolute/path/to/pair/server.json status PROPOSAL_ID
```

Use the installed executable's absolute path if it is not on PATH. Put `--json`
before inspect/status/approve/reject for escaped machine-readable output.
Review the target, expected version, exact escaped UTF-8 bytes/diff, full root
path/device/inode, original grants and baseline hash. Literal backslashes,
control characters and missing final newlines are shown explicitly. Nothing
normalizes or rewrites proposed content. Supply the exact inspected digest.

A prompt, proposal, model output or returned digest is not human consent. Do not
ask an agent to run these commands or wire them into a model-callable tool.
The CLI itself makes no model or gateway calls. This removes model consumption
for inspection/application; submission/status in a native chat can still use tokens.
Native agents with shell access remain outside this protocol's sandbox boundary.

## Authority and state

New pairings default to `ro`; `init --grant rw` explicitly permits replacement
proposals. Model access is read/list/search/propose-replacement only. Creation is
not exposed. Installing this change never rewrites a pair's grants.

Local apply requires the original task's active requester mode, running/succeeded
task and live B lease. Disconnect, cancel, mode exit, expiry and relay restart
block pending application. A decision already executing may finish before a
concurrent disconnect is acknowledged. A shared relay transaction establishes
that ordering; the filesystem guard serializes capture/application/purge.
Live authority is checked after the decision acquires that guard, before recording
approval intent, so expiry during a lock wait also blocks pending application.

Inspection/rejection are available without a network connection and when the root
is unavailable. Application needs valid local authority records and root identity.
Do not start a second relay to inspect: the CLI opens local journals directly and
does not acquire the relay lifetime lock or reset leases.

| Local state | Meaning |
| --- | --- |
| pending | No human decision yet. Inspect before deciding. |
| applied | Replacement, sync and durable success record completed. Repeated decisions do not write again. |
| rejected | Human rejection, protection failure or purge. |
| stale | Expected version no longer matches. Request a fresh proposal. |
| uncertain | Interrupted intent or unconfirmed effect. Never auto-replay. |

CLI exit code 2 signals stale/uncertain; code 1 signals an invalid/refused request;
code 0 means a normal result (inspect its state, including rejected).
Remote receipts remain `proposed`. Local state is not mirrored through task_status,
MCP or an additional reporting endpoint. A completed model task does not imply
that its proposed edits were applied.

Preserve uncertain records, the target and named staging artifacts for human
reconciliation. Matching bytes do not establish success. There is no force/retry,
automatic rollback or SQL-edit recovery procedure. Retention helpers scrub captured
content under the shared lock and keep terminal/hash tombstones. New relay records
keep proposal hashes and identifiers without duplicating replacement content;
legacy relay rows remain preserved. No automatic retention scheduler is added by
this change. Logical scrubbing does not guarantee physical secure erasure.

## Upgrading an existing pair

This repository update does not roll out to running installations automatically.
When ready to upgrade your own installation:

1. Have B disconnect, then stop the relay and finish/stop local decision processes.
   Preserve the same registered project, pairing directory, credentials and journals.
2. Update only the standalone PeerRelay code and dependencies (`git pull --ff-only`
   and `uv sync --frozen`). Do not overlay an upstream bundle, run its older
   `apply-update.py`, rerun init, or copy a new private directory over an old pair.
3. Update the installed requester/response skill copies from this checkout; inspect
   existing copies before replacing them. Restart both native clients so cached MCP
   tool lists lose the old review/approve tools.
4. Start the relay with its original server.json. Schema changes are additive;
   old operation/root rows remain. Old configs without `grant` preserve prior rw
   behavior. An explicit ro configuration stays ro.
5. B explicitly reconnects; A requests a fresh proposal and personally tests the
   local flow on a disposable file before using it on application files.

Old PeerRelay proposals were stored without the new admitted-operation/root/grant
binding. They remain recorded but cannot be imported/approved through this CLI;
request a fresh proposal rather than inventing missing evidence. Prior applied or
uncertain effects must be inspected before any deliberate new task. This is why
Upstream's legacy import-target command is not exposed in PeerRelay's adapter.

Do not run older PeerRelay code against an upgraded pair while using local approval:
it has model-callable apply and lacks the shared local-decision protections.

## Filesystem limits and evidence

The applier checks registered identity, original grants, admitted payload hash,
baseline hash, exact proposed bytes, target version, ownership, parent and staging
identity/bytes/mode. Symlinks, hardlinks, traversal, oversized/non-text contents and
reserved staging paths are refused. POSIX permission bits are preserved; inode,
timestamps, group ownership, ACLs, extended attributes, resource forks and flags
are not. Noncooperating external writers can still race the final check/rename.
Power-loss durability depends on filesystem/OS fsync guarantees.

See [verification](verification.md) for this standalone adaptation's tests. Upstream's
reported 644/30 and historical live evidence are not PeerRelay acceptance results.
