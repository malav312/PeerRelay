# Task changes review

Completed execution tasks can return a short receipt with changed-file and line
counts plus a local browser link. Expanding a file displays its unified diff. The
browser calls the requester-side viewer, which fetches the stored artifact through
the authenticated relay transport. Diff computation and rendering use code; the
ordinary MCP status/follow response contains counts, an opaque task handle and the
link, not paths or patch text.

`view_changes(mode_id, task_id)` returns or renews that link. It does not retrieve
the patch for the model. `get_changes_for_analysis` explicitly returns the full
bounded artifact to model context and is reserved for a user request to analyze
changes. Showing the review page alone should not call that tool or fetch the page
through a model browsing tool. This separation does not prevent a model from
misusing a tool against its instructions; it is not a new consent mechanism.

The local page is read-only: no apply, undo, shell, or write endpoint. Execution and
proposal permissions are unchanged. Proposal-only tasks continue using the existing
local proposal review/approval workflow and do not receive this execution artifact.

## What the counts mean

The first successful claim takes a baseline of the registered project's permitted
regular files. Claim retries reuse it. Successful completion, after all commands
have ended, captures the second snapshot and freezes the result. Edits already
present when the task starts are the baseline, including uncommitted and untracked
files. Created and deleted files count, renames appear as deletion plus creation,
and text-file permission changes are indicated. Later filesystem edits cannot
change the frozen patch. Non-UTF-8 filename bytes display as literal escapes in the
HTML page; the stored path identity remains unchanged.

The scope is **changes during the task**, not proof that Bob authored every edit.
Concurrent changes by the user or another program can appear. Traversal is bounded,
not a filesystem-wide atomic snapshot. Cancelled, interrupted and failed tasks
conservatively have no completed review; they must not capture unrelated later
edits. Older tasks without a baseline also report unavailable.

Line totals cover displayed UTF-8 text. Binary files and files exceeding preview
limits have metadata-only rows, with omitted-preview counts shown separately.
Large files are compared by filesystem metadata; touching them can therefore
appear as a change without proving their bytes changed. Empty files and mode-only
changes may have zero added/deleted lines.

## Boundaries and retention

- Reads reuse the registered-root descriptor and path policy. Symlinks, hardlinks,
  cross-device entries, reserved operation files, credential paths and
  `.peerrelayignore` exclusions are not followed or shown.
- Baseline and completion exclusion rules both apply. Newly excluding a path also
  hides its stored preview on future reads. An unsafe policy or replaced root makes
  the review unavailable. `.gitignore` is not the relay's access policy.
- Reviews additionally omit `node_modules`, `.venv`, `venv`, `__pycache__`,
  `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.next` and `.turbo` at any depth.
- Each scan has a five-second traversal deadline, 10,000 visited entries, 2,000
  regular files, depth 32 and path length 4,096. Incomplete scans fail closed rather
  than reporting unvisited files as deletions.
- Text previews are limited to 128 KiB per file and 1 MiB retained text per snapshot.
  Encoded artifacts above 3 MiB are unavailable, staying below the transport bound.
- Baselines/artifacts reside in the existing private relay database. Baselines are
  discarded at completion or revocation; at most 64 completed artifacts remain
  readable for up to 24 hours. Cleanup occurs on relay calls/startup. Exiting the
  owning mode revokes reads. Logical removal is not guaranteed secure disk erasure.
- Each requester MCP process lazily binds a random loopback port. A random review
  URL grants read access to one mode/task for one hour; every page request rechecks
  relay ownership and active-mode access. URLs are not logged. No CORS access,
  foreign Origin/Host, framing, scripts, remote assets or browser cache is allowed.
  A local program or person holding the URL can view it while valid.
- The server closes with the stdio MCP session. Restarted sessions can create new
  links to unexpired completed artifacts. Expired links return unavailable.

## Validation and acceptance

Automated tests exercise baseline preservation, claim retry, created/deleted files,
no status/follow patch payload, explicit analysis, frozen snapshots, binary and size
limits, dependency exclusions, incomplete scans, root and policy guards, ownership,
restart, cancellation, retention, browser escaping/headers, invalid Host/Origin,
link expiration, shutdown and rejected writes. The browser page has also been
visually checked with a synthetic modified/created/binary example, including an
expanded file.

Reviewer consumer simulations passed the review-link, explicit-analysis and issue
reporting cases. Native Codex/Claude acceptance and model token measurements are
still pending. This implementation does not inject the native Edited files card, use MCP
Apps, or change the MCP protocol version. It does not automatically open a browser
or guarantee a host renders loopback links. If MCP runs on a different machine from
the user's browser, the loopback link is not directly reachable there. A client
that blocks local links needs a supported host adapter; inline UI requires separate
capability negotiation and testing. Neither native UI support nor total zero model
tokens is claimed.

For native acceptance, use a fresh disposable pair and project with preexisting
edits. Complete an execution task with a modification, creation and deletion.
Confirm the compact receipt and link, expand the page, verify unrelated baseline
edits do not appear, then ask for analysis explicitly. Check the link after mode
exit and session shutdown. Also test an unavailable review and omitted previews.
Do not use a live pair or install into an active client without separate approval.
