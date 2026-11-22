import asyncio

from test_follow import DirectClient, context, relay  # noqa: F401

from agent_relay.follow import follow
from agent_relay.mcp_server import build_server


def test_completion_receipt_does_not_expand_peer_summary(relay):  # noqa: F811
    mode = relay.call("requester", "requester_mode", {})["mode_id"]
    lease = relay.call("responder", "response_mode", {})["lease_id"]
    task = relay.call("requester", "submit", dict(mode_id=mode, operation_id="t", prompt="work"))[
        "task_id"
    ]
    relay.call("responder", "claim", dict(lease_id=lease))
    summary = (
        "Created custom.txt containing question test passed plus a newline. "
        "All three commands finished without errors. A byte-for-byte check matched."
    )
    relay.call(
        "responder",
        "complete",
        dict(lease_id=lease, task_id=task, summary=summary, answer="Verification details."),
    )
    result = asyncio.run(follow(DirectClient(relay), mode, task, context(False), seconds=0))
    assert result["display"] == "Done."
    # Compact receipts must not destroy the report available on explicit request.
    report = relay.call("requester", "result", dict(mode_id=mode, task_id=task))
    assert report["summary"] == summary
    assert report["answer"] == "Verification details."


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


def test_structured_receipt_compacts_summary_with_measured_fixture_byte_reduction():
    import json

    from agent_relay.presentation import task_view

    result = {
        "task_id": "task",
        "state": "succeeded",
        "summary": "Long successful report. " * 80,
        "completion": {
            "version": 1,
            "status": "completed",
            "verification": "passed",
            "limitations": [],
        },
        "answer_available": True,
        "questions": [],
        "proposals": [],
        "changes": {"state": "available", "files_changed": 2, "additions": 7, "deletions": 3},
        "commands": [
            {"command_id": f"ok-{n}", "state": "exited", "exit_code": 0} for n in range(100)
        ],
    }
    compact = task_view(result)
    assert compact["display"] == "Done. 2 files changed, +7/-3."
    assert compact["answer_available"] is True
    assert "summary" not in compact and "commands" not in compact
    assert len(json.dumps(compact).encode()) < len(json.dumps(result).encode()) / 5
    assert task_view(result, details=True)["summary"] == result["summary"]
    assert "summary" in result  # Projection does not alter its source.


def test_partial_blocked_and_failed_or_missing_verification_do_not_display_done():
    from agent_relay.presentation import task_view

    for status, verification in [
        ("partial", "passed"),
        ("blocked", "not_run"),
        ("completed", "failed"),
        ("completed", "not_run"),
    ]:
        result = task_view(
            {
                "state": "succeeded",
                "summary": "A detailed report.",
                "commands": [],
                "completion": {
                    "version": 1,
                    "status": status,
                    "verification": verification,
                    "limitations": [],
                },
            }
        )
        assert result["display"] == "Needs attention."
        assert result["completion"]["status"] == status
        assert result["completion"]["verification"] == verification
        assert "summary" not in result


def test_failed_commands_stay_visible_even_when_completion_claims_success():
    from agent_relay.presentation import task_view

    failed = {"command_id": "failed", "state": "exited", "exit_code": 1}
    result = task_view(
        {
            "state": "succeeded",
            "summary": "done",
            "commands": [failed],
            "completion": {
                "version": 1,
                "status": "completed",
                "verification": "passed",
                "limitations": [],
            },
        }
    )
    assert result["display"] == "Needs attention."
    assert result["command_issues"] == [failed]


def test_invalid_or_future_completion_never_hides_summary():
    from agent_relay.presentation import task_view

    for completion in (None, {}, {"version": 2}, "invalid"):
        result = task_view(
            {
                "state": "succeeded",
                "summary": "Blocked on native acceptance.",
                "completion": completion,
                "commands": [],
            }
        )
        assert result["summary"] == "Blocked on native acceptance."
