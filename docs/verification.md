# Remote execution verification — November 20, 2026

Current suite: **128 passed** in the isolated execution checkout. Ruff lint,
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
