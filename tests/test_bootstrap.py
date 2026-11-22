"""Bootstrap binds a chat before credentials exist; all state here is disposable."""

import asyncio
import json
from pathlib import Path

import pytest
import test_mcp

from agent_relay import bootstrap
from agent_relay.setup import initialize


def test_bootstrap_starts_without_bundle_and_reuses_only_matching_chat(tmp_path):
    with bootstrap.Setups(tmp_path / "setups") as setups:
        first = setups.begin("requester", "chat-requester-123456")
        assert first["state"] == "choose_pairing"
        assert first["role"] == "requester"
        assert first["setup_url"].startswith("http://127.0.0.1:")
        assert setups.begin("requester", "chat-requester-123456")["setup_id"] == first["setup_id"]
        second = setups.begin("responder", "chat-responder-123456")
        assert second["setup_id"] != first["setup_id"]
        with pytest.raises(ValueError, match="binding"):
            setups.status(first["setup_id"], "chat-responder-123456")
        with pytest.raises(ValueError, match="role"):
            setups.begin("responder", "chat-requester-123456")
        with pytest.raises(ValueError, match="ready"):
            setups.role_server(first["setup_id"], "chat-requester-123456")


def test_existing_bundle_is_bound_without_reinitializing_or_copying_credentials(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    pair = tmp_path / "existing"
    initialize(project, pair, "127.0.0.1", 9443)
    before = {str(p.relative_to(pair)): p.read_bytes() for p in pair.rglob("*") if p.is_file()}
    with bootstrap.Setups(tmp_path / "setups") as setups:
        receipt = setups.begin("responder", "chat-responder-123456")
        session = setups.session(receipt["setup_id"], "chat-responder-123456")
        with pytest.raises(ValueError, match="role"):
            session.reuse(str(pair / "requester/client.json"))
        session.reuse(str(pair / "responder/client.json"))
        ready = setups.status(receipt["setup_id"], "chat-responder-123456")
        assert ready["state"] == "paired"
        assert ready["relay"] == "not_checked"
        role_server = setups.role_server(receipt["setup_id"], "chat-responder-123456")
        names = {tool.name for tool in asyncio.run(role_server.list_tools())}
        assert "remote_read" in names and "send_task" not in names
        assert not any("token" in p.name for p in (tmp_path / "setups").rglob("*"))
    assert {
        str(p.relative_to(pair)): p.read_bytes() for p in pair.rglob("*") if p.is_file()
    } == before
    with bootstrap.Setups(tmp_path / "setups") as setups:
        resumed = setups.begin("responder", "chat-responder-123456")
        assert resumed["setup_id"] == receipt["setup_id"]
        assert resumed["state"] == "paired"


def test_mcp_bootstrap_advertises_both_entries_without_credentials(tmp_path):
    with bootstrap.Setups(tmp_path / "setups") as setups:
        server = bootstrap.build_server(setups)
        tools = {t.name for t in asyncio.run(server.list_tools())}
        prompts = {p.name for p in asyncio.run(server.list_prompts())}
        assert {"setup_begin", "setup_status", "setup_cancel", "role_tools", "role_call"} <= tools
        assert prompts == {"requester-mode", "response-mode"}
        result = asyncio.run(
            server.call_tool(
                "setup_begin",
                {
                    "role": "requester",
                    "binding_id": "chat-requester-123456",
                },
            )
        )
        receipt = json.loads(result[0].text)
        assert receipt["state"] == "choose_pairing"
        assert not (Path(setups.root) / receipt["setup_id"] / "pair").exists()


def test_local_view_rejects_cross_origin_and_never_returns_code_to_mcp(tmp_path):
    import httpx

    with bootstrap.Setups(tmp_path / "setups") as setups:
        receipt = setups.begin("requester", "chat-requester-123456")
        url = receipt["setup_url"]
        session = setups.session(receipt["setup_id"], "chat-requester-123456")
        session.view["code"] = "7-orbit-lantern"
        with httpx.Client(trust_env=False) as browser:
            page = browser.get(url)
            assert page.status_code == 200
            assert page.headers["referrer-policy"] == "no-referrer"
            assert browser.get(url + "/state").json()["view"]["code"] == "7-orbit-lantern"
            assert (
                browser.get(url + "/state", headers={"Origin": "https://evil.test"}).status_code
                == 403
            )
            assert browser.get(url, headers={"Host": "evil.test"}).status_code == 403
            assert browser.post(url, json={"action": "cancel"}).status_code == 403
            assert (
                browser.post(
                    url, headers={"Origin": setups.url}, json={"action": "cancel"}
                ).status_code
                == 200
            )
            assert browser.get(setups.url + "/wrong").status_code == 404
        assert "orbit" not in json.dumps(
            setups.status(receipt["setup_id"], "chat-requester-123456")
        )
        assert session.record["state"] == "cancelled"


def wait_state(session, expected, seconds=8):
    import time

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if session.record["state"] in expected:
            return session.record["state"]
        time.sleep(0.02)
    pytest.fail(f"Expected {expected}, got {session.record}")


@pytest.mark.parametrize("auto_start,startup_fails", [(False, False), (True, False), (True, True)])
def test_view_pairs_real_crypto_and_starts_one_relay_without_replacing_backend(
    tmp_path, monkeypatch, auto_start, startup_fails
):
    import socket
    import time

    project = tmp_path / "project"
    project.mkdir()
    with bootstrap.Setups(tmp_path / "a") as a, bootstrap.Setups(tmp_path / "b") as b:
        ar = a.begin("requester", "chat-requester-123456")
        br = b.begin("responder", "chat-responder-123456")
        alice = a.session(ar["setup_id"], "chat-requester-123456")
        bob = b.session(br["setup_id"], "chat-responder-123456")
        if startup_fails:
            original_start = alice.start_relay
            def fail_first_start():
                monkeypatch.setattr(alice, "start_relay", original_start)
                raise ValueError("Temporary startup failure")
            monkeypatch.setattr(alice, "start_relay", fail_first_start)
        # Reserve adjacent loopback ports for the relay and pairing window.
        for _ in range(100):
            with socket.socket() as first:
                first.bind(("127.0.0.1", 0))
                port = first.getsockname()[1]
                try:
                    with socket.socket() as second:
                        second.bind(("127.0.0.1", port + 1))
                    break
                except OSError:
                    continue
        alice.start_pairing(
            {
                "project": str(project),
                "grant": "rw",
                "start_relay": auto_start,
                "backend": "docker",
                "host": "127.0.0.1",
                "port": port,
                "advertise": False,
            }
        )
        deadline = time.monotonic() + 5
        while "code" not in alice.view and time.monotonic() < deadline:
            time.sleep(0.02)
        assert "code" in alice.view, alice.record
        bob.start_pairing({"code": alice.view["code"], "address": f"127.0.0.1:{port + 1}"})
        wait_state(bob, {"confirming"})
        assert bob.view["confirmation"]["execution_backend"] == "docker"
        bob.confirm(True)
        with pytest.raises(ValueError, match="pending"):
            bob.confirm(False)
        wait_state(alice, {"confirming"})
        assert alice.view["confirmation"]["check"] == bob.view["confirmation"]["check"]
        alice.confirm(True)
        wait_state(alice, {"paired"})
        wait_state(bob, {"paired"})
        assert alice.record["settings"]["backend"] == "docker"
        alice.worker.join(timeout=10)
        assert alice.record["relay"] == (
            "unavailable" if startup_fails else "ready" if auto_start else "not_started"
        )
        if startup_fails:
            assert alice.record["state"] == "paired"
            assert "Pairing saved" in alice.record["error"]
        assert bob.record["relay"] == "not_checked"
        assert "code" not in alice.view
        with pytest.raises(ValueError, match="already"):
            alice.start_pairing({"project": str(project), "backend": "host"})
        alice.start_relay()
        process = alice.relay_process
        alice.start_relay()
        assert alice.relay_process is process
        assert alice.record["relay"] == "ready"
        assert "error" not in alice.record
        assert bob.check_relay()["relay"] == "ready"
        with pytest.raises(ValueError, match="saved"):
            alice.cancel()

        # Exercise the bound role API through the stable MCP, using the real relay.
        async def call(setups, receipt, binding, tool, arguments):
            result = await bootstrap.build_server(setups).call_tool(
                "role_call",
                {
                    "setup_id": receipt["setup_id"],
                    "binding_id": binding,
                    "tool": tool,
                    "arguments": arguments,
                },
            )
            return json.loads(result[0].text)

        from agent_relay.client import Client, load

        with_client = Client(load(bob.record["config"]))
        connected = with_client.call("response_mode", {})
        assert connected["lease_id"]
        active = asyncio.run(call(a, ar, "chat-requester-123456", "requester_mode", {}))
        assert active["mode_id"]
        with_client.close()


def test_bootstrap_prepare_packages_both_skills_without_a_pair(tmp_path):
    from agent_relay.client_setup import prepare_bootstrap

    result = prepare_bootstrap("codex", tmp_path / "state", tmp_path / "chat")
    assert result["role"] == "unbound"
    mcp = json.loads((tmp_path / "chat/mcp.json").read_text())
    assert mcp["mcpServers"]["peerrelay"]["args"][-3:] == [
        "bootstrap",
        "--state-dir",
        str(tmp_path / "state"),
    ]
    for mode in ("requester-mode", "response-mode"):
        assert (tmp_path / f"chat/.agents/skills/{mode}/SKILL.md").is_file()
    assert not (tmp_path / "state").exists()
    with pytest.raises(ValueError, match="exists"):
        prepare_bootstrap("codex", tmp_path / "state", tmp_path / "chat")


def test_pending_pair_can_cancel_and_retry_without_backend_or_project_drift(tmp_path):
    import socket
    import time

    project = tmp_path / "project"
    project.mkdir()
    with bootstrap.Setups(tmp_path / "setups") as setups:
        receipt = setups.begin("requester", "chat-requester-123456")
        session = setups.session(receipt["setup_id"], "chat-requester-123456")
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1] - 1
        session.start_pairing(
            {
                "project": str(project),
                "host": "127.0.0.1",
                "port": port,
                "advertise": False,
                "backend": "docker",
                "grant": "exec",
            }
        )
        deadline = time.monotonic() + 5
        while "code" not in session.view and time.monotonic() < deadline:
            time.sleep(0.02)
        assert "code" in session.view, session.record
        cert = (session.directory / "pair/gateway.crt").read_bytes()
        session.cancel()
        wait_state(session, {"cancelled"})
        session.worker.join(timeout=2)
        with pytest.raises(ValueError, match="pinned"):
            session.start_pairing({"project": str(project), "backend": "host"})
        session.start_pairing({})
        assert session.record["settings"]["backend"] == "docker"
        session.cancel()
        wait_state(session, {"cancelled"})
        assert (session.directory / "pair/gateway.crt").read_bytes() == cert


def test_another_process_cannot_take_over_a_live_setup(tmp_path):
    with (
        bootstrap.Setups(tmp_path / "setups") as first,
        bootstrap.Setups(tmp_path / "setups") as other,
    ):
        first.begin("requester", "chat-requester-123456")
        with pytest.raises(ValueError, match="another MCP process"):
            other.begin("requester", "chat-requester-123456")
        assert other.begin("responder", "chat-responder-123456")["state"] == "choose_pairing"


def test_reuse_rejects_symlink_bundle(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    initialize(project, tmp_path / "pair", "127.0.0.1", 9443)
    link = tmp_path / "link.json"
    link.symlink_to(tmp_path / "pair/requester/client.json")
    with bootstrap.Setups(tmp_path / "setups") as setups:
        receipt = setups.begin("requester", "chat-requester-123456")
        session = setups.session(receipt["setup_id"], "chat-requester-123456")
        with pytest.raises(ValueError, match="private regular"):
            session.reuse(str(link))


transport_pair = test_mcp.live_pair


def test_sdk_bootstrap_dispatch_preserves_context_forms_and_role_boundary(transport_pair, tmp_path):
    import sys

    import httpx
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import ElicitResult
    from test_mcp import decode

    pair, _, _ = transport_pair

    def params(role):
        return StdioServerParameters(
            command=sys.executable,
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
            args=["-m", "agent_relay.cli", "bootstrap", "--state-dir", str(tmp_path / role)],
        )

    async def run():
        questions = []

        async def answer(ctx, request):
            questions.append(request.message)
            return ElicitResult(action="accept", content={"answer": "Custom — 多字"})

        async with (
            stdio_client(params("a")) as (ar, aw),
            ClientSession(ar, aw, elicitation_callback=answer) as a,
            stdio_client(params("b")) as (br, bw),
            ClientSession(br, bw) as b,
        ):
            await a.initialize()
            await b.initialize()
            bindings = {}
            async with httpx.AsyncClient(trust_env=False) as browser:
                for client, role in ((a, "requester"), (b, "responder")):
                    binding = f"chat-{role}-123456"
                    receipt = decode(
                        await client.call_tool("setup_begin", {"role": role, "binding_id": binding})
                    )
                    bindings[role] = {"setup_id": receipt["setup_id"], "binding_id": binding}
                    url = receipt["setup_url"]
                    response = await browser.post(
                        url,
                        headers={"Origin": url.rsplit("/", 1)[0]},
                        json={
                            "action": "reuse",
                            "settings": {"config": str(pair / role / "client.json")},
                        },
                    )
                    assert response.status_code == 200, response.text
                    tools = decode(await client.call_tool("role_tools", bindings[role]))
                    assert any(
                        t["name"] == f"{role if role == 'requester' else 'response'}_mode"
                        for t in tools["tools"]
                    )

            async def call(client, role, tool, arguments):
                return decode(
                    await client.call_tool(
                        "role_call", {**bindings[role], "tool": tool, "arguments": arguments}
                    )
                )

            lease = (await call(b, "responder", "response_mode", {}))["lease_id"]
            mode = (await call(a, "requester", "requester_mode", {}))["mode_id"]
            pending = asyncio.create_task(
                call(
                    a,
                    "requester",
                    "submit_and_follow",
                    {
                        "mode_id": mode,
                        "operation_id": "first",
                        "prompt": "Choose a separator",
                        "seconds": 8,
                    },
                )
            )
            claimed = await call(b, "responder", "wait_for_task", {"lease_id": lease, "seconds": 5})
            common = {"lease_id": lease, "task_id": claimed["task_id"]}
            question = await call(
                b,
                "responder",
                "ask_requester",
                {
                    **common,
                    "operation_id": "question",
                    "message": "Which separator?",
                    "choices": ["One", "Many"],
                },
            )
            reply = await call(
                b,
                "responder",
                "wait_for_answer",
                {**common, "question_id": question["question_id"], "seconds": 5},
            )
            assert reply["answer"] == "Custom — 多字"
            await call(b, "responder", "complete_task", {**common, "summary": "Done."})
            assert (await pending)["state"] == "succeeded"
            assert questions == ["Which separator?\n\n1. One\n2. Many"]
            forbidden = await a.call_tool(
                "role_call", {**bindings["requester"], "tool": "remote_read", "arguments": {}}
            )
            assert forbidden.isError
            await call(b, "responder", "disconnect", {"lease_id": lease})

    asyncio.run(run())


def test_restarted_pair_does_not_report_stale_relay_readiness(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    initialize(project, tmp_path / "pair", "127.0.0.1", 9443)
    with bootstrap.Setups(tmp_path / "setups") as setups:
        receipt = setups.begin("requester", "chat-requester-123456")
        session = setups.session(receipt["setup_id"], "chat-requester-123456")
        session.reuse(str(tmp_path / "pair/requester/client.json"))
        session.record["relay"] = "ready"
        session.save()
    with bootstrap.Setups(tmp_path / "setups") as resumed:
        receipt = resumed.begin("requester", "chat-requester-123456")
        assert receipt["state"] == "paired"
        assert receipt["relay"] == "not_checked"


def test_cancelled_setup_can_deliberately_reuse_an_existing_pair(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    initialize(project, tmp_path / "pair", "127.0.0.1", 9443)
    with bootstrap.Setups(tmp_path / "setups") as setups:
        receipt = setups.begin("requester", "chat-requester-123456")
        session = setups.session(receipt["setup_id"], "chat-requester-123456")
        session.cancel()
        session.reuse(str(tmp_path / "pair/requester/client.json"))
        assert session.record["state"] == "paired"


def test_bound_role_refuses_a_bundle_whose_role_changed(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    initialize(project, tmp_path / "pair", "127.0.0.1", 9443)
    config = tmp_path / "pair/requester/client.json"
    with bootstrap.Setups(tmp_path / "setups") as setups:
        receipt = setups.begin("requester", "chat-requester-123456")
        session = setups.session(receipt["setup_id"], "chat-requester-123456")
        session.reuse(str(config))
        changed = json.loads(config.read_text())
        changed["role"] = "responder"
        config.write_text(json.dumps(changed))
        with pytest.raises(ValueError, match="role changed"):
            session.role_server()


def test_reused_bundle_cannot_start_an_earlier_setup_relay(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    initialize(project, tmp_path / "external", "127.0.0.1", 9443)
    with bootstrap.Setups(tmp_path / "setups") as setups:
        receipt = setups.begin("requester", "chat-requester-123456")
        session = setups.session(receipt["setup_id"], "chat-requester-123456")
        # A failed earlier attempt can leave local server configuration behind.
        from agent_relay.setup import initialize_host

        initialize_host(project, session.directory / "pair", "127.0.0.1", 9444)
        session.reuse(str(tmp_path / "external/requester/client.json"))
        with pytest.raises(ValueError, match="selected bundle"):
            session.start_relay()
