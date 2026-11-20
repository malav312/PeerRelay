import pytest
import test_execution
from test_execution import finish, run

from agent_relay.service import Denied, Relay

execution_session = test_execution.session


@pytest.fixture
def session(execution_session):
    return execution_session


def ask(relay, lease, task, operation="question"):
    return relay.call(
        "responder",
        "ask_question",
        dict(
            lease_id=lease,
            task_id=task,
            operation_id=operation,
            message="Which format?",
            choices=["JSON", "CSV"],
        ),
    )


def answer(relay, mode, task, question, action="accept", text="JSON"):
    return relay.call(
        "requester",
        "answer_question",
        dict(
            mode_id=mode,
            task_id=task,
            question_id=question["question_id"],
            action=action,
            answer=text,
        ),
    )


@pytest.mark.parametrize(
    "action,text,state",
    [("accept", "JSON", "answered"), ("decline", "", "declined"), ("cancel", "", "cancelled")],
)
def test_question_response_is_bound_durable_and_idempotent(session, action, text, state):
    relay, _, mode, lease, task = session
    q = ask(relay, lease, task)
    assert ask(relay, lease, task) == q
    view = relay.call("requester", "status", dict(mode_id=mode, task_id=task))
    assert view["questions"][0]["message"] == "Which format?"
    response = answer(relay, mode, task, q, action, text)
    assert response["state"] == state
    assert answer(relay, mode, task, q, action, text) == response
    b = relay.call(
        "responder",
        "question_status",
        dict(lease_id=lease, task_id=task, question_id=q["question_id"]),
    )
    assert b["answer"] == text and b["action"] == action
    with pytest.raises((Denied, ValueError)):
        answer(relay, mode, task, q, "accept", "changed")
    with pytest.raises(Denied):
        relay.call(
            "responder",
            "answer_question",
            dict(
                mode_id=mode,
                task_id=task,
                question_id=q["question_id"],
                action="accept",
                answer="forged",
            ),
        )
    wrong = relay.call("requester", "requester_mode", {})["mode_id"]
    with pytest.raises(Denied):
        answer(relay, wrong, task, q)


def test_pending_question_blocks_execution_but_answer_allows_continuation(session):
    relay, root, mode, lease, task = session
    q = ask(relay, lease, task)
    with pytest.raises(Denied):
        run(relay, lease, task, "touch early")
    with pytest.raises(Denied):
        relay.call("responder", "complete", dict(lease_id=lease, task_id=task, summary="done"))
    answer(relay, mode, task, q)
    job = run(relay, lease, task, "touch after")
    assert finish(relay, lease, task, job)["exit_code"] == 0
    assert (root / "after").exists() and not (root / "early").exists()


@pytest.mark.parametrize("event", ["disconnect", "cancel", "exit", "restart"])
def test_revocation_invalidates_pending_reply(session, event):
    relay, root, mode, lease, task = session
    q = ask(relay, lease, task)
    replacement = None
    if event == "disconnect":
        relay.call("responder", "disconnect", {"lease_id": lease})
    elif event == "cancel":
        relay.call("requester", "cancel", dict(mode_id=mode, task_id=task))
    elif event == "exit":
        relay.call("requester", "exit_mode", {"mode_id": mode})
    else:
        relay.close()
        replacement = Relay(root, relay.state, grant="exec")
    try:
        with pytest.raises(Denied):
            answer(replacement or relay, mode, task, q)
        view = (replacement or relay).call("requester", "status", dict(mode_id=mode, task_id=task))
        assert view["questions"] == []
    finally:
        if replacement:
            replacement.close()


def test_question_id_cannot_be_reused_for_different_prompt(session):
    relay, _, _, lease, task = session
    ask(relay, lease, task)
    with pytest.raises((Denied, ValueError)):
        relay.call(
            "responder",
            "ask_question",
            dict(lease_id=lease, task_id=task, operation_id="question", message="Changed?"),
        )
