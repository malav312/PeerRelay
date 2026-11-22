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


@pytest.fixture
def live_pair(transport_pair):
    return transport_pair


@pytest.mark.parametrize("transport_pair", ["exec"], indirect=True)
@pytest.mark.parametrize("action", ["accept", "decline", "cancel", "unsupported"])
def test_two_mcp_clients_execute_and_forward_elicitation(live_pair, action):
    pair, root, _ = live_pair

    async def flow():
        received = []

        async def elicit(ctx, params):
            received.append(params.message)
            return ElicitResult(
                action=action, content={"answer": "blue"} if action == "accept" else None
            )

        def params(role):
            return StdioServerParameters(
                env={"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
                command=sys.executable,
                args=["-m", "agent_relay.cli", "mcp", "--config", str(pair / role / "client.json")],
            )

        async with (
            stdio_client(params("requester")) as (ar, aw),
            ClientSession(
                ar, aw, elicitation_callback=None if action == "unsupported" else elicit
            ) as a,
        ):
            await a.initialize()
            mode = decode(await a.call_tool("requester_mode", {"execution": True}))["mode_id"]
            async with stdio_client(params("responder")) as (br, bw), ClientSession(br, bw) as b:
                await b.initialize()
                lease = decode(await b.call_tool("response_mode", {}))["lease_id"]
                task = decode(
                    await a.call_tool(
                        "send_task",
                        dict(
                            mode_id=mode,
                            operation_id="task",
                            prompt="Make a change and ask a question",
                        ),
                    )
                )["task_id"]
                claimed = decode(
                    await b.call_tool("wait_for_task", dict(lease_id=lease, seconds=0))
                )
                assert claimed["writes"] == "commands"
                common = dict(lease_id=lease, task_id=task)
                job = decode(
                    await b.call_tool(
                        "remote_execute",
                        dict(
                            **common, operation_id="edit", command="printf 'automatic' > hello.txt"
                        ),
                    )
                )
                done = decode(
                    await b.call_tool(
                        "wait_for_command", dict(**common, command_id=job["command_id"], seconds=5)
                    )
                )
                assert done["state"] == "exited" and done["exit_code"] == 0
                assert (root / "hello.txt").read_text() == "automatic"
                q = decode(
                    await b.call_tool(
                        "ask_requester",
                        dict(
                            **common,
                            operation_id="choice",
                            message="Choose a colour",
                            choices=["blue", "green"],
                        ),
                    )
                )
                result = decode(
                    await a.call_tool(
                        "wait_for_update", dict(mode_id=mode, task_id=task, seconds=0)
                    )
                )
                if action == "unsupported":
                    assert result["questions"][0]["message"] == "Choose a colour"
                    assert not received
                    decode(
                        await a.call_tool(
                            "answer_question",
                            dict(
                                mode_id=mode,
                                task_id=task,
                                question_id=q["question_id"],
                                action="accept",
                                answer="green",
                            ),
                        )
                    )
                else:
                    assert received and "Choose a colour" in received[0]
                    assert result["question_response"]["action"] == action
                response = decode(
                    await b.call_tool(
                        "wait_for_answer", dict(**common, question_id=q["question_id"], seconds=0)
                    )
                )
                assert response["state"] != "pending"
                assert response["answer"] == (
                    "green" if action == "unsupported" else "blue" if action == "accept" else ""
                )
                decode(await b.call_tool("complete_task", dict(**common, summary="done")))
                final = decode(
                    await a.call_tool(
                        "wait_for_update", dict(mode_id=mode, task_id=task, seconds=0)
                    )
                )
                assert final["state"] == "succeeded"
                decode(await b.call_tool("disconnect", {"lease_id": lease}))

    asyncio.run(flow())
