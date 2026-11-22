import asyncio

from test_follow import DirectClient, context, relay  # noqa: F401

from agent_relay.follow import follow
from agent_relay.mcp_server import build_server


def test_question_fallback_is_readable_without_mutating_stored_question(relay):  # noqa: F811
    mode = relay.call("requester", "requester_mode", {})["mode_id"]
    lease = relay.call("responder", "response_mode", {})["lease_id"]
    task = relay.call("requester", "submit", dict(mode_id=mode, operation_id="t", prompt="work"))[
        "task_id"
    ]
    relay.call("responder", "claim", dict(lease_id=lease))
    q = relay.call(
        "responder",
        "ask_question",
        dict(
            lease_id=lease,
            task_id=task,
            operation_id="q",
            message='Use “–” or "-"?',
            choices=["En dash –", "Hyphen -"],
        ),
    )
    result = asyncio.run(follow(DirectClient(relay), mode, task, context(False), seconds=0))
    assert result["display"] == "Need your input."
    assert result["questions"][0]["message"] == 'Use “–” or "-"?'
    assert result["questions"][0]["choices"] == ["En dash –", "Hyphen -"]
    assert result["resume"] == {"mode_id": mode, "task_id": task}
    assert result["questions"][0]["question_id"] == q["question_id"]
    assert relay.questions.get(task, q["question_id"]) == q


def test_status_compacts_command_history_and_retains_failures_and_details():
    class StatusClient:
        role = "requester"

        def call(self, method, arguments):
            assert method == "status"
            return {
                "task_id": "task",
                "state": "running",
                "summary": "",
                "questions": [],
                "proposals": [],
                "responder_connected": True,
                "commands": [
                    *[
                        {"command_id": f"ok-{n}", "state": "exited", "exit_code": 0}
                        for n in range(100)
                    ],
                    {"command_id": "failed", "state": "exited", "exit_code": 1},
                    {"command_id": "unknown", "state": "uncertain", "exit_code": None},
                ],
            }

    async def exercise():
        import json

        server = build_server(StatusClient())

        async def invoke(**extra):
            content = await server.call_tool(
                "task_status", dict(mode_id="mode", task_id="task", **extra)
            )
            return json.loads(content[0].text)

        return await invoke(), await invoke(details=True)

    compact, detailed = asyncio.run(exercise())
    assert "commands" not in compact
    assert compact["command_counts"] == {"exited": 101, "uncertain": 1}
    assert [item["command_id"] for item in compact["command_issues"]] == ["failed", "unknown"]
    assert len(detailed["commands"]) == 102
    assert compact["display"] == "Working."
    assert compact["_peer_content"] == {"origin": "Bob"}
