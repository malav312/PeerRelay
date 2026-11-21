import asyncio
import json

import pytest
import test_writes

from agent_relay.api import Boundary
from agent_relay.approvals import Approvals
from agent_relay.schemas import Tool

fs = test_writes.fs


@pytest.mark.parametrize(
    "path",
    [".git/config", ".env", "sub/.env.local", "private.pem", ".ssh/id_rsa", ".codex/auth.json"],
)
def test_secret_paths_never_reach_read_list_search_or_proposal(fs, path):
    value, root = fs
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("private-marker")
    payload = Tool(kind="read", root="project", path=path).model_dump()
    assert value.execute("read", payload, {"project": "ro"})["status"] == "denied"
    listing = value.execute("list", dict(kind="list", root="project", path="."), {"project": "ro"})
    assert (
        path.split("/")[0] not in [e["name"] for e in listing["entries"]]
        or "/" in path
        and not path.startswith(".")
    )
    search = value.execute(
        "search",
        dict(kind="search", root="project", path=".", query="private-marker"),
        {"project": "ro"},
    )
    assert not search["matches"]
    proposal = Tool(
        kind="propose_replace", root="project", path=path, content="bad", expected_version="x"
    ).model_dump()
    assert value.execute("propose", proposal, {"project": "rw"})["status"] == "denied"


def test_ignore_rules_are_additive_live_and_fail_closed(fs):
    value, root = fs
    (root / "notes.txt").write_text("private-marker")
    payload = Tool(kind="read", root="project", path="notes.txt").model_dump()
    assert value.execute("same", payload, {"project": "ro"})["status"] == "succeeded"
    (root / ".peerrelayignore").write_text("notes.txt\n")
    assert value.execute("same", payload, {"project": "ro"})["status"] == "denied"
    (root / ".peerrelayignore").write_text("!.env\n")
    assert (
        value.execute("invalid", dict(payload, path="anything"), {"project": "ro"})["status"]
        == "denied"
    )
    (root / ".peerrelayignore").unlink()
    (root / ".peerrelayignore").symlink_to(root / "notes.txt")
    assert value.execute("symlink", payload, {"project": "ro"})["status"] == "denied"


@pytest.mark.parametrize(
    "path",
    ["package.json", "Makefile", ".envrc", ".github/workflows/build.yml", ".vscode/tasks.json"],
)
def test_executable_proposals_are_flagged(fs, path):
    value, root = fs
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("before")
    read = value.read(Tool(kind="read", root="project", path=path))
    proposal = Tool(
        kind="propose_replace",
        root="project",
        path=path,
        content="after",
        expected_version=read["version"],
    )
    assert value.execute("change", proposal.model_dump(), {"project": "rw"})["status"] == "proposed"
    result = Approvals(value).inspect("change")
    assert result["risk_warnings"] and "code" in result["risk_warnings"][0]


def test_unauthenticated_request_is_rejected_before_body_receive():
    reads = []
    output = []

    async def receive():
        reads.append(1)
        return {"type": "http.request", "body": b"x", "more_body": False}

    async def send(message):
        output.append(message)

    async def inner(*args):
        raise AssertionError("unauthenticated request reached application")

    app = Boundary(inner, tokens={"requester": "a", "responder": "b"})
    asyncio.run(
        app(
            {"type": "http", "scheme": "https", "headers": [], "client": ("127.0.0.1", 1)},
            receive,
            send,
        )
    )
    assert not reads
    assert output[0]["status"] == 401
    assert "PeerRelay" not in json.dumps(output, default=str)


def test_secret_shaped_answers_never_persist(tmp_path):
    from agent_relay.service import Relay

    root = tmp_path / "project"
    root.mkdir()
    relay = Relay(root, tmp_path / "state")
    try:
        mode = relay.call("requester", "requester_mode", {})["mode_id"]
        lease = relay.call("responder", "response_mode", {})["lease_id"]
        task = relay.call(
            "requester", "submit", dict(mode_id=mode, operation_id="one", prompt="work")
        )["task_id"]
        relay.call("responder", "claim", dict(lease_id=lease))
        q = relay.call(
            "responder",
            "ask_question",
            dict(lease_id=lease, task_id=task, operation_id="q", message="Paste token"),
        )
        secret = "ghp_" + "a" * 36
        with pytest.raises(ValueError, match="credential"):
            relay.call(
                "requester",
                "answer_question",
                dict(
                    mode_id=mode,
                    task_id=task,
                    question_id=q["question_id"],
                    action="accept",
                    answer=secret,
                ),
            )
        assert relay.questions.get(task, q["question_id"])["answer"] == ""
    finally:
        relay.close()


def test_health_auth_and_bounded_private_audit(tmp_path):
    from fastapi.testclient import TestClient

    from agent_relay.api import create_app
    from agent_relay.client import load
    from agent_relay.setup import initialize

    root = tmp_path / "project"
    root.mkdir()
    pair = tmp_path / "pair"
    initialize(root, pair, "127.0.0.1", 9443)
    app = create_app(load(pair / "server.json"))
    with TestClient(app, base_url="https://localhost") as client:
        for i in range(25):
            response = client.get("/healthz", headers={"Authorization": "Bearer NEVER LOG THIS"})
        assert response.status_code == 429
        token = (pair / "requester/requester.token").read_text()
        assert client.get("/healthz", headers={"Authorization": "Bearer " + token}).json() == {
            "status": "ready"
        }
        assert len(app.state.audit.entries) == 1
    log = pair / "state/auth-audit.jsonl"
    assert len(log.read_text().splitlines()) == 2
    assert "NEVER LOG" not in log.read_text() and token not in log.read_text()
    assert log.stat().st_mode & 0o077 == 0


def test_body_deadline_does_not_wait_for_client_forever():
    output = []

    async def receive():
        await asyncio.sleep(6)
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        output.append(message)

    async def inner(*args):
        raise AssertionError("timed out body reached app")

    app = Boundary(inner, tokens={"requester": "a", "responder": "b"})
    asyncio.run(
        app(
            dict(type="http", scheme="https", headers=[(b"authorization", b"Bearer a")]),
            receive,
            send,
        )
    )
    assert output[0]["status"] == 408
