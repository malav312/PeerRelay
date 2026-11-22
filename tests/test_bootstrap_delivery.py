"""Installed-project defaults and bound channel delivery through the bootstrap."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import test_mcp
from test_background import Session as NotificationSession

from agent_relay import bootstrap
from agent_relay.setup import initialize


def test_installed_project_prefills_view_without_granting_access(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    with bootstrap.Setups(tmp_path / "state", project=project) as setups:
        receipt = setups.begin("requester", "requester-chat-123456")
        assert receipt["background_delivery"] == "manual_follow"
        with httpx.Client(trust_env=False) as browser:
            view = browser.get(receipt["setup_url"] + "/state").json()
        assert view["project_default"] == str(project)
        assert receipt["state"] == "choose_pairing"
        assert not (setups.root / receipt["setup_id"] / "pair").exists()


def test_bootstrap_channel_opt_in_propagates_binding_and_cleanup(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    pair = tmp_path / "pair"
    initialize(project, pair, "127.0.0.1", 9443)

    async def run():
        setups = bootstrap.Setups(tmp_path / "state", background="claude-channel", project=project)
        try:
            server = bootstrap.build_server(setups)
            assert server.initialization_options().capabilities.experimental == {
                "claude/channel": {}
            }
            binding = "requester-chat-123456"
            receipt = setups.begin("requester", binding)
            session = setups.session(receipt["setup_id"], binding)
            session.reuse(pair / "requester/client.json")
            bound = session.role_server()
            assert bound.watches.enabled
            ctx = SimpleNamespace(session=NotificationSession())
            bound.watches.interval = 0.001
            watch = bound.watches.probe(ctx, "mode-test")
            await asyncio.sleep(0.01)
            event = ctx.session.events[0]["params"]["meta"]
            assert event["setup_id"] == receipt["setup_id"]
            assert event["binding_id"] == binding
            assert event["watch_id"] == watch["watch_id"]
            assert "probe" not in json.dumps(receipt)
            await setups.aclose()
            assert not bound.watches.jobs
            count = len(ctx.session.events)
            await asyncio.sleep(0.01)
            assert len(ctx.session.events) == count
        finally:
            await setups.aclose()

    asyncio.run(run())


def test_bootstrap_rejects_private_state_inside_installed_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(ValueError, match="separate|outside"):
        bootstrap.Setups(project / "state", project=project)
    assert not (project / "state").exists()


# Use the actual SDK stdio transport and real TLS relay, not a direct function call.
transport_pair = test_mcp.live_pair


def test_installed_stdio_channel_dispatch_receives_ack_in_same_binding(transport_pair, tmp_path):
    import sys
    from pathlib import Path

    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import Implementation
    from test_mcp import decode

    pair, project, _ = transport_pair
    parameters = StdioServerParameters(
        command=sys.executable,
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        args=[
            "-m",
            "agent_relay.cli",
            "bootstrap",
            "--state-dir",
            str(tmp_path / "installed"),
            "--project",
            str(project),
            "--background",
            "claude-channel",
        ],
    )

    async def run():
        events = asyncio.Queue()
        async with stdio_client(parameters) as (read, write):
            send, receive = anyio.create_memory_object_stream(20)

            async def route():
                async for message in read:
                    payload = message.message.root
                    if getattr(payload, "method", "") == "notifications/claude/channel":
                        await events.put(payload.params)
                    else:
                        await send.send(message)

            async with anyio.create_task_group() as group:
                group.start_soon(route)
                async with ClientSession(
                    receive, write, client_info=Implementation(name="claude-code", version="test")
                ) as client:
                    initialized = await client.initialize()
                    assert initialized.capabilities.experimental == {"claude/channel": {}}
                    binding = "requester-native-123456"
                    receipt = decode(
                        await client.call_tool(
                            "setup_begin",
                            {
                                "role": "requester",
                                "binding_id": binding,
                            },
                        )
                    )
                    url = receipt["setup_url"]
                    async with httpx.AsyncClient(trust_env=False) as browser:
                        response = await browser.post(
                            url,
                            headers={"Origin": url.rsplit("/", 1)[0]},
                            json={
                                "action": "reuse",
                                "settings": {"config": str(pair / "requester/client.json")},
                            },
                        )
                        assert response.status_code == 200

                    async def call(tool, arguments, selected=receipt["setup_id"]):
                        return decode(
                            await client.call_tool(
                                "role_call",
                                {
                                    "setup_id": selected,
                                    "binding_id": binding,
                                    "tool": tool,
                                    "arguments": arguments,
                                },
                            )
                        )

                    mode = (await call("requester_mode", {}))["mode_id"]
                    await call("probe_background", {"mode_id": mode})
                    event = await asyncio.wait_for(events.get(), 4)
                    meta = event["meta"]
                    assert meta["setup_id"] == receipt["setup_id"]
                    assert meta["binding_id"] == binding
                    await call(
                        "acknowledge_event",
                        {
                            "watch_id": meta["watch_id"],
                            "event_id": meta["event_id"],
                        },
                    )
                    status = await call("background_status", {"mode_id": mode})
                    assert status["delivery"] == "verified"
                    await call("exit_requester_mode", {"mode_id": mode})
                    assert (await call("background_status", {"mode_id": mode}))[
                        "delivery"
                    ] == "stopped"
                group.cancel_scope.cancel()

    asyncio.run(run())


def test_shutdown_fences_new_roles_before_waiting_for_watches(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    initialize(project, tmp_path / "pair", "127.0.0.1", 9443)

    async def run():
        setups = bootstrap.Setups(tmp_path / "state", background="claude-channel")
        receipt = setups.begin("requester", "requester-native-123456")
        session = setups.session(receipt["setup_id"], "requester-native-123456")
        session.reuse(tmp_path / "pair/requester/client.json")
        bound = session.role_server()
        started, finish = asyncio.Event(), asyncio.Event()

        async def close():
            started.set()
            await finish.wait()

        monkeypatch.setattr(bound.watches, "close", close)
        pending = asyncio.create_task(setups.aclose())
        await started.wait()
        try:
            with pytest.raises(ValueError, match="closed|closing"):
                setups.begin("responder", "responder-native-123456")
            with pytest.raises(ValueError, match="closed|closing"):
                session.role_server()
        finally:
            finish.set()
            await pending

    asyncio.run(run())
