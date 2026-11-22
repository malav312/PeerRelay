import asyncio
import json
from urllib.request import Request, urlopen

import pytest
from test_changes import finish

from agent_relay.mcp_server import build_server
from agent_relay.service import Relay


class LocalClient:
    role = "requester"

    def __init__(self, relay):
        self.relay = relay
        self.calls = []

    def call(self, method, arguments):
        self.calls.append(method)
        return self.relay.call(self.role, method, arguments)


@pytest.fixture
def finished(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    relay = Relay(root, tmp_path / "state", grant="exec")
    mode = relay.call("requester", "requester_mode", {"execution": True})["mode_id"]
    lease = relay.call("responder", "response_mode", {})["lease_id"]
    task = relay.call(
        "requester",
        "submit",
        {
            "mode_id": mode,
            "operation_id": "first",
            "prompt": "Edit files",
        },
    )["task_id"]
    relay.call("responder", "claim", {"lease_id": lease})
    (root / "new.txt").write_text("<script>alert('diff sentinel')</script>\n")
    finish(relay, lease, task)
    client = LocalClient(relay)
    server = build_server(client)
    yield relay, client, server, mode, task
    if hasattr(server, "changes_viewer"):
        server.changes_viewer.close()
    relay.close()


def invoke(server, tool, mode, task):
    result = asyncio.run(server.call_tool(tool, {"mode_id": mode, "task_id": task}))
    return json.loads("".join(item.text for item in result if item.type == "text"))


def test_status_delivers_browser_link_without_fetching_diff(finished):
    _, client, server, mode, task = finished
    result = invoke(server, "task_status", mode, task)
    assert "view_url" in result["changes"]
    assert "diff sentinel" not in json.dumps(result)
    assert client.calls == ["status"]
    with urlopen(result["changes"]["view_url"]) as response:
        html = response.read().decode()
        assert response.headers["Cache-Control"] == "no-store"
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]
    assert "diff sentinel" in html and "<details>" in html
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert client.calls == ["status", "changes"]


def test_view_link_ownership_host_origin_exit_and_expiry(finished):
    relay, _, server, mode, task = finished
    result = invoke(server, "view_changes", mode, task)
    url = result["changes"]["view_url"]
    for headers in ({"Host": "evil.example"}, {"Origin": "https://evil.example"}):
        with pytest.raises(Exception) as error:
            urlopen(Request(url, headers=headers))
        assert error.value.code == 403
    with pytest.raises(Exception) as error:
        urlopen(url + "invalid")
    assert error.value.code == 404
    relay.call("requester", "exit_mode", {"mode_id": mode})
    with pytest.raises(Exception) as error:
        urlopen(url)
    assert error.value.code == 404


def test_only_explicit_analysis_tool_exposes_patch(finished):
    _, client, server, mode, task = finished
    view = invoke(server, "view_changes", mode, task)
    assert "diff sentinel" not in json.dumps(view)
    assert client.calls == ["status"]
    analysis = invoke(server, "get_changes_for_analysis", mode, task)
    assert "diff sentinel" in json.dumps(analysis)
    assert client.calls == ["status", "changes"]


def test_expired_link_and_shutdown_do_not_serve_artifact(finished):
    _, _, server, mode, task = finished
    clock = [100.0]
    server.changes_viewer.clock = lambda: clock[0]
    url = invoke(server, "view_changes", mode, task)["changes"]["view_url"]
    clock[0] += 3601
    with pytest.raises(Exception) as error:
        urlopen(url)
    assert error.value.code == 404
    renewed = invoke(server, "view_changes", mode, task)["changes"]["view_url"]
    assert renewed != url
    with urlopen(renewed) as response:
        assert response.status == 200
    server.changes_viewer.close()
    with pytest.raises(Exception):
        urlopen(renewed, timeout=1)


def test_browser_cannot_apply_or_write(finished):
    _, _, server, mode, task = finished
    url = invoke(server, "view_changes", mode, task)["changes"]["view_url"]
    with pytest.raises(Exception) as error:
        urlopen(Request(url, method="POST", data=b"undo"))
    assert error.value.code == 501
