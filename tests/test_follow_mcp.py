import asyncio
import sys
from pathlib import Path

import pytest
import test_mcp
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import ElicitResult
from test_mcp import decode

transport_pair = test_mcp.live_pair


@pytest.mark.parametrize("transport_pair", ["exec"], indirect=True)
def test_single_sdk_call_handles_questions_and_reconnect_followup(transport_pair):
    pair, _, _ = transport_pair

    def params(role):
        return StdioServerParameters(
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
            command=sys.executable,
            args=["-m", "agent_relay.cli", "mcp", "--config", str(pair / role / "client.json")],
        )

    async def exercise():
        received = []

        async def elicit(ctx, request):
            received.append(request.message)
            assert request.requestedSchema["properties"]["answer"]["title"] == "Your answer"
            return ElicitResult(action="accept", content={"answer": "Custom — 多字"})

        async with (
            stdio_client(params("requester")) as (ar, aw),
            ClientSession(ar, aw, elicitation_callback=elicit) as a,
            stdio_client(params("responder")) as (br, bw),
            ClientSession(br, bw) as b,
        ):
            await a.initialize()
            await b.initialize()
            mode = decode(await a.call_tool("requester_mode", {"execution": True}))["mode_id"]
            diagnostics = decode(await a.call_tool("requester_diagnostics", {"mode_id": mode}))
            assert diagnostics["form_elicitation_advertised"] is True
            assert diagnostics["ui_display_verified"] is False
            lease = decode(await b.call_tool("response_mode", {}))["lease_id"]
            args = dict(mode_id=mode, operation_id="stable", prompt="first", seconds=8)
            pending = asyncio.create_task(a.call_tool("submit_and_follow", args))
            claimed = decode(await b.call_tool("wait_for_task", dict(lease_id=lease, seconds=5)))
            common = dict(lease_id=lease, task_id=claimed["task_id"])
            for number in range(2):
                q = decode(
                    await b.call_tool(
                        "ask_requester",
                        dict(
                            **common,
                            operation_id=str(number),
                            message="Which separator—one or many?",
                            choices=["One character", "Any string (recommended)"],
                        ),
                    )
                )
                answer = decode(
                    await b.call_tool(
                        "wait_for_answer", dict(**common, question_id=q["question_id"], seconds=5)
                    )
                )
                assert answer["action"] == "accept"
                assert answer["answer"] == "Custom — 多字"
            decode(await b.call_tool("complete_task", dict(**common, summary="done")))
            result = decode(await pending)
            assert result["state"] == "succeeded" and len(received) == 2
            assert received == [
                "Which separator—one or many?\n\n1. One character\n2. Any string (recommended)"
            ] * 2
            assert result["questions"] == []
            duplicate = decode(await a.call_tool("submit_and_follow", dict(args, seconds=0)))
            assert duplicate["task_id"] == claimed["task_id"]
            decode(await b.call_tool("disconnect", dict(lease_id=lease)))
            resumed = decode(await a.call_tool("resume_requester_mode", dict(mode_id=mode)))
            assert resumed["execution"] is True
            lease = decode(await b.call_tool("response_mode", {}))["lease_id"]
            next_task = decode(
                await a.call_tool(
                    "submit_and_follow",
                    dict(mode_id=mode, operation_id="second", prompt="continue", seconds=0),
                )
            )
            claimed = decode(await b.call_tool("wait_for_task", dict(lease_id=lease, seconds=0)))
            assert claimed["task_id"] == next_task["task_id"]
            assert claimed["writes"] == "commands"
            assert claimed["context"] == [{"prompt": "first", "summary": "done"}]
            decode(await b.call_tool("disconnect", dict(lease_id=lease)))

    asyncio.run(exercise())


def test_claude_channel_capability_and_metadata_on_real_stdio(transport_pair):
    import json

    from agent_relay.client import Client, load

    pair, _, _ = transport_pair

    async def exercise():
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "agent_relay.cli",
            "mcp",
            "--config",
            str(pair / "requester/client.json"),
            "--background",
            "claude-channel",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        events = []

        async def send(message):
            process.stdin.write((json.dumps(message) + "\n").encode())
            await process.stdin.drain()

        async def response(ident):
            while True:
                raw = await asyncio.wait_for(process.stdout.readline(), 5)
                assert raw, "MCP process exited"
                item = json.loads(raw)
                if item.get("id") == ident:
                    assert "error" not in item, item
                    return item["result"]
                events.append(item)

        async def tool(ident, name, arguments):
            await send(
                dict(
                    jsonrpc="2.0",
                    id=ident,
                    method="tools/call",
                    params=dict(name=name, arguments=arguments),
                )
            )
            result = await response(ident)
            assert not result.get("isError"), result
            return json.loads(result["content"][0]["text"])

        bob = Client(load(pair / "responder/client.json"))
        try:
            await send(
                dict(
                    jsonrpc="2.0",
                    id=1,
                    method="initialize",
                    params=dict(
                        protocolVersion="2025-11-25",
                        capabilities={},
                        clientInfo=dict(name="claude-code", version="wire-test"),
                    ),
                )
            )
            init = await response(1)
            assert init["capabilities"]["experimental"]["claude/channel"] == {}
            assert "claude/channel/permission" not in init["capabilities"]["experimental"]
            await send(dict(jsonrpc="2.0", method="notifications/initialized"))
            mode = (await tool(2, "requester_mode", {}))["mode_id"]
            lease = (await asyncio.to_thread(bob.call, "response_mode", {}))["lease_id"]
            task = await tool(
                3,
                "submit_and_follow",
                dict(mode_id=mode, operation_id="one", prompt="test", seconds=0),
            )
            assert task["background"]["state"] == "watching"
            await asyncio.to_thread(bob.call, "claim", dict(lease_id=lease))
            await asyncio.to_thread(
                bob.call,
                "complete",
                dict(
                    lease_id=lease,
                    task_id=task["task_id"],
                    summary="REMOTE TEXT MUST NOT BE PUSHED",
                ),
            )
            event = json.loads(await asyncio.wait_for(process.stdout.readline(), 5))
            assert event["method"] == "notifications/claude/channel"
            assert event["params"]["meta"]["event_kind"] == "task_finished"
            assert "REMOTE TEXT" not in json.dumps(event)
            meta = event["params"]["meta"]
            ack = await tool(
                4, "acknowledge_event", dict(watch_id=meta["watch_id"], event_id=meta["event_id"])
            )
            assert ack["state"] == "acknowledged"
        finally:
            await asyncio.to_thread(bob.close)
            process.terminate()
            await asyncio.wait_for(process.wait(), 5)

    asyncio.run(exercise())
