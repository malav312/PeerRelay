#!/usr/bin/env python3
"""No-inference native metadata probe for macOS, with outbound IP denied.

Creates only temporary state, a credential-free Codex home, and an owned native
server. The production transport defaults to a metadata-only method allowlist.
By default it does not launch the TUI. --terminal also verifies an actual PTY
attachment, replying only to cursor-position queries. Neither mode starts a turn,
contacts a relay, reads native credentials, or accepts native prompts.
"""

import argparse
import asyncio
import fcntl
import json
import os
import pty
import shutil
import struct
import tempfile
import termios
import tomllib
from pathlib import Path

from agent_relay.codex_responder import (
    ResponderBridge,
    discover_mcp_servers,
    native_command,
    validate_config,
    validate_thread,
)
from agent_relay.native_codex import OwnedCodexSession


async def probe_terminal(session, *, timeout=12):
    """Observe the exact remote session in a PTY; never submit model/user input."""
    master, slave = pty.openpty()
    process = None
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 140, 0, 0))
        os.set_blocking(master, False)
        process = await asyncio.create_subprocess_exec(
            *session.resume_argv(), cwd=session.cwd, env=session.env,
            stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
        )
        session.terminal = process
        os.close(slave)
        slave = None
        output, pending, cursor_replies = bytearray(), b"", 0
        async with asyncio.timeout(timeout):
            while True:
                try:
                    chunk = os.read(master, 65536)
                except BlockingIOError:
                    chunk = b""
                except OSError as error:
                    raise RuntimeError("Native terminal closed before attachment") from error
                if chunk:
                    output.extend(chunk)
                    if len(output) > 256 * 1024:
                        raise RuntimeError("Native terminal exceeded probe output limit")
                    pending += chunk
                    while b"\x1b[6n" in pending:
                        _, pending = pending.split(b"\x1b[6n", 1)
                        os.write(master, b"\x1b[1;1R")
                        cursor_replies += 1
                    pending = pending[-3:]
                if process.returncode is not None:
                    raise RuntimeError("Native terminal exited before attachment")
                # These appear only after the remote thread has finished loading.
                if (
                    b"\x1b]0;PeerRelay owned session | " in output
                    and b"\x1b]7501;state=idle:app=codex" in output
                ):
                    break
                await asyncio.sleep(0.05)
        resumed = await session.rpc.call("thread/resume", {"threadId": session.thread_id})
        validate_thread(resumed)
        assert resumed["thread"]["id"] == session.thread_id
        validate_config((await session.rpc.call("config/read", {}))["config"])
        thread = (await session.rpc.call(
            "thread/read", {"threadId": session.thread_id, "includeTurns": True}
        ))["thread"]
        assert thread["turns"] == [] and thread["status"]["type"] == "idle"
        assert process.returncode is None
        return {"native_tui_attached": True, "terminal_cursor_replies": cursor_replies}
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                process.kill()
                await process.wait()
        if slave is not None:
            os.close(slave)
        os.close(master)


async def probe(root, *, terminal=False):
    codex = shutil.which("codex")
    sandbox = shutil.which("sandbox-exec")
    if not codex or not sandbox:
        raise RuntimeError("Probe requires Codex CLI and macOS sandbox-exec")
    home = root / "home"
    home.mkdir(mode=0o700)
    work = root / "work"
    work.mkdir(mode=0o700)
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
    marker = root / "unexpected-mcp-start"
    with (home / "config.toml").open("a") as config:
        config.write(
            '\n[mcp_servers."existing.peer"]\ncommand = "/usr/bin/touch"\n'
            f'args = [{json.dumps(str(marker))}]\n'
        )
        if terminal:
            # Only this newly created, empty fixture directory is pretrusted.
            # Never answer the TUI trust prompt or alter the user's Codex home.
            config.write(
                f'\n[projects.{json.dumps(str(work))}]\ntrust_level = "trusted"\n'
            )
    original_config = (home / "config.toml").read_bytes()
    profile = root / "no-network.sb"
    profile.write_text('(version 1)\n(allow default)\n(deny network-outbound (remote ip "*:*"))\n')
    env = {
        key: os.environ[key]
        for key in ("PATH", "TMPDIR", "USER", "LOGNAME", "LANG")
        if key in os.environ
    }
    env["CODEX_HOME"] = str(home)
    if terminal:
        env["TERM"] = "xterm-256color"
    servers = discover_mcp_servers(
        (sandbox, "-f", str(profile), *native_command(codex)), cwd=work, env=env
    )
    assert servers == ("existing.peer",)

    class Relay:
        role = "responder"

    bridge = ResponderBridge(Relay())
    schemas = await bridge.schemas()
    session = await OwnedCodexSession.start(
        root / "native",
        cwd=work,
        env=env,
        command=(sandbox, "-f", str(profile), *native_command(codex, servers)),
        terminal_command=(sandbox, "-f", str(profile), codex),
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
        terminal_result = await probe_terminal(session) if terminal else {}
        thread = (
            await session.rpc.call(
                "thread/read", {"threadId": session.thread_id, "includeTurns": True}
            )
        )["thread"]
        assert thread["id"] == session.thread_id
        assert thread["turns"] == []
        assert thread["status"]["type"] == "idle"
        assert not marker.exists()
        current_config = (home / "config.toml").read_bytes()
        if terminal:
            # The TUI records preferences such as screen-reader detection itself.
            assert tomllib.loads(current_config.decode())["mcp_servers"] == tomllib.loads(
                original_config.decode()
            )["mcp_servers"]
        else:
            assert current_config == original_config
        result = {
            "exact_thread_resumable": True,
            "thread_status": "idle",
            "model_turns": 0,
            "responder_tools_registered": len(schemas),
            "native_restrictions_validated": True,
            "existing_mcp_disabled_without_config_changes": True,
            "tui_argv_uses_exact_thread": session.thread_id in session.resume_argv(),
            **terminal_result,
        }
    finally:
        await session.close()
    result["owned_server_stopped"] = session.server.returncode is not None
    result["socket_removed"] = not session.socket.exists()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terminal", action="store_true", help="Verify real PTY remote attachment")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="pr-probe-", dir="/private/tmp") as directory:
        print(json.dumps(asyncio.run(probe(Path(directory), terminal=args.terminal)), indent=2))
