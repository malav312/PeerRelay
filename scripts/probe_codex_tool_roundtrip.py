#!/usr/bin/env python3
"""Exercise native exec -> dynamic tools -> role bridge with scripted responses.

No external inference, account credentials, live pair, or native prompts are used.
The disposable native server can contact localhost only. One synthetic turn calls
wait_for_task and remote_read on a fake relay, then verifies that apply_patch cannot
create a disposable marker. --terminal keeps a real, cursor-only PTY client attached
throughout the turn. --host-disabled reproduces the old error.
--requester submits once and verifies a monitor-delivered question in a second turn.
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
from pathlib import Path

from agent_relay.codex_requester import RequesterBridge
from agent_relay.codex_responder import (
    ResponderBridge,
    native_command,
    native_overrides,
    validate_config,
    validate_thread,
)
from agent_relay.delivery import Binding, DeliveryController, RelayMonitor
from agent_relay.native_codex import OwnedCodexSession

MODEL = "gpt-6.1-sol"
SCRIPT = """text("PROBE_INVENTORY:" + JSON.stringify(Object.keys(tools).sort()));
text(await tools.wait_for_task({lease_id:"lease",seconds:50}));
text(await tools.remote_read({lease_id:"lease",task_id:"task",path:"hello.txt"}));
try {
  await tools.apply_patch("*** Begin Patch\\n*** Add File: forbidden.txt\\n+probe\\n*** End Patch");
  text("PROBE_UNEXPECTED_WRITE");
} catch (error) {
  text("PROBE_READONLY_DENIAL:" + String(error));
}"""
REQUESTER_SCRIPT = """text("PROBE_INVENTORY:" + JSON.stringify(Object.keys(tools).sort()));
text(await tools.requester_session({}));
text(await tools.submit_and_follow({
  mode_id:"mode",operation_id:"op",prompt:"Synthetic task",seconds:50
}));
""" + SCRIPT[SCRIPT.index("try {"):]
FOLLOW_SCRIPT = """text(await tools.follow_task({mode_id:"mode",task_id:"task",seconds:50}));"""
BUILTIN_TOOLS = {
    "apply_patch", "clock__curr_time", "create_goal", "get_goal", "update_goal",
    "list_mcp_resources", "list_mcp_resource_templates", "read_mcp_resource",
}


def response_events(index, script=SCRIPT):
    if index % 2 == 1:
        item = {
            "type": "custom_tool_call", "id": f"exec_{index}", "call_id": f"call_{index}",
            "name": "exec", "input": script, "status": "completed",
        }
    else:
        item = {
            "type": "message", "id": f"message_{index}", "role": "assistant",
            "status": "completed", "content": [{
                "type": "output_text", "text": "Scripted provider complete.", "annotations": [],
            }],
        }
    response = {
        "id": f"response_{index}", "object": "response", "created_at": 1,
        "status": "completed", "model": MODEL, "output": [item],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }
    return [
        {"type": "response.created", "response": {
            **response, "status": "in_progress", "output": [],
        }},
        {"type": "response.output_item.added", "output_index": 0,
         "item": {**item, "status": "in_progress"}},
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": response},
    ]


async def attach_terminal(session):
    master, slave = pty.openpty()
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 140, 0, 0))
        os.set_blocking(master, False)
        session.terminal = await asyncio.create_subprocess_exec(
            *session.resume_argv(), cwd=session.cwd, env=session.env,
            stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
        )
    except BaseException:
        os.close(master)
        raise
    finally:
        os.close(slave)
    attached = asyncio.Event()

    async def pump():
        output, pending = bytearray(), b""
        try:
            while True:
                try:
                    chunk = os.read(master, 65536)
                    output.extend(chunk)
                    if len(output) > 256 * 1024:
                        raise RuntimeError("Native terminal exceeded probe output limit")
                    pending += chunk
                    while b"\x1b[6n" in pending:
                        _, pending = pending.split(b"\x1b[6n", 1)
                        os.write(master, b"\x1b[1;1R")
                    pending = pending[-3:]
                    if (b"\x1b]0;PeerRelay owned session | " in output
                            and b"\x1b]7501;state=idle:app=codex" in output):
                        attached.set()
                except BlockingIOError:
                    pass
                except OSError:
                    return
                await asyncio.sleep(0.05)
        finally:
            os.close(master)

    task = asyncio.create_task(pump())
    try:
        await asyncio.wait_for(attached.wait(), 12)
    except BaseException:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
    return task


async def probe(root, *, terminal=False, host_disabled=False, requester=False):
    codex, sandbox = shutil.which("codex"), shutil.which("sandbox-exec")
    if not codex or not sandbox:
        raise RuntimeError("Probe requires Codex CLI and macOS sandbox-exec")
    home, work = root / "home", root / "work"
    home.mkdir(mode=0o700)
    work.mkdir(mode=0o700)
    requests, provider_errors, relay_calls, bridge_calls = [], [], [], []
    expected_turns = 2 if requester and not host_disabled else 1

    async def serve(reader, writer):
        try:
            async with asyncio.timeout(10):
                header = (await reader.readuntil(b"\r\n\r\n")).decode()
                assert header.split("\r\n")[0] == "POST /v1/responses HTTP/1.1"
                size = next(int(line.split(":", 1)[1]) for line in header.split("\r\n")
                            if line.lower().startswith("content-length:"))
                assert 0 < size < 2 * 1024 * 1024
                request = json.loads(await reader.readexactly(size))
                assert request["model"] == MODEL
                requests.append(request)
                assert len(requests) <= 2 * expected_turns
                script = (REQUESTER_SCRIPT if len(requests) == 1 else FOLLOW_SCRIPT
                          ) if requester else SCRIPT
                payload = "".join(
                    f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                    for event in response_events(len(requests), script)
                ).encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: "
                    + str(len(payload)).encode() + b"\r\nConnection: close\r\n\r\n" + payload
                )
                await writer.drain()
        except Exception as error:
            provider_errors.append(type(error).__name__)
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    session, terminal_pump, bridge, controller = None, None, None, None
    try:
        (home / "config.toml").write_text(f'''model_provider = "peerrelay_probe"
model = "{MODEL}"
check_for_update_on_startup = false
[analytics]
enabled = false
[model_providers.peerrelay_probe]
name = "Local scripted probe"
base_url = "http://127.0.0.1:{port}/v1"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false
''')
        if terminal:
            with (home / "config.toml").open("a") as config:
                config.write(f'\n[projects.{json.dumps(str(work))}]\ntrust_level = "trusted"\n')
        profile = root / "localhost-only.sb"
        profile.write_text(
            '(version 1)\n(allow default)\n(deny network-outbound (remote ip "*:*"))\n'
            '(allow network-outbound (remote ip "localhost:*"))\n'
        )
        env = {key: os.environ[key] for key in ("PATH", "TMPDIR", "USER", "LOGNAME", "LANG")
               if key in os.environ}
        env.update(CODEX_HOME=str(home), TERM="xterm-256color")

        class Relay:
            role = "responder"

            async def wait_for_task(self, lease_id, seconds):
                assert lease_id == "lease" and seconds == 0
                relay_calls.append("wait_for_task")
                return {"state": "claimed", "task_id": "task"}

            def call(self, method, arguments):
                assert method == "remote_tool"
                assert arguments == {"lease_id": "lease", "task_id": "task",
                                     "path": "hello.txt", "kind": "read"}
                relay_calls.append("remote_read")
                return {"content": "LOCAL_TOOL_RESULT", "version": "1"}

        class RequesterRelay:
            role = "requester"
            questions = []

            def call(self, method, arguments):
                assert arguments["mode_id"] == "mode"
                relay_calls.append(method)
                if method == "mode_status":
                    assert arguments == {"mode_id": "mode"}
                    return {"workspace_matches": True, "responder_connected": True}
                if method == "submit":
                    assert arguments == {"mode_id": "mode", "operation_id": "op",
                                         "prompt": "Synthetic task"}
                    assert relay_calls.count("submit") == 1
                    return {"task_id": "task", "state": "queued"}
                if method == "status":
                    assert arguments == {"mode_id": "mode", "task_id": "task"}
                    return {"task_id": "task", "state": "running", "summary": "",
                            "questions": self.questions, "commands": [], "proposals": []}
                raise AssertionError(method)

        relay = RequesterRelay() if requester else Relay()
        bridge = RequesterBridge(relay) if requester else ResponderBridge(relay)
        schemas = await bridge.schemas()

        async def handle(params):
            result = await bridge.handle(params)
            bridge_calls.append((params["tool"], result["success"]))
            return result

        def check_config(config):
            assert config["features"]["code_mode_host"] is (not host_disabled)
            # The negative control changes this one flag intentionally.
            config = {**config, "features": {**config["features"], "code_mode_host":
                       native_overrides()["features"]["code_mode_host"]}}
            validate_config(config)

        command = (sandbox, "-f", str(profile), *native_command(codex))
        if host_disabled:
            command += ("-c", "features.code_mode_host=false")
        session = await OwnedCodexSession.start(
            root / "native", cwd=work, env=env, command=command,
            terminal_command=(sandbox, "-f", str(profile), codex),
            allow_turns=True, experimental=True, tool_handler=handle,
            validate_config=check_config,
            thread_options={"model": MODEL, "modelProvider": "peerrelay_probe",
                            "approvalPolicy": "never", "sandbox": "read-only",
                            "dynamicTools": schemas},
        )
        validate_thread(session.start_result)
        if requester:
            binding = Binding("requester", session.generation, session.thread_id, mode_id="mode")
            controller = DeliveryController.create(root / "delivery.sqlite", binding, session.rpc)
            monitor = RelayMonitor(controller, relay)
            bridge.bind(binding, monitor)
        else:
            bridge.bind(Binding(
                "responder", session.generation, session.thread_id, lease_id="lease"
            ))
        if terminal:
            terminal_pump = await attach_terminal(session)
        await session.rpc.call("turn/start", {
            "threadId": session.thread_id,
            "input": [{"type": "text", "text": "Run the synthetic fixture tool roundtrip."}],
        })
        async def wait_turn():
            async with asyncio.timeout(25):
                while True:
                    message = await session.rpc.notifications.get()
                    if controller:
                        controller.observe(session.generation, message)
                    if message["method"] == "turn/completed":
                        return

        await wait_turn()
        if requester and not host_disabled:
            assert monitor.tasks == {"task"}
            await monitor.poll()
            assert not await controller.dispatch()
            assert len(requests) == 2
            relay.questions = [{"question_id": "q", "message": "LOCAL_PENDING_QUESTION",
                                "choices": []}]
            await monitor.poll()
            assert await controller.dispatch()
            await wait_turn()
            await controller.reconcile()
            assert len(controller.records()) == 1
            assert controller.records()[0]["kind"] == "question_ready"
            assert controller.records()[0]["state"] == "completed"
            # Reading the question consumes its wakeup; an unchanged poll is quiet.
            await monitor.poll()
            assert not await controller.dispatch()
        thread = (await session.rpc.call("thread/read", {
            "threadId": session.thread_id, "includeTurns": True,
        }))["thread"]
        check_config((await session.rpc.call("config/read", {}))["config"])
        assert not provider_errors and len(requests) == 2 * expected_turns
        assert session.rpc.failure is None
        assert len(thread["turns"]) == expected_turns
        assert all(turn["status"] == "completed" for turn in thread["turns"])
        outputs = [item["output"] for item in requests[1]["input"]
                   if item["type"] == "custom_tool_call_output"]
        assert len(outputs) == 1
        tool_names = []
        if host_disabled:
            assert outputs == ["code-mode host is disabled"]
            assert not bridge_calls and not relay_calls
        else:
            texts = [item["text"] for item in outputs[0] if item["type"] == "input_text"]
            if requester:
                assert bridge_calls == [("requester_session", True), ("submit_and_follow", True),
                                        ("follow_task", True)]
                assert relay_calls.count("submit") == 1
                assert set(relay_calls) == {"mode_status", "submit", "status"}
                assert any('"follow_state": "waiting"' in text for text in texts)
                follow_outputs = [item["output"] for item in requests[3]["input"]
                                  if item["type"] == "custom_tool_call_output"
                                  and item["call_id"] == "call_3"]
                assert len(follow_outputs) == 1
                follow_text = json.dumps(follow_outputs[0])
                assert "LOCAL_PENDING_QUESTION" in follow_text and "needs_input" in follow_text
            else:
                assert bridge_calls == [("wait_for_task", True), ("remote_read", True)]
                assert relay_calls == ["wait_for_task", "remote_read"]
                assert any("LOCAL_TOOL_RESULT" in text for text in texts)
            inventory = next(text.removeprefix("PROBE_INVENTORY:") for text in texts
                             if text.startswith("PROBE_INVENTORY:"))
            tool_names = json.loads(inventory)
            assert set(tool_names) == {schema["name"] for schema in schemas} | BUILTIN_TOOLS
            assert any(text.startswith("PROBE_READONLY_DENIAL:")
                       and "writing is blocked by read-only sandbox" in text for text in texts)
        assert not (work / "forbidden.txt").exists()
        if terminal:
            assert session.terminal.returncode is None and not terminal_pump.done()
        result = {
            "external_inference_requests": 0, "scripted_provider_requests": len(requests),
            "synthetic_turns": expected_turns, "native_tui_attached": terminal,
            "role": "requester" if requester else "responder",
            "disabled_host_reproduced": host_disabled,
            "bridge_roundtrip_verified": not host_disabled, "runtime_tool_names": tool_names,
            "read_only_write_rejected": not host_disabled,
        }
        if requester:
            result["requester_submit_count"] = relay_calls.count("submit")
            result["question_delivery_verified"] = not host_disabled
            result["unchanged_status_stays_quiet"] = not host_disabled
    finally:
        if requester and bridge:
            await bridge.close()
        if session:
            await session.close()
        if controller:
            controller.close()
        if terminal_pump:
            terminal_pump.cancel()
            await asyncio.gather(terminal_pump, return_exceptions=True)
        server.close()
        await server.wait_closed()
    result["owned_server_stopped"] = session.server.returncode is not None
    result["socket_removed"] = not session.socket.exists()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terminal", action="store_true")
    parser.add_argument(
        "--requester", action="store_true", help="Verify requester automatic delivery"
    )
    parser.add_argument("--host-disabled", action="store_true", help="Verify the negative control")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="pr-tool-", dir="/private/tmp") as directory:
        print(json.dumps(asyncio.run(probe(Path(directory), terminal=args.terminal,
                                          host_disabled=args.host_disabled,
                                          requester=args.requester)), indent=2))
