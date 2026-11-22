"""Pairing must retain the security guarantees of the main relay."""

import asyncio
import hashlib
import json

import pytest

from agent_relay.api import Boundary
from agent_relay.pairing_cli import pair_host
from agent_relay.setup import initialize_host


def test_spoken_exec_defaults_to_container(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    path = initialize_host(root, tmp_path / "pair", "127.0.0.1", 9443, grant="exec")
    config = json.loads(path.read_text())
    assert config["execution_backend"] == {
        "kind": "docker",
        "image": "python:3.13-slim",
        "allow_host_credentials": False,
    }


def test_spoken_host_exec_needs_explicit_credential_acknowledgement(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    folder = tmp_path / "pair"
    with pytest.raises(ValueError, match="acknowledgement"):
        initialize_host(root, folder, "127.0.0.1", 9443, grant="exec", execution_backend="host")
    assert not folder.exists()


def test_repair_cannot_change_backend(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    folder = tmp_path / "pair"
    initialize_host(root, folder, "127.0.0.1", 9443, grant="exec")
    before = (folder / "server.json").read_bytes()
    with pytest.raises(ValueError, match="backend"):
        pair_host(
            root,
            folder,
            execution_backend="host",
            allow_host_credentials=True,
            advertise=False,
            out=lambda s: None,
        )
    assert (folder / "server.json").read_bytes() == before


@pytest.mark.parametrize(
    "headers,allowed",
    [
        ([(b"authorization", b"Bearer bob-token")], True),
        ([(b"authorization", b"Bearer alice-token")], True),
        ([(b"authorization", b"bob-token")], False),
        ([(b"authorization", b"Bearer bad")], False),
        ([(b"authorization", b"Bearer bob-token")] * 2, False),
    ],
)
def test_digest_auth_still_precedes_body(headers, allowed):
    reads, calls, output = [], [], []

    async def receive():
        reads.append(True)
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        output.append(message)

    async def inner(scope, receive, send):
        calls.append(scope["peerrelay_role"])

    app = Boundary(
        inner,
        digests={
            r: hashlib.sha256(t.encode()).hexdigest()
            for r, t in [("requester", "alice-token"), ("responder", "bob-token")]
        },
    )
    asyncio.run(app(dict(type="http", scheme="https", headers=headers), receive, send))
    assert bool(reads) is allowed
    assert bool(calls) is allowed
    if not allowed:
        assert output[0]["status"] == 401


def test_repair_holds_configured_state_lock_until_confirmation(tmp_path):
    import socket
    import threading

    from test_pairing import CODE

    from agent_relay.service import Relay
    from agent_relay.setup import initialize

    root = tmp_path / "project"
    root.mkdir()
    folder = tmp_path / "pair"
    initialize(root, folder, "127.0.0.1", 9443)
    path = folder / "server.json"
    raw = json.loads(path.read_text())
    raw["state_dir"] = "custom-state"
    path.write_text(json.dumps(raw))
    offered, finish = threading.Event(), threading.Event()
    errors = []

    def output(line):
        if "Waiting for a device" in line:
            offered.set()
            assert finish.wait(10)

    with socket.create_server(("127.0.0.1", 0)) as listener:

        def pair():
            try:
                pair_host(
                    root,
                    folder,
                    host="127.0.0.1",
                    advertise=False,
                    code=CODE,
                    listener=listener,
                    out=output,
                    window=0,
                )
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=pair)
        thread.start()
        try:
            assert offered.wait(5)
            # A relay starting during the confirmation window must never load old tokens.
            try:
                relay = Relay(root, folder / "custom-state", grant="ro")
            except BlockingIOError:
                pass
            else:
                relay.close()
                pytest.fail("Relay started while re-pairing was still in progress")
        finally:
            finish.set()
            thread.join(10)
    assert errors and "expired" in str(errors[0])
    # Cancellation/expiry releases the guard for a later normal startup.
    relay = Relay(root, folder / "custom-state", grant="ro")
    relay.close()


def test_server_rejects_config_cached_before_repair(tmp_path):
    from fastapi.testclient import TestClient

    from agent_relay.api import create_app
    from agent_relay.client import load
    from agent_relay.setup import initialize

    root = tmp_path / "project"
    root.mkdir()
    folder = tmp_path / "pair"
    initialize(root, folder, "127.0.0.1", 9443)
    path = folder / "server.json"
    stale = create_app(load(path))
    raw = json.loads(path.read_text())
    raw["tokens"].pop("responder")
    raw["token_sha256"] = {"responder": hashlib.sha256(b"new-responder").hexdigest()}
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="configuration changed"):
        with TestClient(stale, base_url="https://x"):
            pass
    with TestClient(create_app(load(path)), base_url="https://x") as client:
        assert (
            client.get("/healthz", headers={"Authorization": "Bearer new-responder"}).status_code
            == 200
        )
