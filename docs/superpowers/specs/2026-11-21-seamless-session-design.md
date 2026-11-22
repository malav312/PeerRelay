# PeerRelay session experience

The product is a relay between two known people using their own native coding
clients. Pair once, activate a role, and work through ordinary messages. Requester
model output should be small: a question when needed and a short outcome. The
responder should wake for meaningful work without manual queue checks.

## Delivery order

1. **Conversation experience (this slice):** normal question text and readable
   choices instead of JSON/warning paragraphs; compact status tool results and
   native-chat responses; persistent routing of ordinary requester messages and
   explicit routing of answers, cancellation, progress and mode exit. Keep control
   IDs in tool state rather than narration. Short responder questions and summaries.
2. **Mode-driven pairing:** one stable bootstrap MCP registration plus both entry
   skills. Setup collects project/backend choices, performs existing pairing and
   starts the matching relay. Pairing must work before role bundles exist. Native
   dialogs belong to the host; their position cannot be chosen by the MCP server.
3. **Automatic delivery:** enable and verify Claude's native channels; implement a
   separately bound native Codex session adapter. Poll and maintain leases in code;
   wake the model only for work, an answer, a question, completion or disconnect.

All four client/role combinations remain in scope. Step 1 does not pretend to
implement steps 2 or 3. No automatic upgrade of existing live installations.

## Conversation decisions

Use plain text plus numbered choices in the existing compatible form, retaining
free-text replies. This preserves custom answers without forcing a dropdown or a
new protocol revision. An enum-only form would exclude custom answers; retaining
JSON would reproduce the reported problem. Preserve the actual question and
choices, including Unicode, without summarizing away the decision.

Use the same rendering for chat fallback. No repeated distrust banners, raw JSON,
question IDs, routine backend details or per-poll narration. Provenance remains
small tool metadata; permission enforcement stays in the relay. Important failures
and uncertain execution remain visible. No success claim before completion.

Default status tools aggregate command counts and expose exceptional commands.
Detailed status remains explicitly available. Keep task/resume/background IDs and
pending questions intact for recovery. Diagnostics remain a deliberate tool call.

Once mode is active, ordinary messages are forwarded as tasks without requiring
“use PeerRelay.” Pending-question answers resolve the existing question rather
than creating a new task; progress requests follow the existing task. Cancel and
mode-exit remain controls. This instruction-based routing is not yet deterministic
client input interception and must be stated honestly.

## Later client adapters

Bootstrap needs a UI-independent pairing coordinator, resumable setup IDs and
per-chat role bindings. Keep code exchange in a local pairing view where supported;
do not promise a particular host popup. A raw MCP registration does not portably
install bare slash skills, so package installation must include both pieces.

Claude channels need explicit native enablement and acknowledgement; a successful
notification write alone does not prove delivery. Watch lifetime must match active
response mode. Codex needs an explicitly bound native thread/session, not a guess
at the latest desktop conversation. Starting a dedicated native session is a
product choice the user has now accepted for Codex. Neither adapter may replay
uncertain remote effects or silently reactivate a disconnected responder.

## Acceptance

Reproduce the reported JSON/warning dialog failure before changing it. Test plain
question delivery through both follow APIs, Unicode and custom responses,
accept/decline/cancel, no-form fallback and reply validation. Test compact status
with many commands, errors, uncertainty and detailed retrieval. Verify persistent
routing and concise narration with a fresh skill behavior probe. Run the existing
suite. Native rendering, provider token savings and unattended delivery are separate
acceptance results, not inferred from unit tests.
