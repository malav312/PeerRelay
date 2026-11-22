# Follow-through and security boundaries

The user selected capability diagnostics and one submit-and-follow call first,
then client-specific background delivery. Waiting belongs in code; meaningful
questions and completion may wake the model. Preserve explicit chat mode IDs,
workspace binding, permissions, operation IDs and prior context through follow-ups
and a responder reconnect. Never auto-upgrade, reconnect a responder, or replay
interrupted work. No shared global "latest mode" across chats.

## Approach and tradeoffs

Keep the Python relay and existing MCP tools compatible. Add bounded follow calls
(default 50 seconds, configurable up to 3600) which handle consecutive native
elicitation replies until terminal state. A timeout returns a durable resume
handle. Unsupported elicitation returns one labeled question. Client capability
advertisements are evidence of protocol support, not proof a dialog was displayed.
A diagnostics tool distinguishes those states and offers an explicit test form.

Add optional Claude channel notifications only after the portable flow works.
They contain locally generated IDs/states, never remote prompts, output or choices.
They wake only on actionable changes and require explicit local configuration and
watch registration. Delivery is at-least-once with explicit acknowledgement and
bounded retries; transport emission is not UI receipt. Codex stays on portable
follow calls until a supported idle-wake contract is established. No provider API
or alternate model processes. No native token-savings percentage claim.

## Security scope

- Treat all peer text, files and outputs as untrusted in tool envelopes and forms.
- A TTY is NOT proof of a human; do not advertise CLI approval as enforceably
  human-only. Require interactive confirmation to reduce accidental automation;
  client tool restrictions / separate OS identity remain necessary.
- Deny .git and common credential paths in filesystem tools. .peerrelayignore is
  additive, bounded, fail-closed; no negation that removes defaults. Flag code-
  executing proposal targets during inspection. This cannot sanitize arbitrary
  secrets embedded in ordinary source, and cannot constrain host exec.
- Authenticate before reading bodies, time-bound authenticated bodies, bound failed
  authentication accounting, expose generic authenticated health only, log no
  credentials/content. Document LAN flood limits and fresh-pair revocation.
- Label Bob's questions and block common credential-shaped answers before forwarding;
  conservative patterns are not a complete secret detector.
- Preserve host exec only with explicit acknowledgement of access to provider and
  relay credentials; disposable HOME reduces incidental leakage, not authority.
  Add optional Docker command isolation with project-only mount, no network,
  non-root uid, dropped caps, readonly root, resource limits and per-command removal.
  No Docker socket, pairing directory or real HOME mounts. Never fall back to host.
  Docker must be operator-installed; native Docker tests require an available daemon.
- Document both-side local tool restrictions, escaped-process limits, fresh-pair
  rotation procedure and retained journal contents. Automated retention/rotation
  and cryptographically human-only approval are separate work; report as residuals.

## Validation

Regression tests for follow-up permissions/context through reconnect, sequential
questions in one call, timeout/resume idempotency, cancellation, fallback and
capability truthfulness; traversal/ignore/symlink and risky proposal tests;
authentication-before-body, deadlines and sanitized audit tests; secret-reply
blocking; container launch/cleanup contract and native runtime where available.
Full existing suite and lint. Do not reconnect the live pair or rewrite Git history.
