# Remote execution progress
Base 74ce5f2. Plan docs/superpowers/plans/2026-11-20-remote-execution.md.
Ruling: user explicitly said go ahead after the remote executor and elicitation
proposal; implement inline without another approval gate. Use isolated local clone
under /private/tmp because permanent checkout lies outside writable roots.
Ruling: exec is trusted-host access as Alice's OS user, not a fake cwd sandbox.
Existing ro/rw grants never upgrade implicitly. Cost: broader capabilities require
explicit new exec setup/session activation, and a trusted responder can access host files.
Ruling: portable POSIX process groups provide best-effort stop, not OS containment;
escaped daemons and external side effects cannot be undone. Document this limit.
Pre-flight: Task 2 consumes Task 1 role/task admission and command IDs; question
pending gate will be added before spawn. MCP tools use the same argument names.
Task 1: initial executor tests exposed missing exec grant/API (3 failures, 11 setup
errors); 2 denial checks already passed. Real commands and full suite now 105 passed.
Found macOS returns EPERM for a second signal to an exited process group; reproduced
outside the test harness and fixed by checking leader exit before treating EPERM
as termination failure. Live permission failures remain uncertain. Ruff clean.
Task 2: question suite RED 9 failures; MCP integration RED 4 failures. GREEN:
real HTTPS + two MCP processes apply changes and route accept/decline/cancel and
unsupported-client chat fallback. Additional EOF/concurrency/task isolation/crash
coverage passes. Transient macOS signal error required retries with bounded
uncertainty, observed failing deterministic regression then corrected. Full122pass.
Task 3: 5 old-skill fresh-context probes consistently could not execute or route
questions; updated both role skills and mirrored MCP instructions. Guided probes
and final review completed below. Documentation now distinguishes exec from old modes,
trusted-host scope, same worktree, asynchronous process stop and idle-chat limits.

Guided skill probes: 5/5 chose the intended execution/question/stop flow; native
product acceptance is not implied. Pre-review full suite122pass and lint/format
clean; 10 documentation shell blocks parse, local links resolve.

Final review: independent whole-branch review of 74ce5f2..e71a367 found three
Important issues and one Minor. Rulings: accept lease expiry after slow workspace
validation, stranded worker on Thread.start failure, and false sent receipt after
PTY EOF failure as Important. Regrade constructor resource cleanup to Important:
a rejected configuration held the exclusive owner lock and prevented restarting
its repaired relay in the same process. All four fixed in one regression-first
pass. Five parameterized cases observed RED before the changes; sixth input-expiry
coverage already passed because command_owned checked the live lease again.
GREEN: all six review cases pass, full suite128pass; Ruff lint/format and whitespace
checks pass. No second review requested.

Declined-to-judge rulings: actual Codex/Claude UI support, model token savings and
instruction adherence require native two-laptop acceptance, not synthetic protocol
tests or skill probes. Linux PTY behavior is unverified on this macOS host. Accept
those limits and document them; do not claim native acceptance or Linux validation.
Host containment and guaranteed escaped-process termination are explicitly outside
the trusted-host contract. Live pairing/provider rollout remains a separate user
operation. Existing configs, skills installed in native clients and services untouched.
Integration ruling: existing user instructions authorize commits and private push;
fast-forward the known codex/requester-response base without a redundant menu.
