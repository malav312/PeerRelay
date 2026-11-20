import json
import shutil

import pytest
from fastapi.testclient import TestClient

from agent_relay.api import create_app
from agent_relay.client import Client, load
from agent_relay.setup import initialize


def test_setup_private_portable_credentials_and_role_api(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    directory = tmp_path / "pair"
    initialize(root, directory, "127.0.0.1", 9443)
    server = load(directory / "server.json")
    a = load(directory / "requester/client.json")
    b = load(directory / "responder/client.json")
    assert a["role"] == "requester" and b["role"] == "responder"
    assert a["token_file"] != b["token_file"]
    for path in directory.rglob("*"):
        if path.is_file():
            assert path.stat().st_mode & 0o077 == 0
    with TestClient(create_app(server), base_url="https://localhost") as client:
        assert (
            client.post("/rpc", json={"method": "requester_mode", "arguments": {}}).status_code
            == 401
        )
        token = (directory / "responder/responder.token").read_text()
        response = client.post(
            "/rpc",
            headers={"Authorization": "Bearer " + token},
            json={"method": "requester_mode", "arguments": {}},
        )
        assert response.status_code == 403
        assert (
            client.post(
                "/rpc",
                headers={"Authorization": "Bearer " + token},
                json={"method": "response_mode", "arguments": {}},
            ).status_code
            == 200
        )
    # Moving B's three-file bundle does not retain absolute paths into A's machine.
    config = json.loads((directory / "responder/client.json").read_text())
    assert config["token_file"] == "responder.token" and config["ca_file"] == "gateway.crt"


def test_unsafe_setup_and_client_config_rejected(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    with pytest.raises(ValueError):
        initialize(root, root / "credentials", "127.0.0.1", 9443)
    directory = tmp_path / "pair"
    initialize(root, directory, "127.0.0.1", 9443)
    config = load(directory / "requester/client.json")
    with pytest.raises(ValueError):
        Client(dict(config, url="http://127.0.0.1:9443"))
    with pytest.raises(ValueError):
        Client(dict(config, url="https://example.com/leak"))
    (directory / "requester/client.json").chmod(0o644)
    with pytest.raises(ValueError):
        load(directory / "requester/client.json")


@pytest.mark.parametrize("field", ["requester", "responder", "key_file", "_path"])
def test_server_rejects_private_files_inside_project(tmp_path, field):
    root = tmp_path / "project"
    root.mkdir()
    directory = tmp_path / "pair"
    initialize(root, directory, "127.0.0.1", 9443)
    config = load(directory / "server.json")
    mapping = config["tokens"] if field in ("requester", "responder") else config
    exposed = root / "private-file"
    shutil.copyfile(mapping[field], exposed)
    exposed.chmod(0o600)
    mapping[field] = str(exposed)
    with pytest.raises(ValueError, match="overlap"):
        with TestClient(create_app(config), base_url="https://localhost"):
            pass
