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
