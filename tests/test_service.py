import json

import pytest

from agent_relay.service import Denied, Relay


@pytest.fixture
def relay(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "hello.txt").write_text("before")
    clock = [1000.0]
    service = Relay(root, tmp_path / "state", clock=lambda: clock[0])
    yield service, root, clock
    service.close()


def setup(service):
    mode = service.call("requester", "requester_mode", {})["mode_id"]
    lease = service.call("responder", "response_mode", {})["lease_id"]
    task = service.call(
        "requester", "submit", {"mode_id": mode, "operation_id": "first", "prompt": "Fix hello"}
    )["task_id"]
    claimed = service.call("responder", "claim", {"lease_id": lease})
    assert claimed["task_id"] == task
    return mode, lease, task


def propose(service, lease, task):
    read = service.call(
        "responder",
        "remote_tool",
        {"lease_id": lease, "task_id": task, "kind": "read", "path": "hello.txt"},
    )
    return service.call(
        "responder",
        "propose",
        {
            "lease_id": lease,
            "task_id": task,
            "operation_id": "p1",
            "path": "hello.txt",
            "content": "after",
            "base_version": read["version"],
        },
    )


def test_full_flow_compact_answer_and_explicit_apply(relay):
    service, root, _ = relay
    mode, lease, task = setup(service)
    p = propose(service, lease, task)
    assert (root / "hello.txt").read_text() == "before"
    service.call(
        "responder",
        "complete",
        {
            "lease_id": lease,
            "task_id": task,
            "summary": "Ready to review",
            "answer": "detail " * 4000,
        },
    )
    status = service.call("requester", "status", {"mode_id": mode, "task_id": task})
    assert status["state"] == "succeeded" and status["summary"] == "Ready to review"
    assert len(json.dumps(status)) < 2500
    assert "answer" not in status and "content" not in json.dumps(status)
    review = service.call("requester", "review", {"mode_id": mode, "proposal_id": p["proposal_id"]})
    assert "-before" in review["diff"] and "+after" in review["diff"]
    args = {"mode_id": mode, "proposal_id": p["proposal_id"], "digest": review["digest"]}
    assert service.call("requester", "apply", args)["status"] == "applied"
    assert service.call("requester", "apply", args)["status"] == "applied"
    assert (root / "hello.txt").read_text() == "after"
    usage = service.call("requester", "usage", {"mode_id": mode})
    assert usage["requester_model_tokens"] is None
    assert usage["relay_model_calls"] == 0


def test_roles_modes_and_digests_cannot_be_confused(relay):
    service, root, _ = relay
    mode, lease, task = setup(service)
    p = propose(service, lease, task)
    other = service.call("requester", "requester_mode", {})["mode_id"]
    for role, method, args in [
        (
            "responder",
            "apply",
            {"mode_id": mode, "proposal_id": p["proposal_id"], "digest": p["digest"]},
        ),
        ("requester", "status", {"mode_id": other, "task_id": task}),
        (
            "requester",
            "apply",
            {"mode_id": mode, "proposal_id": p["proposal_id"], "digest": "wrong"},
        ),
        (
            "responder",
            "remote_tool",
            {"lease_id": "wrong", "task_id": task, "kind": "read", "path": "hello.txt"},
        ),
    ]:
        with pytest.raises(Denied):
            service.call(role, method, args)
    assert (root / "hello.txt").read_text() == "before"


def test_submit_replay_and_no_local_fallback(relay):
    service, _, _ = relay
    mode = service.call("requester", "requester_mode", {})["mode_id"]
    args = {"mode_id": mode, "operation_id": "id", "prompt": "task"}
    with pytest.raises(Denied):
        service.call("requester", "submit", args)
    service.call("responder", "response_mode", {})
    first = service.call("requester", "submit", args)
    assert service.call("requester", "submit", args) == first
    with pytest.raises(Denied):
        service.call("requester", "submit", dict(args, prompt="different"))


@pytest.mark.parametrize("disconnect", ["explicit", "expired", "requester"])
def test_disconnect_revokes_tools_and_proposals(relay, disconnect):
    service, root, clock = relay
    mode, lease, task = setup(service)
    p = propose(service, lease, task)
    if disconnect == "explicit":
        service.call("responder", "disconnect", {"lease_id": lease})
    elif disconnect == "requester":
        service.call("requester", "exit_mode", {"mode_id": mode})
    else:
        clock[0] += 61
    with pytest.raises(Denied):
        service.call(
            "responder",
            "remote_tool",
            {"lease_id": lease, "task_id": task, "kind": "read", "path": "hello.txt"},
        )
    with pytest.raises(Denied):
        service.call(
            "requester",
            "apply",
            {"mode_id": mode, "proposal_id": p["proposal_id"], "digest": p["digest"]},
        )
    assert (root / "hello.txt").read_text() == "before"


def test_reconnect_does_not_reclaim_interrupted_task(relay):
    service, _, _ = relay
    mode, lease, task = setup(service)
    service.call("responder", "disconnect", {"lease_id": lease})
    new = service.call("responder", "response_mode", {})["lease_id"]
    assert new != lease
    assert service.call("responder", "claim", {"lease_id": new}) == {"state": "idle"}
    assert (
        service.call("requester", "status", {"mode_id": mode, "task_id": task})["state"]
        == "interrupted"
    )


def test_followups_keep_bounded_same_chat_context(relay):
    service, _, _ = relay
    mode, lease, task = setup(service)
    service.call(
        "responder",
        "complete",
        {"lease_id": lease, "task_id": task, "summary": "First outcome", "answer": "long details"},
    )
    service.call(
        "requester", "submit", {"mode_id": mode, "operation_id": "second", "prompt": "follow up"}
    )
    claimed = service.call("responder", "claim", {"lease_id": lease})
    assert claimed["context"][0]["summary"] == "First outcome"
    assert claimed["prompt"] == "follow up"
    assert "long details" not in json.dumps(claimed)


def test_restart_revokes_active_lease_and_keeps_results(relay):
    service, root, clock = relay
    mode, lease, task = setup(service)
    service.call(
        "responder",
        "complete",
        {"lease_id": lease, "task_id": task, "summary": "saved", "answer": "result"},
    )
    service.close()
    resumed = Relay(root, root.parent / "state", clock=lambda: clock[0])
    try:
        assert (
            resumed.call("requester", "status", {"mode_id": mode, "task_id": task})["summary"]
            == "saved"
        )
        with pytest.raises(Denied):
            resumed.call("responder", "heartbeat", {"lease_id": lease})
    finally:
        resumed.close()


def test_path_escape_and_limits(relay):
    service, root, _ = relay
    mode, lease, task = setup(service)
    result = service.call(
        "responder",
        "remote_tool",
        {"lease_id": lease, "task_id": task, "kind": "read", "path": "../outside"},
    )
    assert result["status"] == "denied"
    with pytest.raises(Denied):
        service.call(
            "requester", "submit", {"mode_id": mode, "operation_id": "long", "prompt": "x" * 20001}
        )
    with pytest.raises(Denied):
        service.call(
            "responder",
            "remote_tool",
            {"lease_id": lease, "task_id": task, "kind": "create_file", "path": "bad"},
        )
