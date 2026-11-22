# Mode-driven pairing bootstrap

The bootstrap is a stable MCP entry that starts before any pairing bundle exists.
It packages both entry skills, opens a local pairing view, and dispatches tools to
an explicitly bound requester or responder session after pairing. Existing
`pair`, `join`, `mcp --config` and `client-setup` commands remain supported.

## Prepare a fresh native chat

Run this from an installed PeerRelay environment, selecting `claude` or `codex`:

```sh
peer-relay bootstrap-setup --client codex \
  --state-dir /private/path/peerrelay-setups \
  --directory /private/path/new-chat
```

The chat directory must be new. Keep private setup state outside every shared
project. The command writes `mcp.json`, both role skills, and next steps. It does
not change global native-client settings or any existing live integration. Install
that MCP entry and open the chat directory so the native client discovers both
skills. The MCP command is stable across pairing and role activation:

```sh
peer-relay bootstrap --state-dir /private/path/peerrelay-setups
```

A raw MCP registration exposes both named prompts but does not portably install
bare slash commands. Keep the packaged skills with the registration. This packaging
does not enforce native local-tool restrictions; the existing native-client
acceptance requirements still apply.

## Pair and activate

1. Invoke requester-mode on the project owner's chat or response-mode on the
   colleague's chat. The skill starts setup using a fresh random chat `binding_id`.
   Keep the returned `setup_id` and that binding in the same chat's continuation.
2. Open the returned loopback URL. The requester chooses a project, access level
   and execution backend. Host execution requires its existing explicit credential
   exposure acknowledgement. The responder enters the spoken code in this view.
   An existing role bundle can instead be selected by its local `client.json` path;
   no credentials are copied and no pairing is initialized over that bundle.
3. Compare check numbers and accept on both local views. Pairing uses the existing
   SPAKE2 exchange, certificate pin and responder token digest. The code and check
   number stay out of MCP tool results, setup records and model prompts.
4. The requester selects **Start relay** in the view. This starts at most one
   relay for the new pair and checks authenticated pinned HTTPS readiness. An
   already running matching relay is reused. The responder can **Check relay**.
   Docker execution requires a usable local Docker backend; a startup failure
   does not silently switch to host execution.
5. Return to the chat. `setup_status` reports pairing and relay readiness separately.
   `role_tools` supplies the existing role's tool schemas and instructions;
   `role_call` invokes them through the same registration. Activate response mode
   first, then requester mode, retaining the usual lease, mode and operation IDs.

**Paired** means the credentials were saved. **Relay ready** means an authenticated
health check succeeded. Neither is a claim that a native responder agent is
connected or working. The role activation result establishes that connection.
The MCP host controls how the URL opens; no popup position or native form is promised.

## Resume, cancel and lifecycle

Repeat `setup_begin` with the same role and chat binding, or `setup_status` with the
stored IDs, to resume. Another role or another binding cannot take over that setup.
Separate chats have separate records; there is no global “latest role.” One active
MCP process owns each setup lock. A second process must resume the original chat
or wait until its owner exits; it cannot steal a live setup.

A failed or cancelled requester setup can retry its original project/backend
choices without recreating credentials. Once a host credential may have committed,
setup reports interrupted and requires deliberate existing-bundle recovery instead
of replacing a responder. Codes expire using the original pairing window and
attempt limits. A failed responder retry asks for the current code in the local
view. Cancellation interrupts pending confirmations and active pairing sockets;
discovery or a connection attempt can take a few seconds to return. After pairing,
use normal role exit/disconnect controls; cancelling setup does not revoke a saved
pair or undo remote work.

The local view is loopback-only, uses an unguessable per-process URL, rejects foreign
Host/Origin headers, disables caching/referrers/framing and loads no external assets.
Closing the MCP process closes its views, disconnects clients and stops only relays
it started. A restart retains the pairing but does not reconnect a response lease
or restart a relay automatically. Interrupted work still requires reconciliation.

## Verified locally and remaining acceptance

Disposable-state tests cover a bundle-free MCP handshake, both packaged entry
skills, per-chat binding/ownership, existing bundle reuse without credential
copies, cancellation/retry with pinned backend choices, matching check numbers
through real loopback cryptographic pairing, authenticated relay readiness, and
real stdio role dispatch. The stdio protocol path handles a requester task, an MCP form
elicitation callback with a custom Unicode answer, completion and responder
disconnect. The local setup page was also inspected in a browser; native-client
form rendering is separate acceptance.

Still required before declaring the whole seamless-session experience accepted:

- Fresh Claude/Codex UI acceptance in all four client/role combinations, including
  skill discovery, URL opening, native local-tool restrictions and real LAN/mDNS.
- Actual Docker execution acceptance in the selected runtime; unit coverage and
  proposal-grant relay startup do not establish Docker availability.
- Native automatic responder delivery and requester updates. Bootstrap currently
  dispatches role tools with background delivery off; it does not enable Claude
  channels or attach a Codex terminal controller.
- Per-chat IDs are an explicit protocol/skill binding, not a host-attested identity.
  Deterministic native input routing and automatic native session binding remain
  integration work. Role invocation is schema-driven through `role_call`, so native
  permission UIs see that dispatcher name rather than each inner tool name.

Run `pytest tests/test_bootstrap.py tests/test_pairing.py` for the focused path and
`pytest` for the complete suite. These tests use private temporary directories and
loopback servers; no provider credentials or live LAN peers are required.
