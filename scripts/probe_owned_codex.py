#!/usr/bin/env python3
"""No-inference native metadata probe for macOS, with outbound IP denied.

Creates only temporary state, a credential-free Codex home, and an owned native
server. The production transport defaults to a metadata-only method allowlist.
It does not launch the TUI, start a turn, contact a relay or read native credentials.
"""

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

from agent_relay.codex_responder import (
    ResponderBridge,
    native_command,
    validate_config,
    validate_thread,
)
from agent_relay.native_codex import OwnedCodexSession


async def probe(root):
    codex = shutil.which("codex")
    sandbox = shutil.which("sandbox-exec")
    if not codex or not sandbox:
        raise RuntimeError("Probe requires Codex CLI and macOS sandbox-exec")
    home = root / "home"
    home.mkdir(mode=0o700)
    (home / "config.toml").write_text("""model_provider = "peerrelay_probe"
model = "no-inference"
check_for_update_on_startup = false
[analytics]
enabled = false
[model_providers.peerrelay_probe]
name = "No inference probe"
base_url = "http://127.0.0.1:9/v1"
wire_api = "responses"
requires_openai_auth = false
""")
    profile = root / "no-network.sb"
    profile.write_text('(version 1)\n(allow default)\n(deny network-outbound (remote ip "*:*"))\n')
    env = {
        key: os.environ[key]
        for key in ("PATH", "TMPDIR", "USER", "LOGNAME", "LANG")
        if key in os.environ
    }
    env["CODEX_HOME"] = str(home)

    class Relay:
        role = "responder"

    bridge = ResponderBridge(Relay())
    schemas = await bridge.schemas()
    session = await OwnedCodexSession.start(
        root / "native",
        cwd=root,
        env=env,
        command=(sandbox, "-f", str(profile), *native_command(codex)),
        experimental=True,
        validate_config=validate_config,
        tool_handler=bridge.handle,
        thread_options={
            "model": "no-inference",
            "modelProvider": "peerrelay_probe",
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "dynamicTools": schemas,
        },
    )
    try:
        validate_thread(session.start_result)
        thread = (
            await session.rpc.call(
                "thread/read", {"threadId": session.thread_id, "includeTurns": True}
            )
        )["thread"]
        assert thread["id"] == session.thread_id
        assert thread["turns"] == []
        assert thread["status"]["type"] == "idle"
        result = {
            "exact_thread_resumable": True,
            "thread_status": "idle",
            "model_turns": 0,
            "responder_tools_registered": len(schemas),
            "native_restrictions_validated": True,
            "tui_argv_uses_exact_thread": session.thread_id in session.resume_argv(),
        }
    finally:
        await session.close()
    result["owned_server_stopped"] = session.server.returncode is not None
    result["socket_removed"] = not session.socket.exists()
    return result


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="pr-probe-", dir="/private/tmp") as directory:
        print(json.dumps(asyncio.run(probe(Path(directory))), indent=2))
