# Combined setup and changes-view acceptance

This branch combines bootstrap pairing, normal questions, concise requester
receipts, native diff guidance and the local changes viewer. The standard bootstrap
chat still needs manual responder resume where the native client cannot deliver
background events. The experimental dedicated Codex launcher below tests automatic
responder delivery separately.

Use separate installations/chat directories on both laptops. Keep the previously
tested checkout and pairing intact. Explicitly disconnect/exit the old role chats
before switching clients; do not replace a running installation's environment.

## Install the integration branch

Choose a new checkout directory on each laptop. The chained commands stop if a
checkout or installation step fails:

```sh
git clone --branch feat/session-integration \
  https://github.com/malav312/PeerRelay.git "$HOME/PeerRelayIntegration" &&
cd "$HOME/PeerRelayIntegration" &&
uv sync --frozen &&
git rev-parse --short HEAD
```

Follow [mode-driven pairing](mode-pairing.md) to package the bootstrap MCP and both
role skills for fresh Claude/Codex chats. Use a fresh disposable project and setup
state for the full pairing test, rather than overwriting the existing pair. For
the established test combination, Alice uses Claude Code and Bob uses Codex.

## Test in this order

1. Activate the appropriate role skill on each laptop. Both should obtain a local
   setup URL before a role bundle exists. Pair through those pages and compare
   check numbers; keep codes in the local pages. Start Alice's relay in her view.
   Check that paired, relay-ready and role-connected remain separate states.
2. Activate Bob's response mode, then Alice's requester mode with container
   execution explicitly enabled. Confirm the registered disposable project.
3. In Alice's project, create `baseline.txt` containing `existing user edit` before
   submitting the task. This provides a change the task must not claim as its own.
4. Send this ordinary prompt without repeating the relay keyword:

   ```text
   Create a new folder named integration-demo. If it exists, stop.
   Ask me whether to name its file notes.txt or summary.txt, allowing a custom answer.
   Wait for my answer, create that file with "question test passed" and a newline,
   then run a command verifying the exact contents. Do not touch baseline.txt.
   ```

5. Resume Bob if needed. Answer `custom.txt` in Alice's native form. The question
   should be readable, without JSON or repeated warnings. The answered form should
   not be echoed or reconfirmed in chat.
6. On completion, expect a short receipt, changed-file/line counts and a review
   link. Open the link yourself. Expanding `integration-demo/custom.txt` should show
   its contents; `baseline.txt` must not appear. Viewing the page should not invoke
   a model tool or paste the patch into chat.
7. Ask `Add "follow-up passed" as the second line and verify both lines.` The same
   requester mode should route the task. Its new review should show only that task's
   additions. Ask `Show changes` to obtain a link, then ask `Explain those changes`
   to verify that analysis is sent to Bob and the requested answer is delivered.
8. If using native `/diff`, open the actual edited Git project. The setup chat
   folder is not that project. Native UI is a separate viewer, not a relay task or
   a promise that PeerRelay can open the client's panel.
9. Explicitly exit requester mode and disconnect Bob. Old review links should no
   longer expose the artifact. Close the bootstrap MCP session; its local setup
   page and review server should stop. A pending setup request must not start new
   work after shutdown.

Record native client versions and whether each check passed. Note any manual
resume separately; this test is not acceptance of automatic delivery or a measured
token-savings claim. Docker execution, real LAN discovery and actual native forms
must be verified here rather than inferred from local integration tests.

## Automatic Codex responder acceptance

Use this after the pairing and ordinary relay checks above. Alice can keep using
Claude Code. Disconnect Bob's ordinary response-mode session before starting the
dedicated launcher; do not run both responders against the same pair.

On Bob, install and sign in to Codex CLI **0.171.0** normally, then run this from
the integration checkout in a human terminal. Replace the config path with Bob's
existing responder bundle, and choose a new short private directory for each run:

```sh
uv run --frozen peer-relay codex-responder \
  --config /absolute/path/responder/client.json \
  --directory /private/tmp/peerrelay-bob-test-1
```

The command opens a dedicated native Codex terminal and activates the responder.
Keep that terminal and Alice's relay running. Do not activate response mode again
inside the new terminal. The launcher owns that lease and its code handles waits.

1. Leave Bob idle before submitting work. No model turns should be needed for
   queue checks or heartbeats.
2. Activate Alice's requester mode if necessary, then submit the disposable file
   task above with a new folder name. Bob should pick it up without a manual prompt.
3. Answer Bob's question in Alice's native form. Bob should resume automatically,
   create and verify the file, and complete. Confirm Alice's compact receipt and
   browser diff still work. Alice's completion delivery remains subject to her
   client's follow/background support; this launcher changes Bob's delivery only.
4. Send a follow-up on Alice. Bob should pick it up in the same dedicated session.
   Record any duplicate work, missing answer, or manual wakeup.
5. Explicitly disconnect Bob, or close his dedicated terminal. Verify requester
   diagnostics no longer show a connected responder. New work must not wake that
   closed session. A new launch requires a new session directory and explicit start.

Automated tests use scripted native turns and disposable relay state. This native
test establishes signed-in model/tool behavior and terminal delivery, which those
tests cannot prove. Record client versions, task/answer pickup, follow-ups and
disconnect separately. See [client sessions](client-sessions.md#dedicated-automatic-codex-responder-experimental)
for the exact native restrictions and remaining local-read limitation.
