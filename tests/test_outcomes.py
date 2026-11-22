import asyncio
import json

import pytest
from test_service import relay, setup  # noqa: F401

from agent_relay.mcp_server import build_server
from agent_relay.presentation import task_view
from agent_relay.service import Denied, Relay


def completion(**changes):
    return dict(version=1, status="completed", verification="passed", limitations=[], **changes)


def test_structured_completion_persists_without_duplicating_summary_and_report_is_unchanged(relay):  # noqa: F811
    service, root, clock = relay
    mode, lease, task = setup(service)
    outcome = completion()
    summary = "Verified the requested implementation."
    answer = "The full requested report."
    service.call(
        "responder",
        "complete",
        dict(
            lease_id=lease,
            task_id=task,
            summary=summary,
            answer=answer,
            completion=outcome,
        ),
    )
    service.close()
    with_restarted = Relay(root, service.state, clock=lambda: clock[0])
    try:
        status = with_restarted.call("requester", "status", dict(mode_id=mode, task_id=task))
        assert status["completion"] == outcome
        assert status["answer_available"] is True
        assert "summary" not in task_view(status)
        assert task_view(status, details=True)["summary"] == summary
        assert with_restarted.call("requester", "result", dict(mode_id=mode, task_id=task)) == {
            "task_id": task,
            "summary": summary,
            "answer": answer,
        }
        stored = with_restarted.db.execute(
            "SELECT completion FROM tasks WHERE id=?", (task,)
        ).fetchone()[0]
        assert summary not in stored
    finally:
        with_restarted.close()


@pytest.mark.parametrize(
    "outcome",
    [
        {},
        [],
        "completed",
        1,
        True,
        {"version": 2, "status": "completed", "verification": "passed", "limitations": []},
        {"version": True, "status": "completed", "verification": "passed", "limitations": []},
        {"version": "1", "status": "completed", "verification": "passed", "limitations": []},
        {"version": 1.0, "status": "completed", "verification": "passed", "limitations": []},
        {"version": 1, "status": "success", "verification": "passed", "limitations": []},
        {"version": 1, "status": "completed", "verification": "maybe", "limitations": []},
        {"version": 1, "status": "completed", "verification": "passed"},
        {"version": 1, "status": "completed", "verification": "passed", "limitations": [" "]},
        {"version": 1, "status": "completed", "verification": "passed", "limitations": [7]},
        {"version": 1, "status": "completed", "verification": "passed", "limitations": ["x" * 301]},
        {"version": 1, "status": "completed", "verification": "passed", "limitations": ["x"] * 5},
        {
            "version": 1,
            "status": "completed",
            "verification": "passed",
            "limitations": [],
            "summary": "duplicated",
        },
    ],
)
def test_invalid_completion_is_rejected_without_completing_task(relay, outcome):  # noqa: F811
    service, _, _ = relay
    mode, lease, task = setup(service)
    with pytest.raises(Denied):
        service.call(
            "responder",
            "complete",
            dict(
                lease_id=lease,
                task_id=task,
                summary="done",
                completion=outcome,
            ),
        )
    assert (
        service.call("requester", "status", dict(mode_id=mode, task_id=task))["state"] == "running"
    )


def test_legacy_database_migrates_and_legacy_completion_keeps_limitations(relay):  # noqa: F811
    service, root, clock = relay
    mode, lease, task = setup(service)
    service.call(
        "responder",
        "complete",
        dict(
            lease_id=lease,
            task_id=task,
            summary="Partial: could not run integration tests.",
        ),
    )
    # Recreate the pre-completion-column schema, preserving an actual legacy task.
    if "completion" in {row[1] for row in service.db.execute("PRAGMA table_info(tasks)")}:
        service.db.execute("ALTER TABLE tasks DROP COLUMN completion")
        service.db.commit()
    service.close()
    restarted = Relay(root, service.state, clock=lambda: clock[0])
    try:
        result = task_view(restarted.call("requester", "status", dict(mode_id=mode, task_id=task)))
        assert result["completion"] is None
        assert result["summary"] == "Partial: could not run integration tests."
        assert result["answer_available"] is False
    finally:
        restarted.close()


@pytest.mark.parametrize("stored", ['{"version":2}', "{broken"])
def test_malformed_persisted_completion_preserves_legacy_summary(relay, stored):  # noqa: F811
    service, _, _ = relay
    mode, lease, task = setup(service)
    service.call("responder", "complete", dict(lease_id=lease, task_id=task, summary="Blocked."))
    service.db.execute("UPDATE tasks SET completion=? WHERE id=?", (stored, task))
    service.db.commit()
    result = task_view(service.call("requester", "status", dict(mode_id=mode, task_id=task)))
    assert result["completion"] is None
    assert result["summary"] == "Blocked."


def test_mcp_completion_has_bounded_schema_and_accepts_optional_outcome(relay):  # noqa: F811
    service, _, _ = relay
    mode, lease, task = setup(service)

    class Responder:
        role = "responder"

        def call(self, method, arguments):
            return service.call(self.role, method, arguments)

    async def exercise():
        server = build_server(Responder())
        schema = next(t.inputSchema for t in await server.list_tools() if t.name == "complete_task")
        assert "completion" in schema["properties"]
        assert "completion" not in schema["required"]
        schema_text = json.dumps(schema)
        assert '"maxItems": 4' in schema_text and '"maxLength": 300' in schema_text
        result = await server.call_tool(
            "complete_task",
            dict(
                lease_id=lease,
                task_id=task,
                summary="done",
                completion=completion(),
            ),
        )
        assert json.loads(result[0].text)["state"] == "succeeded"

    asyncio.run(exercise())
    assert (
        service.call("requester", "status", dict(mode_id=mode, task_id=task))["completion"]
        == completion()
    )


@pytest.mark.parametrize(
    "status,verification",
    [
        ("partial", "failed"),
        ("blocked", "not_run"),
        ("completed", "not_applicable"),
    ],
)
def test_non_success_outcomes_and_verification_modes_survive_service(relay, status, verification):  # noqa: F811
    service, _, _ = relay
    mode, lease, task = setup(service)
    outcome = {
        "version": 1,
        "status": status,
        "verification": verification,
        "limitations": ["Human acceptance remains required."],
    }
    service.call(
        "responder",
        "complete",
        dict(
            lease_id=lease,
            task_id=task,
            summary="See limitations.",
            completion=outcome,
        ),
    )
    result = task_view(service.call("requester", "status", dict(mode_id=mode, task_id=task)))
    assert result["completion"] == outcome
    assert result["display"] == "Needs attention."
    assert result["answer_available"] is False


def test_compact_completion_does_not_create_browser_link_until_requested():
    import asyncio
    import json

    from agent_relay.mcp_server import build_server

    class Client:
        role = "requester"

        def call(self, method, arguments):
            assert method == "status"
            return {
                "task_id": "task", "state": "succeeded", "summary": "Long successful story",
                "commands": [], "questions": [], "proposals": [],
                "completion": {"version": 1, "status": "completed",
                               "verification": "passed", "limitations": []},
                "changes": {"state": "available", "files_changed": 1,
                            "additions": 1, "deletions": 0},
            }

    async def run():
        server = build_server(Client())
        links = []

        def link(mode, task):
            links.append((mode, task))
            return "http://127.0.0.1/fixture"

        server.changes_viewer.link = link
        args = {"mode_id": "mode", "task_id": "task"}
        result = await server.call_tool("task_status", args)
        compact = json.loads(result[0].text)
        assert "summary" not in compact and "view_url" not in compact["changes"]
        assert links == []
        result = await server.call_tool("view_changes", args)
        assert json.loads(result[0].text)["changes"]["view_url"].endswith("/fixture")
        assert links == [("mode", "task")]
    asyncio.run(run())


def test_optional_completion_keeps_legacy_wire_arguments():
    import asyncio

    from agent_relay.mcp_server import build_server

    class LegacyClient:
        role = "responder"

        def call(self, method, arguments):
            assert method == "complete"
            assert set(arguments) == {"lease_id", "task_id", "summary", "answer"}
            return {"state": "succeeded"}

    asyncio.run(build_server(LegacyClient()).call_tool("complete_task", {
        "lease_id": "lease", "task_id": "task", "summary": "Done",
    }))
