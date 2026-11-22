import asyncio
from types import SimpleNamespace

import pytest
from mcp.types import ClientCapabilities, ElicitationCapability

from agent_relay.service import Denied, Relay


@pytest.fixture
def relay(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    r = Relay(root, tmp_path / "state", grant="exec")
    yield r
    r.close()


class DirectClient:
    role = "requester"

    def __init__(self, relay):
        self.relay = relay

    def call(self, method, arguments):
        return self.relay.call(self.role, method, arguments)


def test_mode_resume_keeps_execution_context_after_responder_reconnect(relay):
    mode = relay.call("requester", "requester_mode", {"execution": True})["mode_id"]
    lease = relay.call("responder", "response_mode", {})["lease_id"]
    task = relay.call(
        "requester", "submit", dict(mode_id=mode, operation_id="first", prompt="first")
    )
    relay.call("responder", "claim", dict(lease_id=lease))
    relay.call(
        "responder", "complete", dict(lease_id=lease, task_id=task["task_id"], summary="done")
    )
    relay.call("responder", "disconnect", dict(lease_id=lease))
    resumed = relay.call("requester", "mode_status", dict(mode_id=mode))
    assert resumed["execution"] is True and resumed["responder_connected"] is False
    lease = relay.call("responder", "response_mode", {})["lease_id"]
    relay.call("requester", "submit", dict(mode_id=mode, operation_id="next", prompt="continue"))
    claimed = relay.call("responder", "claim", dict(lease_id=lease))
    assert claimed["writes"] == "commands"
    assert claimed["context"] == [{"prompt": "first", "summary": "done"}]
    relay.call("requester", "exit_mode", dict(mode_id=mode))
    with pytest.raises(Denied):
        relay.call("requester", "mode_status", dict(mode_id=mode))


def context(supported, callback=None):
    caps = ClientCapabilities(elicitation=ElicitationCapability() if supported else None)
    return SimpleNamespace(
        session=SimpleNamespace(
            client_params=SimpleNamespace(
                capabilities=caps, clientInfo=SimpleNamespace(name="test-client", version="1")
            )
        ),
        elicit=callback,
    )


@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
def test_follow_handles_two_questions_without_returning_to_model(relay, action):
    from agent_relay.follow import follow

    mode = relay.call("requester", "requester_mode", {})["mode_id"]
    lease = relay.call("responder", "response_mode", {})["lease_id"]
    task = relay.call("requester", "submit", dict(mode_id=mode, operation_id="one", prompt="work"))[
        "task_id"
    ]
    relay.call("responder", "claim", dict(lease_id=lease))
    messages = []

    async def respond(message, schema):
        messages.append(message)
        return SimpleNamespace(
            action=action,
            data=SimpleNamespace(answer="Custom — 多字") if action == "accept" else None,
        )

    async def bob():
        for index in range(2):
            q = relay.call(
                "responder",
                "ask_question",
                dict(
                    lease_id=lease,
                    task_id=task,
                    operation_id=str(index),
                    message="Choose a format",
                    choices=["yes"],
                ),
            )
            while (
                relay.call(
                    "responder",
                    "question_status",
                    dict(lease_id=lease, task_id=task, question_id=q["question_id"]),
                )["state"]
                == "pending"
            ):
                await asyncio.sleep(0.01)
            answer = relay.questions.get(task, q["question_id"])
            assert answer["action"] == action
            assert answer["answer"] == ("Custom — 多字" if action == "accept" else "")
        relay.call("responder", "complete", dict(lease_id=lease, task_id=task, summary="done"))

    async def exercise():
        worker = asyncio.create_task(bob())
        result = await follow(DirectClient(relay), mode, task, context(True, respond), seconds=5)
        await worker
        return result

    result = asyncio.run(exercise())
    assert result["state"] == "succeeded"
    assert messages == ["Choose a format\n\n1. yes"] * 2
    assert result["delivery"]["answered"] == 2
    assert result["delivery"] == {"requested": 2, "answered": 2}


def test_follow_timeout_resume_and_fallback_do_not_resubmit(relay):
    from agent_relay.follow import follow

    mode = relay.call("requester", "requester_mode", {})["mode_id"]
    lease = relay.call("responder", "response_mode", {})["lease_id"]
    args = dict(mode_id=mode, operation_id="same", prompt="work")
    task = relay.call("requester", "submit", args)["task_id"]
    result = asyncio.run(follow(DirectClient(relay), mode, task, context(False), seconds=0))
    assert result["follow_state"] == "waiting"
    assert result["resume"] == {"mode_id": mode, "task_id": task}
    assert relay.call("requester", "submit", args)["task_id"] == task
    relay.call("responder", "claim", dict(lease_id=lease))
    q = relay.call(
        "responder",
        "ask_question",
        dict(
            lease_id=lease, task_id=task, operation_id="q", message="Which?", choices=["one", "two"]
        ),
    )
    result = asyncio.run(follow(DirectClient(relay), mode, task, context(False), seconds=0))
    assert result["follow_state"] == "needs_input"
    assert result["questions"][0]["question_id"] == q["question_id"]
    assert relay.questions.pending(task)


def test_cancelled_follow_does_not_cancel_or_replay_task(relay):
    from agent_relay.follow import follow

    mode = relay.call("requester", "requester_mode", {})["mode_id"]
    relay.call("responder", "response_mode", {})
    task = relay.call("requester", "submit", dict(mode_id=mode, operation_id="one", prompt="work"))[
        "task_id"
    ]

    async def exercise():
        waiting = asyncio.create_task(follow(DirectClient(relay), mode, task, context(False), 10))
        await asyncio.sleep(0.05)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting

    asyncio.run(exercise())
    assert relay.call("requester", "status", dict(mode_id=mode, task_id=task))["state"] == "queued"
