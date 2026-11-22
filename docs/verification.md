# Conversation experience verification — November 21, 2026

The first [session experience](session-experience.md) slice passes **201 tests**,
with eight optional container tests skipped and one existing Starlette/httpx
warning. Ruff and whitespace checks pass. Old JSON/warning question rendering and
verbose status failed the new assertions before implementation. Real MCP tests
verify normal readable forms, Unicode custom answers and unchanged task identity;
follow tests verify accept/decline/cancel. Compact status preserves exceptional
commands and provides full history on request.

Independent review found no important introduced regression. Fresh role behavior
simulations passed seven routing/verbosity scenarios. Native UI rendering and
provider-token savings remain unmeasured. A pre-existing minor difference remains:
the legacy wait_for_update tool reports invalid credential-like replies as an
error, while submit_and_follow returns pending context plus reply_rejected.

The separate Codex 0.171.0 feasibility probe attached a native terminal and local
controller to the same explicit thread over a private Unix socket, observed a
metadata update in the terminal and exited with zero turns. It used isolated test
configuration and no outbound IP networking. This verifies transport/thread binding,
not inference wakeup, approval ownership, automatic task execution or reconnect.

---

# Native client setup verification — November 21, 2026

The pairing branch's portable suite passes **197 tests**, with eight optional
container-runtime checks skipped and one upstream Starlette/httpx warning. Eight
new setup tests cover both roles and clients, private artifacts, existing paths,
invalid bundles, shell quoting and the native launch argument boundary. The
launcher override test failed before its correction and then passed.

The built wheel was installed into a disposable directory. All four role/client
setup combinations successfully started the real stdio MCP server and listed the
expected role tools without activating a lease. All role skills were present in
the wheel. Ruff and whitespace checks pass. Independent review found no important
issues; a native help timeout can still display a traceback while failing closed.

The installed Claude CLI advertises the launcher's required controls. No native
model session or two-laptop question/write test was run for this change. Tool
enforcement, skill invocation and native elicitation remain pending; Codex local
tool restriction remains explicitly unverified. Follow the separate checkpoints
in [native client sessions](client-sessions.md). This is not a new container or
provider-token benchmark.

---

# Clean main verification — November 21, 2026

The cleaned main candidate preserves the follow-through and Podman-tested execution
implementation. Changes since that acceptance are documentation/naming cleanup and
private temporary-file naming. Prefix checks continue to protect legacy operation
files, including empty and newline-containing suffixes. Independent review found
that edge case; the new test failed before its correction and then passed.

The portable suite passes **167 tests**, with seven explicitly opt-in container
tests skipped and one upstream Starlette/httpx warning. Ruff lint/format and Git
whitespace checks pass. The previous seven real Podman acceptance tests are recorded
below; they were not rerun as part of this naming cleanup.

The history scan examines every commit reachable from the cleaned branch candidates,
including PR #1's branch. Following maintainer approval, the updated pairing branch
also uses cleaned ancestry and the two superseded branches were removed. The scan
permits required attribution files and the pairing code's ordinary English word list,
and rejects unrelated project references and personal paths elsewhere.
See [clone migration](history-cleanup.md).

---

# Podman runtime acceptance — November 21, 2026

**173 tests passed in 39.51 seconds**, including all seven real-runtime checks.
Ruff lint/format and whitespace checks pass; one upstream Starlette/httpx warning
remains. Independent review found no important issues in the HOME fix or tests.

The previously unavailable runtime checks were exercised through the actual backend:
Docker CLI 29.10.0, `/var/run/docker.sock`, Podman 6.0.2, rootless libkrun VM,
Apple silicon host and cached `python:3.13-slim`. No backend mocks or CLI aliases.
The standalone Docker CLI was downloaded to a temporary test directory from
[Docker's official distribution](https://docs.docker.com/engine/install/binaries/).
No live pair or provider credentials were mounted or used.

The first test failed before workload launch: Podman's Docker API rejected
`uid=501` on the HOME tmpfs mount. The backend now creates a mode-700 HOME as the
command UID inside a fresh sticky tmpfs before executing peer text as a separate
shell argument. No other execution restriction was relaxed.

The opt-in suite checks project writes and stable-ID replay, a synthetic outside
secret and escaping symlink, non-root UID, private HOME permissions, read-only root,
no external network or container socket, dropped capabilities and no-new-privileges;
pipe/PTY input; timeout; disconnect removal of a detached child; and cleanup of a
real orphaned container after relay-process death, without replaying its write.

Reproduce with a running local Docker-compatible socket, the cached image and a real
Docker CLI on PATH:

```sh
PEERRELAY_CONTAINER_TEST=1 PYTHONPATH=src .venv/bin/python -m pytest -q
```

Without explicit opt-in the runtime tests are skipped. Tests create disposable
projects and journals, and clean up only their own named containers. Actual Docker
Engine, other Podman versions, native client delivery and token savings are still
unverified. This is runtime acceptance, not a general container escape audit.

---

# Follow-through and security verification — November 21, 2026 (before runtime acceptance)

The full suite passed: **166 tests in 31.19 seconds**. Ruff lint, formatting
and Git whitespace checks pass. One upstream Starlette/httpx deprecation warning
remains. Verification ran in an isolated checkout; MCP SDK subprocess tests
explicitly load that checkout's source rather than the installed editable package.

Coverage includes mode/context reuse, idempotent submit-and-follow, sequential
elicitation, deadlines and cancellation; authenticated body limits, filtered file
access, credential-shaped answer refusal and interactive approval; execution
backend binding, stopped-container preparation, launch-time authority revalidation,
cleanup uncertainty and quarantine. Raw MCP stdio tests verify Claude channel
capability advertisement, bounded notifications containing IDs rather than peer
text, acknowledgement and duplicate suppression.

Independent whole-change review identified backend binding, container admission,
cleanup quarantine and stale runtime guidance issues. A second pass identified
lease/workspace changes during container preparation. These were corrected, and
the final reviewer verified the launch revalidation fix with all 13 backend tests
passing and no remaining important findings within the review scope.

**Limits:** Docker is unavailable on the test host, so container argument/lifecycle
contracts and failure injection are verified, not execution against a real Docker
daemon. Native Claude channel wake-up and Codex/Claude form rendering still need
two-client acceptance. Updated skill guidance has not been newly pressure-tested
or deployed to live clients. Requester token savings have not been measured.

The prior responder session was disconnected. No live pairing configuration was
changed and no automatic reconnection occurred. TTY confirmation is friction, not
proof of human presence; unrestricted host execution still exposes OS-user secrets.
Automatic retention and in-place token rotation remain deferred, with operational
tradeoffs documented in [security](security.md). Git history cleanup remains a
separate TODO.

---

# Remote execution verification — November 20, 2026

Current suite: **128 passed** in both the isolated execution checkout and
`/path/to/PeerRelay` after fast-forward integration. Ruff lint,
formatting and Git whitespace checks pass; 10 setup shell examples parse and local
links resolve. One upstream Starlette/httpx test-client deprecation warning remains.
Independent whole-branch review completed at e71a367: three Important findings
and one Minor finding. The startup resource leak was regraded Important because
repairing configuration could not restart the relay in the same process. All four
were fixed in one pass; no second independent review is implied.

Regressions were observed failing before fixes for lease expiry during workspace
validation, failed thread startup, PTY EOF delivery (empty/nonempty input), and
constructor cleanup. A sixth test confirms input already rechecked expiry after
workspace validation; it passed before the fixes and is coverage, not a RED claim.
Failed worker startup now records a terminal refusal with a stable command ID;
failed EOF retains known bytes written and reports uncertainty without replay;
failed initialization releases acquired resources and its owner lock.

The additional tests use actual local processes for automatic file creation,
replacement, rename/deletion, shell exit codes, bounded logs, PTY/pipe input, EOF,
concurrency, timeouts, cancellation, root/branch binding, task ownership, stable-ID
replay and crash uncertainty. Existing 89 proposal/local-approval tests remain green.

Question regressions cover acceptance/decline/cancel, ownership, duplicate/different
replies, revocation/restart and pending execution denial. Four end-to-end tests run
real HTTPS and two SDK MCP processes: remote commands change a disposable Alice
project without manual approval, then native-protocol elicitation routes an accept,
decline or cancel; a client without elicitation uses the chat-response tool fallback.
These establish protocol behavior, not actual Codex/Claude product UI support.

Five fresh-context probes per old/new skill variant are recorded in
[skill evaluation](skill-evaluation.md). Native acceptance, model usage savings,
idle-chat push and host-shell containment are not claimed. No live paired config,
service, provider account or upstream code was changed.

---

# Local-human approval verification — November 19, 2026 (historical)

Current adaptation suite: **89 passed** in both the isolated checkout and
`/path/to/PeerRelay` after fast-forward integration.
Local approval CLI help also passed in the permanent checkout.
Ruff lint/format and Git whitespace checks pass. One upstream Starlette/httpx
test-client deprecation warning remains. Independent review found two Important
issues and no Critical or Minor issues. Both were fixed in one pass, with each
regression observed failing before the fix: lease expiry while waiting for the
decision lock now prevents application; new relay records no longer duplicate
captured proposal content that would survive journal purge. Legacy rows are preserved.
No second review is implied.

This includes 32 ported approval/integration tests from upstream 6234675, covering
exact bytes, permission bits, tampering, stale targets, additive journals,
connection closure, purge tombstones, two-process serialization, and process death
before/after replacement. The port's first run had 23 missing/old-behavior failures
and one pass; the approval foundation then passed all 32 tests.

Standalone adapter tests prove remote role/API denial of review/apply/approve/reject,
new ro defaults and explicit rw grants, replacement-only proposals, local offline
CLI inspection/rejection, receipt separation, old proposal preservation, missing
roots, live/expired/revoked leases, cancellation/mode exit/restart, and ordering of
an already-started local apply before concurrent disconnect acknowledgement.
Core adapter RED: 12 failures/7 passes before implementation; the MCP exposure
regression also failed before removing the tools.

The end-to-end TLS test runs two actual MCP subprocesses through the SDK, captures
a remote replacement proposal, completes the model task, inspects/applies via a
separate local CLI subprocess, checks exact output and duplicate protection, and
confirms remote status still says proposed. These are synthetic tasks and local
fixtures, not live models or user projects. No model/gateway call is made by local
inspection/decisions. Requester submission/status still use native model turns.

No live rollout, private pair access, service restart, or upstream code change was
performed. Existing native client configurations/skill installations are untouched.
Two-laptop native acceptance and actual token savings remain unmeasured. Earlier
wording simulations below predate the changed human-only approval instructions.

---

# Initial delivery evidence (historical)

The transport and approved write path run without any provider calls. The current
suite includes real TLS certificate validation and two independent MCP stdio
subprocesses driven by the official SDK client. It confirms unchanged files before
approval, exact digest approval, a successful replacement, idempotent replay, and
B disconnect refusing further tasks. Filesystem tests also cover create, conflict,
symlink/hardlink/traversal refusal, restart, and uncertainty after an applied effect.

Fresh-agent wording probes are recorded separately in skill-evaluation.md. They
are simulations, not actual Codex/Claude product runs and not a token benchmark.

Pending acceptance: install the role config and mode skills on two native clients;
confirm persistent routing across turns, owner interruption, approval UX and actual
requester/worker token totals. Both Codex and Claude use the standard MCP package,
but no claim of full native-client acceptance is made before these tests.

Final staging suite: **33 passed**. Ruff lint, formatting and whitespace checks pass.
The extra regressions cover configured credential/configuration overlap with the
project, NUL-containing reads/proposals, complete large-diff pagination, explicit
missing-newline markers, concurrent MCP wait/disconnect, and cancelled waits.
Every review regression was observed failing before its correction.

Fresh whole-branch review found four Important issues and no Critical or Minor
issues. All four were corrected in one fix pass. See progress.md for findings,
decisions and limits. No second review or native-model acceptance is implied.

Claude's installed plugin validator passed with optional missing-author/icon
warnings. The suite emits one upstream Starlette/httpx deprecation warning.
These do not affect the verified protocol and fixture-write results.

Delivery verified at `/path/to/PeerRelay` with a fresh
`uv sync --frozen` environment (Python 3.15.2): **33 passed**, Ruff lint/format
clean, CLI help successful, Git whitespace/status clean. Staging also passed on
Python 3.13.2. Upstream remained unchanged. Native client configurations have not
been installed or enabled automatically.
