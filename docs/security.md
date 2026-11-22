# Security boundaries and tradeoffs

PeerRelay is an experimental bridge between two agents. Both directions carry
untrusted content. A skill is guidance, not a sandbox. The requester/responder
role split is enforced at the relay API only while its credentials and runtime
remain outside the other party's reach.

## Choose the execution boundary

New `--grant exec` pairings use the Docker backend. Commands run in a new container
per command, with only the project mounted at `/workspace`, no network, an unprivileged
UID, dropped capabilities, no-new-privileges, a read-only container root, temporary
HOME/tmp, a PID limit, memory/CPU limits and no Docker socket mount. The relay and
its pairing directory stay on the host. Preinstall Docker and explicitly pull a
trusted image; execution uses `--pull never`. Prefer an image pinned by digest.
Build project dependencies into that image because network installs are unavailable.
The default image is `python:3.13-slim`; it does not include every development tool.
The adapter uses the local `/var/run/docker.sock` endpoint, not remote Docker contexts.

The project mount is writable: malicious code can destroy project data or plant a
build script that runs later on the host. Keep backups and review changes before
running project tooling outside containment. The image/kernel/daemon are trusted.
Do not mount credentials, the relay installation or pairing files into the project.
Symlink targets outside the mounted project and linked Git worktrees whose `.git`
points outside it may be unavailable. Use a self-contained checkout for container
work; the exact workspace remains the one registered, with no automatic copy.

Containers are named and journaled before a stopped-container creation phase;
only confirmed creation can proceed to an attached start. Authority is rechecked
after creation. A delayed create after cancellation cannot start a workload. Normal completion and cancellation
remove the container; startup refuses to proceed if a previously admitted container
cannot be removed. A server crash can leave a container alive until recovery.
A missing/unavailable daemon never causes fallback to host execution. Cleanup
failure means `uncertain`, not successful revocation. It quarantines new execution
and task completion until a restart can verify removal. An unconfirmed creation
whose container is absent remains unresolved; stop and reconcile with the Docker
operator rather than deleting the journal or assuming absence proves no late admission. These lifecycle contracts have
automated tests plus real acceptance coverage against Podman 6.0.2's Docker API
with Docker CLI 29.10.0. Docker Engine itself and other runtime versions remain
unverified. Run the opt-in runtime suite in your environment before relying on this
experimental backend. See [verification](verification.md).

Temporary HOME is a mode-700 directory created as the non-root command UID inside
a fresh sticky tmpfs before peer code starts. This avoids Docker API implementations
that reject tmpfs ownership options. The private directory is disposable per command;
its parent is writable within the container, and no host HOME is mounted.

Host execution is a separate explicit choice:

```
--grant exec --execution-backend host --allow-host-credentials
```

**Host exec gives Bob commands with the relay user's OS authority.** Commands can
read provider logins (`~/.codex`, `~/.claude`), SSH/cloud/Git credentials, requester
tokens, TLS private keys and journals. They can edit the relay or steal credentials
to impersonate Alice, answer questions, regain access and persist after disconnect.
The API role split, question gating and revocation cannot defend against this.
A disposable HOME prevents incidental loading of normal profiles; absolute paths
and OS account lookup still reach real credentials. It is not isolation.

Use a separate restricted OS account with no provider credentials if Docker does
not fit. For a real role boundary, the executor identity must not be able to read
or modify relay state/runtime. Simply running both relay and commands under one
new account limits damage to other accounts but does not separate those two roles.
The current host backend does not provision or enforce a separate executor user.
No deployment should interpret `--allow-host-credentials` as making host exec safe.

## Restrict both native agents' local tools

Use the [native session setup and acceptance checklist](client-sessions.md).
The Claude launcher configures removal of built-in tools and limits MCP servers;
native enforcement still needs acceptance on the installed client. Codex setup
currently reports restrictions as unverified and does not generate a launcher.
Neither integration files nor a skill prove that a running chat is restricted.

Alice's agent sees Bob's summaries/questions; Bob's agent sees Alice's task/files.
Either can contain prompt injection. Tool results mark peer content as untrusted;
question forms identify Bob and state that replies grant no local authority.
These labels help the agent distinguish data but do not enforce client behavior.

Use dedicated client sessions with local shell, file writes and other side-effect
tools disabled or subject to explicit human approval. Restrict reads of unrelated
home directories as well. Use the client's actual permission controls, not only the
PeerRelay skill. Where such restrictions cannot be enforced, run the client in a
separate restricted OS environment. Do not enable unattended local tool approval
in a responder or requester session. PeerRelay cannot verify that the native client
has applied these restrictions; diagnostics reports protocol capability, not host
security posture. Native account approvals stay with their account owner.

Proposal decisions require a digest and interactive confirmation through stdin and
stderr terminals. This prevents accidental piped approval. **An agent can allocate
a PTY or call Python APIs, so this is not human authentication.** The test suite
intentionally demonstrates this. True human-only approval needs a separately
protected broker/OS identity or hardware-backed user-presence mechanism; it is not
implemented. Never treat Bob's suggested CLI instructions as approval.

## File and question safeguards

Filesystem tools refuse `.git` (including worktree metadata files), common provider
and credential folders, `.env`/`.env.*`, private-key extensions, token files and
common credential filenames. Read, listing, search, proposal capture and application
share the policy. A project-root `.peerrelayignore` adds exclusions. It is bounded
at 64 KiB, must be a normal non-symlinked file, and malformed policy fails closed.
It cannot be changed through proposal tools. Rules are case-insensitive glob patterns:
blank/comment lines ignored, basename patterns match at any depth, slash patterns
match root-relative paths and excluded parents. Trailing slash excludes a directory.
Negation (`!`) is refused; this is deliberately not the full gitignore grammar.
For example:

```
customer-data/
internal/reports/*.txt
*.sqlite
```

Defaults may hide useful config; edit locally after deliberate review instead of
removing defaults. Filtering does not scan every source string for secrets and
cannot constrain arbitrary shell commands. Secrets embedded in allowed files may
still enter Bob's provider context. Prepare a sanitized project before sharing.

Proposal inspection warns on common code-executing targets, including package/build
files, shell scripts, IDE tasks and CI workflows. This is a heuristic: any source
change can affect later execution. `.git` is refused outright.

Native questions are labeled as coming from Bob. Common provider/GitHub/npm/AWS key
formats, bearer credentials and private-key blocks are rejected in replies before
forwarding and again at the relay. The error never echoes the candidate secret.
This catches some mistakes, not arbitrary passwords, opaque tokens or encoded
secrets. Do not send credentials through chat or elicitation. Reject suspicious
questions; authenticate locally through the service's normal UI.

## Network, revocation and storage

Authentication happens before request-body reads, including `/healthz`. Authorized
bodies have a five-second deadline and 4 MiB limit. Failed authentication is counted
per source address with bounded memory; repeated failures return 429. The first
failure and rate threshold enter a private rotating `state/auth-audit.jsonl` log,
with no headers or payloads. This does not prevent TCP/TLS/connection-slot exhaustion;
keep the relay behind a trusted LAN/firewall, never public port forwarding.

There is no live token-rotation command. If either peer credential may be compromised:
stop the old relay, disconnect both clients, create a fresh pair with new tokens
and certificate in a new private directory, transfer only the new responder bundle,
and replace both client registrations. Do not restart the compromised pair. Preserve
old journals securely for investigation. A stolen host credential requires separate
rotation at its provider; re-pairing cannot evict malware or revoke provider tokens.

Host process groups cannot contain `setsid`, persistence services or privileged
children. Cancellation is best effort and never rolls back edits/external effects.
Container cleanup is stronger within that container, but project edits survive.

Journals retain task text, file contents/proposals, command text/output and answers
without an automatic retention limit. Output bounds are not redaction or retention.
Do not print secrets. Protect backups and restrict access. A safe retention command
needs replay tombstones and cross-journal cleanup; it is deferred, as is live token
rotation. These unresolved items remain part of the threat model.
