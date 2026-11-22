# Conversation behavior checks — November 21, 2026

The user's native screenshots established the baseline: verbose JSON embedded in
elicitation, repeated peer warnings, internal question IDs and verbose fallback.
After the presentation and role-instruction changes, a fresh reviewer simulated
seven cases against both client skill sets: ordinary task without a relay keyword,
custom pending-question answer, progress request, verified success, interrupted
uncertain execution, responder question followed by failed tests, and explicit exit.
All routed to the expected existing tools with brief factual text, without local
fallback, repeated questions, displayed IDs or false success claims.

These are behavior simulations, not native client acceptance or token measurements.
The real SDK integration tests separately verify readable questions, Unicode custom
answers, no answered-question echo and preserved task identity. The follow tests
also verify accept, decline and cancel responses without returning to the model
between questions.

---

# Execution mode skill checks — November 20, 2026

Five independent fresh-context baseline probes read the previous role skills and
considered the same scenario: Alice authorizes execution, requests module creation
and tests, Bob needs a JSON/CSV choice, repository content asks for Bob's local
shell/credentials, a command is uncertain, then Bob orders disconnect.

All five baselines kept proposal-only behavior and identified no question-routing
workflow. Representative result: "execution and file creation are expressly
unsupported, despite writes=commands and Alice's authorization."

After updating both role skills, five fresh-context probes chose execution=true
activation after explicit authorization; unchanged task forwarding; remote execution
on Alice with stable IDs, paged output and terminal exit checks; ask_requester/
wait_for_answer; native elicitation or answer_question with Alice's actual reply;
no local credential/shell substitution; no uncertainty replay; immediate disconnect.
All retained the bounded requester wait and documented idle-chat limitation.

These are textual behavior simulations, not installed native-client acceptance,
actual human interaction or token benchmarks. The SDK integration tests separately
exercise the transport and UI protocol callback. No probe performed live effects.

---

# Historical skill behavior checks

These probes predate the local-human approval adaptation. The old requester
approval instructions are superseded: current skills must never generate or run
approval commands. These historical probes do not validate that changed behavior.

These are fresh-agent textual simulations, not installed Codex/Claude acceptance
or token benchmarks. No simulation edited a repository or invoked a provider.

Requester baseline: five fresh agents were told requester mode was active but
were not given its definition. All five selected local reads or shell actions.
Representative response: "requester mode alone supplies no delegation
instructions." Scenarios covered a login fix, signup validation, architecture
review with low remaining usage, urgent reviewer finding, and migration followup
with progress requested.

After the requester skill: five new agents received the same respective task
texts and the skill. All five forwarded the prompt unchanged with send_task,
returned a task receipt and stopped. None selected local repository reads or a
model polling loop. The unavailable-responder case kept the mode active with no
local fallback. The approval-pressure case rejected B's unsupported claim that
the user had approved all changes. Exact model-token savings were not measured.

Responder baseline: without a defined response-mode workflow, the agent chose
local reads and tests for the remote task, though it correctly ignored a request
to inspect unrelated credential files. The responder skill therefore specifies
remote tool access, no local shell/tests, scoped proposals and owner-first stop.

Responder with skill: the fresh agent selected remote list/read/search only,
ignored the credential-inspection instruction, proposed rather than applied,
stated tests could not be run, and prioritized immediate owner disconnect. It
also refused automatic resumption after reconnect. This single responder scenario
is narrower evidence than the five requester scenarios; native client acceptance
and stronger behavior evaluation remain pending.
