"""Shutdown must fence requests that were authorized before their bodies arrived."""

import http.client
import json
import socket
import threading
import time
from urllib.parse import urlsplit

import pytest

from agent_relay.bootstrap import Setups
from agent_relay.setup import initialize


@pytest.mark.parametrize("action", ["pair", "start_relay"])
def test_delayed_authorized_http_body_cannot_start_resources_after_shutdown(
    tmp_path, monkeypatch, action
):
    setups = Setups(tmp_path / "setups")
    receipt = setups.begin("requester", "lifecycle-chat-123456")
    session = setups.session(receipt["setup_id"], "lifecycle-chat-123456")
    project = tmp_path / "project"
    project.mkdir()
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1] - 1
    settings = {"project": str(project), "host": "127.0.0.1", "port": port, "advertise": False}
    if action == "start_relay":
        initialize(project, session.directory / "pair", "127.0.0.1", port)
        session.reuse(str(session.directory / "pair/requester/client.json"))
        settings = {}

    # Observe the real authorization boundary without bypassing any HTTP validation.
    authorized = threading.Event()
    handler = setups.http.RequestHandlerClass
    original = handler.authorize

    def observe_authorization(self, mutation=False):
        result = original(self, mutation)
        if mutation and result is session:
            authorized.set()
        return result

    monkeypatch.setattr(handler, "authorize", observe_authorization)
    url = urlsplit(receipt["setup_url"])
    connection = http.client.HTTPConnection(url.hostname, url.port, timeout=10)
    body = json.dumps({"action": action, "settings": settings}).encode()
    closed = False
    try:
        connection.putrequest("POST", url.path)
        connection.putheader("Origin", setups.url)
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(len(body)))
        connection.endheaders()
        assert authorized.wait(3), "Request never reached authorization"
        setups.close()
        closed = True
        connection.send(body)
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 400
        assert "closed" in payload["error"].lower()
        assert session.worker is None
        assert session.relay_process is None
        if action == "pair":
            assert not (session.directory / "pair").exists()
    finally:
        connection.close()
        if not closed:
            setups.close()
        # Clean up any late resources if the regression returns.
        if session.worker and session.worker.is_alive():
            session.cancel()
            session.worker.join(timeout=10)
        if session.relay_process and session.relay_process.poll() is None:
            session.relay_process.terminate()
            session.relay_process.wait(timeout=10)


@pytest.mark.parametrize("direct_session", [False, True])
def test_concurrent_close_stops_pairing_without_holding_worker_lock(tmp_path, direct_session):
    setups = Setups(tmp_path / "setups")
    receipt = setups.begin("requester", "lifecycle-chat-123456")
    session = setups.session(receipt["setup_id"], "lifecycle-chat-123456")
    project = tmp_path / "project"
    project.mkdir()
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1] - 1
    session.start_pairing(
        {"project": str(project), "host": "127.0.0.1", "port": port, "advertise": False}
    )
    deadline = time.monotonic() + 5
    while "code" not in session.view and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "code" in session.view, session.record
    errors = []
    gate = threading.Barrier(3)

    def close():
        gate.wait()
        try:
            (session.close if direct_session else setups.close)()
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=close) for _ in range(2)]
    for thread in threads:
        thread.start()
    gate.wait()
    for thread in threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in threads), "Close deadlocked with a worker"
    assert not errors
    assert not session.worker.is_alive()
    setups.close()
    with pytest.raises(ValueError, match="closed"):
        session.start_pairing({})
    with pytest.raises(ValueError, match="closed"):
        setups.begin("responder", "another-closed-chat-123456")
    # Closing is idempotent, and the owner lock is released once the worker exits.
    setups.close()
    session.close()
    with Setups(tmp_path / "setups") as resumed:
        assert resumed.begin("requester", "lifecycle-chat-123456")["state"] == "cancelled"
