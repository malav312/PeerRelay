import json
import socket
import threading

import pytest
from fastapi.testclient import TestClient

from agent_relay import pairing
from agent_relay.api import create_app
from agent_relay.client import Client, load
from agent_relay.pairing_cli import pair_host, pair_join

CODE = "7-orbit-lantern"


class Host:
    """Runs Alice's side on loopback in a thread (no mDNS)."""

    def __init__(self, tmp_path, allow=True, grant="exec", window=20):
        self.project = tmp_path / "project"
        self.project.mkdir(exist_ok=True)
        self.pair_dir = tmp_path / "pair"
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.address = self.listener.getsockname()
        self.lines, self.error, self.peer = [], None, None
        self.allow, self.grant, self.window = allow, grant, window

    def run(self):
        try:
            self.peer = pair_host(
                self.project,
                self.pair_dir,
                grant=self.grant,
                host="127.0.0.1",
                advertise=False,
                code=CODE,
                listener=self.listener,
                ask=lambda prompt: "y" if self.allow else "n",
                out=self.lines.append,
                window=self.window,
            )
        except Exception as error:
            self.error = error

    def __enter__(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.thread.join(timeout=30)
        self.listener.close()


def join(tmp_path, address, code=CODE, answer="y", folder="bob"):
    lines = []
    offer = pair_join(
        tmp_path / folder,
        code=code,
        address=address,
        name="Bob's ThinkPad",
        ask=lambda prompt: answer,
        out=lines.append,
    )
    return offer, lines


def test_codes_parse_normalize_and_reject():
    assert pairing.parse_code(" 07 Orbit  LANTERN ") == (7, CODE)
    for bad in ("", "7-orbit", "1-orbit-lantern", "100-orbit-lantern", "7-orbit-notaword"):
        with pytest.raises(ValueError):
            pairing.parse_code(bad)
    channel, code = pairing.parse_code(pairing.new_code(taken=range(2, 99)))
    assert channel == 99 and code.startswith("99-")


def test_spoken_code_pairing_end_to_end(tmp_path):
    with Host(tmp_path) as host:
        offer, lines = join(tmp_path, host.address)
    assert host.error is None
    assert offer["grant"] == "exec" and offer["host_name"]
    # Both people saw the same check number.
    host_check = next(line for line in host.lines if "Check number" in line)
    bob_check = next(line for line in lines if "Check number" in line)
    assert host_check.split(":")[1].split("←")[0] == bob_check.split(":")[1].split("←")[0]

    bob = tmp_path / "bob"
    token = (bob / "responder.token").read_text()
    server = json.loads((host.pair_dir / "server.json").read_text())
    # Alice's disk holds only a digest of Bob's self-generated credential.
    assert "responder" not in server["tokens"]
    assert token not in (host.pair_dir / "server.json").read_text()
    assert not any(token in p.read_text() for p in host.pair_dir.rglob("*") if p.is_file())
    assert server["responder"]["name"] == "Bob's ThinkPad"
    for path in [*bob.rglob("*"), *host.pair_dir.rglob("*")]:
        if path.is_file():
            assert path.stat().st_mode & 0o077 == 0

    config = load(bob / "client.json")
    assert config["pin"] is True and config["role"] == "responder"
    Client(config).http.close()
    with TestClient(create_app(load(host.pair_dir / "server.json")), base_url="https://x") as c:
        auth = {"Authorization": "Bearer " + token}
        assert (
            c.post(
                "/rpc", headers=auth, json={"method": "response_mode", "arguments": {}}
            ).status_code
            == 200
        )
        assert (
            c.post(
                "/rpc", headers=auth, json={"method": "requester_mode", "arguments": {}}
            ).status_code
            == 403
        )
        assert (
            c.post(
                "/rpc",
                headers={"Authorization": token},
                json={"method": "response_mode", "arguments": {}},
            ).status_code
            == 401
        )


def test_wrong_code_burns_attempts_and_saves_nothing(tmp_path):
    with Host(tmp_path) as host:
        for _ in range(pairing.ATTEMPTS):
            with pytest.raises(pairing.WrongCode):
                join(tmp_path, host.address, code="7-orbit-lemon")
    assert isinstance(host.error, pairing.PairingFailed)
    assert "Too many wrong codes" in str(host.error)
    assert sum("wrong code" in line for line in host.lines) == pairing.ATTEMPTS
    assert not (tmp_path / "bob").exists()
    assert "token_sha256" not in json.loads((host.pair_dir / "server.json").read_text())


def test_either_person_can_decline(tmp_path):
    with Host(tmp_path) as host:
        with pytest.raises(pairing.PairingFailed, match="You declined"):
            join(tmp_path, host.address, answer="n")
    assert "declined" in str(host.error)
    assert not (tmp_path / "bob").exists()

    with Host(tmp_path, allow=False) as host:
        with pytest.raises(pairing.PairingFailed, match="other person declined"):
            join(tmp_path, host.address, folder="bob2")
    assert not (tmp_path / "bob2").exists()
    assert "token_sha256" not in json.loads((host.pair_dir / "server.json").read_text())


def test_repairing_replaces_responder_and_keeps_grant(tmp_path):
    with Host(tmp_path) as host:
        join(tmp_path, host.address)
    first = (tmp_path / "bob" / "responder.token").read_text()
    with Host(tmp_path, grant=None) as host:
        join(tmp_path, host.address, folder="bob-new")
    assert host.error is None
    second = (tmp_path / "bob-new" / "responder.token").read_text()
    app = create_app(load(host.pair_dir / "server.json"))
    with TestClient(app, base_url="https://x") as c:
        call = {"method": "response_mode", "arguments": {}}
        assert (
            c.post("/rpc", headers={"Authorization": "Bearer " + first}, json=call).status_code
            == 401
        )
        assert (
            c.post("/rpc", headers={"Authorization": "Bearer " + second}, json=call).status_code
            == 200
        )
    with pytest.raises(ValueError, match="different access level"):
        pair_host(host.project, host.pair_dir, grant="ro", advertise=False, out=lambda s: None)


def test_server_refuses_to_start_without_a_responder(tmp_path):
    from agent_relay.setup import initialize_host

    (tmp_path / "project").mkdir()
    path = initialize_host(tmp_path / "project", tmp_path / "pair", "127.0.0.1", 9443)
    with pytest.raises(ValueError, match="No responder"):
        create_app(load(path))


def test_hostile_peer_text_and_frames_are_rejected():
    for bad in ("\x1b[2Jowned", "", "x" * 65, "tab\there"):
        with pytest.raises(pairing.PairingFailed):
            pairing.display_name(bad)
    a, b = socket.socketpair()
    with a, b:
        a.sendall((pairing.MAX_FRAME + 1).to_bytes(4, "big"))
        with pytest.raises(pairing.PairingFailed, match="too large"):
            pairing.Channel(b).recv()
    with pytest.raises(pairing.PairingFailed):
        pairing.validate_offer(
            {
                "type": "offer",
                "v": 1,
                "host_name": "A",
                "project": "p",
                "grant": "root",
                "user": "alice",
                "relay_port": 9443,
                "certificate": "",
            }
        )


def test_tampered_encrypted_message_fails_authentication():
    a, b = socket.socketpair()
    with a, b:
        sender, receiver = pairing.Channel(a), pairing.Channel(b)
        sender.secure(b"k" * 32, b"j" * 32)
        receiver.secure(b"j" * 32, b"x" * 32)  # Wrong key: as if a relay rewrote it.
        sender.send_json({"ok": True})
        with pytest.raises(pairing.PairingFailed, match="authentication"):
            receiver.recv_json()


def test_wrong_code_is_reported_once(tmp_path):
    lines = []
    with Host(tmp_path, window=2) as host:
        with pytest.raises(pairing.WrongCode):
            pair_join(
                tmp_path / "bob",
                code="7-orbit-lemon",
                address=host.address,
                ask=lambda prompt: "y",
                out=lines.append,
            )
    # The CLI prints the raised error; pair_join itself must not print it too.
    assert not any("Wrong code" in line for line in lines)


def test_symlinked_parent_folders_are_resolved(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    from agent_relay.pairing_cli import canonical_folder

    assert canonical_folder(tmp_path / "link" / "pair") == real.resolve() / "pair"
    with pytest.raises(ValueError, match="itself must not be a symlink"):
        canonical_folder(tmp_path / "link")
