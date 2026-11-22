# Combined setup and changes-view acceptance

This branch combines bootstrap pairing, normal questions, concise requester
receipts, native diff guidance and the local changes viewer. It does not yet enable
automatic native responder delivery. Manual responder resume can still be needed.

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
