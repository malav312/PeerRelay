"""Real stdio and pinned HTTPS, using disposable loopback pairs and no model provider."""

import asyncio
import json
import sys
from pathlib import Path
from urllib.request import urlopen

import pytest
import test_mcp
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_mcp import decode

from agent_relay.client import Client, load

live_pair = test_mcp.live_pair


@pytest.mark.parametrize("live_pair", ["exec"], indirect=True)
def test_stdio_follow_returns_link_while_browser_alone_fetches_patch(live_pair):
    pair, root, _ = live_pair
    (root / "hello.txt").write_text("preexisting user edit\n")
    responder = Client(load(pair / "responder/client.json"))
    lease = responder.call("response_mode", {})["lease_id"]
    params = StdioServerParameters(
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        command=sys.executable,
        args=["-m", "agent_relay.cli", "mcp", "--config", str(pair / "requester/client.json")],
    )

    async def exercise():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as requester:
            await requester.initialize()
            mode = decode(await requester.call_tool("requester_mode", {"execution": True}))[
                "mode_id"
            ]
            pending = asyncio.create_task(
                requester.call_tool(
                    "submit_and_follow",
                    {
                        "mode_id": mode,
                        "operation_id": "first",
                        "prompt": "Edit hello",
                        "seconds": 8,
                    },
                )
            )
            task = await responder.wait_for_task(lease, 5)
            common = {"lease_id": lease, "task_id": task["task_id"]}
            job = await asyncio.to_thread(
                responder.call,
                "execute",
                dict(
                    **common,
                    operation_id="edit",
                    command="printf 'task patch sentinel\\n' > hello.txt",
                ),
            )
            for _ in range(100):
                state = await asyncio.to_thread(
                    responder.call,
                    "command_status",
                    dict(
                        **common,
                        command_id=job["command_id"],
                    ),
                )
                if state["state"] == "exited":
                    assert state["exit_code"] == 0
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("Disposable edit command did not finish")
            await asyncio.to_thread(responder.call, "complete", dict(**common, summary="Done"))
            result = decode(await pending)
            assert result["follow_state"] == "complete"
            assert result["changes"]["files_changed"] == 1
            assert result["changes"]["additions"] == result["changes"]["deletions"] == 1
            assert "sentinel" not in json.dumps(result) and "hello.txt" not in json.dumps(result)
            with urlopen(result["changes"]["view_url"]) as response:
                page = response.read().decode()
            assert "+task patch sentinel" in page and "-preexisting user edit" in page
            return result["changes"]["view_url"]

    try:
        url = asyncio.run(exercise())
        # The requesting stdio session is now closed, so its viewer has stopped.
        with pytest.raises(OSError):
            urlopen(url, timeout=1)
    finally:
        responder.close()
