import asyncio
import json
import socket
import ssl
import subprocess
import sys
import time

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_relay.client import Client, load
from agent_relay.mcp_server import build_server
from agent_relay.setup import initialize


def test_role_specific_mcp_tool_sets(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    initialize(root, tmp_path / "pair", "127.0.0.1", 9443)
    for role, expected, forbidden in [
        ("requester", "approve_change", "remote_read"),
        ("responder", "remote_read", "approve_change"),
    ]:
        client = Client(load(tmp_path / f"pair/{role}/client.json"))
        try:
            names = {t.name for t in asyncio.run(build_server(client).list_tools())}
            assert expected in names
            assert forbidden not in names
        finally:
            client.close()


@pytest.fixture
def live_pair(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    root = tmp_path / "project"
    root.mkdir()
    (root / "hello.txt").write_text("before\n")
    pair = tmp_path / "pair"
    initialize(root, pair, "127.0.0.1", port)
    process = subprocess.Popen(
        [sys.executable, "-m", "agent_relay.cli", "serve", "--config", str(pair / "server.json")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        with httpx.Client(
            verify=ssl.create_default_context(cafile=pair / "gateway.crt"), trust_env=False
        ) as check:
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail("Relay startup failed: " + process.stderr.read().decode()[-2000:])
                try:
                    if check.get(f"https://127.0.0.1:{port}/healthz").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.05)
            else:
                pytest.fail("Relay startup timed out")
        yield pair, root, port
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stderr.close()


def decode(result):
    assert not result.isError, result.content
    return json.loads("".join(c.text for c in result.content if c.type == "text"))


def test_real_tls_rejects_untrusted_certificate(live_pair):
    _, _, port = live_pair
    with httpx.Client(trust_env=False) as client:
        with pytest.raises(httpx.ConnectError):
            client.get(f"https://127.0.0.1:{port}/healthz")


def test_sdk_stdio_approved_write_then_responder_disconnect(live_pair):
    pair, root, _ = live_pair

    async def run():
        aparams = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agent_relay.cli", "mcp", "--config", str(pair / "requester/client.json")],
        )
        bparams = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agent_relay.cli", "mcp", "--config", str(pair / "responder/client.json")],
        )
        async with stdio_client(aparams) as (ar, aw), ClientSession(ar, aw) as a:
            await a.initialize()
            mode = decode(await a.call_tool("requester_mode", {}))["mode_id"]
            async with stdio_client(bparams) as (br, bw), ClientSession(br, bw) as b:
                await b.initialize()
                lease = decode(await b.call_tool("response_mode", {}))["lease_id"]
                task = decode(
                    await a.call_tool(
                        "send_task",
                        {
                            "mode_id": mode,
                            "operation_id": "smoke",
                            "prompt": "Change before to after",
                        },
                    )
                )["task_id"]
                claimed = decode(
                    await b.call_tool("wait_for_task", {"lease_id": lease, "seconds": 0})
                )
                assert claimed["task_id"] == task
                read = decode(
                    await b.call_tool(
                        "remote_read", {"lease_id": lease, "task_id": task, "path": "hello.txt"}
                    )
                )
                proposed = decode(
                    await b.call_tool(
                        "propose_change",
                        {
                            "lease_id": lease,
                            "task_id": task,
                            "operation_id": "edit",
                            "path": "hello.txt",
                            "content": "after\n",
                            "base_version": read["version"],
                        },
                    )
                )
                assert (root / "hello.txt").read_text() == "before\n"
                complete = decode(
                    await b.call_tool(
                        "complete_task",
                        {
                            "lease_id": lease,
                            "task_id": task,
                            "summary": "One proposed change",
                            "answer": "",
                        },
                    )
                )
                assert complete["state"] == "succeeded"
                review = decode(
                    await a.call_tool(
                        "review_change", {"mode_id": mode, "proposal_id": proposed["proposal_id"]}
                    )
                )
                assert "+after" in review["diff"]
                args = {
                    "mode_id": mode,
                    "proposal_id": proposed["proposal_id"],
                    "digest": review["digest"],
                    "confirmation": "not approved",
                }
                assert (await a.call_tool("approve_change", args)).isError
                args["confirmation"] = f"APPLY {proposed['proposal_id']} {review['digest']}"
                applied = decode(await a.call_tool("approve_change", args))
                assert applied["status"] == "applied"
                assert (root / "hello.txt").read_text() == "after\n"
                assert decode(await a.call_tool("approve_change", args)) == applied
                assert "approve_change" not in {t.name for t in (await b.list_tools()).tools}
                decode(await b.call_tool("disconnect", {"lease_id": lease}))
                assert (
                    await a.call_tool(
                        "send_task",
                        {"mode_id": mode, "operation_id": "after-close", "prompt": "must fail"},
                    )
                ).isError
                status = decode(
                    await a.call_tool("task_status", {"mode_id": mode, "task_id": task})
                )
                assert status["responder_connected"] is False
                assert status["state"] == "succeeded"
            decode(await a.call_tool("exit_requester_mode", {"mode_id": mode}))

    asyncio.run(run())
