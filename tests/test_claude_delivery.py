"""Channel boundary tests use real relay and MCP handlers, without a native account."""

import asyncio
from types import SimpleNamespace

import pytest
import test_follow
from mcp.types import ClientCapabilities
from test_background import Session
from test_follow import DirectClient

from agent_relay.mcp_server import build_server

relay = test_follow.relay


async def invoke(server, name, **arguments):
    return await server._tool_manager.get_tool(name).fn(**arguments)


def context():
    session = Session()
    session.client_params = SimpleNamespace(
        clientInfo=SimpleNamespace(name="claude-code", version="test"),
        capabilities=ClientCapabilities(),
    )
    return SimpleNamespace(session=session)


def test_probe_diagnostics_and_exit_keep_delivery_mode_scoped(relay):
    async def run():
        server = build_server(DirectClient(relay), background="claude-channel")
        server.watches.interval = 0.005
        ctx = context()
        mode = (await invoke(server, "requester_mode"))["mode_id"]
        other = (await invoke(server, "requester_mode"))["mode_id"]
        probe = await invoke(server, "probe_background", mode_id=mode, ctx=ctx)
        await asyncio.sleep(0.02)
        diagnostics = await invoke(server, "requester_diagnostics", mode_id=mode, ctx=ctx)
        assert diagnostics["background"]["delivery"] == "unverified"
        event = ctx.session.events[-1]["params"]["meta"]
        await invoke(
            server, "acknowledge_event", watch_id=probe["watch_id"], event_id=event["event_id"]
        )
        status = await invoke(server, "background_status", mode_id=mode, ctx=ctx)
        assert status["delivery"] == "verified"
        assert (await invoke(server, "background_status", mode_id=other, ctx=ctx))[
            "delivery"
        ] == "unverified"
        await invoke(server, "exit_requester_mode", mode_id=mode)
        assert not server.watches.jobs
        assert server.watches.status(ctx, mode)["delivery"] == "stopped"
        with pytest.raises(Exception):
            await invoke(server, "probe_background", mode_id=mode, ctx=ctx)
        await server.watches.close()

    asyncio.run(run())


def test_delayed_question_then_completion_only_wakes_once_each(relay):
    async def run():
        server = build_server(DirectClient(relay), background="claude-channel")
        server.watches.interval = 0.005
        ctx = context()
        mode = (await invoke(server, "requester_mode"))["mode_id"]
        lease = relay.call("responder", "response_mode", {})["lease_id"]
        result = await invoke(
            server,
            "submit_and_follow",
            mode_id=mode,
            operation_id="once",
            prompt="work",
            ctx=ctx,
            seconds=0,
        )
        task = result["task_id"]
        assert result["background"]["state"] == "watching"
        await asyncio.sleep(0.02)
        assert not ctx.session.events
        relay.call("responder", "claim", dict(lease_id=lease))
        question = relay.call(
            "responder",
            "ask_question",
            dict(
                lease_id=lease,
                task_id=task,
                operation_id="q",
                message="PRIVATE QUESTION",
                choices=[],
            ),
        )
        await asyncio.sleep(0.03)
        assert len(ctx.session.events) == 1
        event = ctx.session.events[0]["params"]["meta"]
        assert event["event_kind"] == "question_ready"
        assert "PRIVATE" not in str(ctx.session.events)
        await invoke(
            server, "acknowledge_event", watch_id=event["watch_id"], event_id=event["event_id"]
        )
        result = await invoke(server, "follow_task", mode_id=mode, task_id=task, ctx=ctx, seconds=0)
        assert result["follow_state"] == "needs_input"
        await invoke(server, "watch_task", mode_id=mode, task_id=task, ctx=ctx)
        await asyncio.sleep(0.02)
        assert len(ctx.session.events) == 1
        await invoke(
            server,
            "answer_question",
            mode_id=mode,
            task_id=task,
            question_id=question["question_id"],
            action="accept",
            answer="actual answer",
        )
        result = await invoke(server, "follow_task", mode_id=mode, task_id=task, ctx=ctx, seconds=0)
        assert result["follow_state"] == "waiting"
        relay.call(
            "responder",
            "complete",
            dict(lease_id=lease, task_id=task, summary="PRIVATE COMPLETION"),
        )
        await asyncio.sleep(0.03)
        assert len(ctx.session.events) == 2
        assert ctx.session.events[-1]["params"]["meta"]["event_kind"] == "task_finished"
        assert "PRIVATE" not in str(ctx.session.events)
        await invoke(server, "follow_task", mode_id=mode, task_id=task, ctx=ctx, seconds=0)
        await invoke(server, "watch_task", mode_id=mode, task_id=task, ctx=ctx)
        await asyncio.sleep(0.02)
        assert len(ctx.session.events) == 2
        await server.watches.close()

    asyncio.run(run())


@pytest.mark.parametrize("action", ["cancel_task", "exit_requester_mode"])
def test_cancel_and_exit_close_watches_before_next_event(relay, action):
    async def run():
        server = build_server(DirectClient(relay), background="claude-channel")
        server.watches.interval = 0.005
        ctx = context()
        mode = (await invoke(server, "requester_mode"))["mode_id"]
        relay.call("responder", "response_mode", {})
        task = await invoke(
            server,
            "submit_and_follow",
            mode_id=mode,
            operation_id="once",
            prompt="work",
            ctx=ctx,
            seconds=0,
        )
        arguments = dict(mode_id=mode)
        if action == "cancel_task":
            arguments["task_id"] = task["task_id"]
        await invoke(server, action, **arguments)
        await asyncio.sleep(0.02)
        assert not server.watches.jobs
        assert not ctx.session.events
        with pytest.raises(ValueError, match="stopped"):
            server.watches.start(ctx, mode_id=mode, task_id=task["task_id"])
        await server.watches.close()

    asyncio.run(run())


def test_foreground_question_is_not_echoed_by_manual_watch(relay):
    async def run():
        server = build_server(DirectClient(relay), background="claude-channel")
        server.watches.interval = 0.005
        ctx = context()
        mode = (await invoke(server, "requester_mode"))["mode_id"]
        lease = relay.call("responder", "response_mode", {})["lease_id"]
        task = (
            await invoke(server, "send_task", mode_id=mode, operation_id="once", prompt="work")
        )["task_id"]
        relay.call("responder", "claim", dict(lease_id=lease))
        relay.call(
            "responder",
            "ask_question",
            dict(lease_id=lease, task_id=task, operation_id="q", message="question", choices=[]),
        )
        result = await invoke(server, "follow_task", mode_id=mode, task_id=task, ctx=ctx, seconds=0)
        assert result["follow_state"] == "needs_input"
        await invoke(server, "watch_task", mode_id=mode, task_id=task, ctx=ctx)
        await asyncio.sleep(0.02)
        assert not ctx.session.events
        await server.watches.close()

    asyncio.run(run())


def test_watch_cannot_race_active_foreground_follow(relay):
    async def run():
        server = build_server(DirectClient(relay), background="claude-channel")
        ctx = context()
        mode = (await invoke(server, "requester_mode"))["mode_id"]
        relay.call("responder", "response_mode", {})
        task = (
            await invoke(server, "send_task", mode_id=mode, operation_id="once", prompt="work")
        )["task_id"]
        following = asyncio.create_task(
            invoke(server, "follow_task", mode_id=mode, task_id=task, ctx=ctx, seconds=5)
        )
        await asyncio.sleep(0.02)
        try:
            with pytest.raises(ValueError, match="follow"):
                await invoke(server, "watch_task", mode_id=mode, task_id=task, ctx=ctx)
        finally:
            following.cancel()
            await asyncio.gather(following, return_exceptions=True)
            await server.watches.close()

    asyncio.run(run())


def test_full_watch_capacity_preserves_accepted_task_and_resume(relay):
    async def run():
        server = build_server(DirectClient(relay), background="claude-channel")
        ctx = context()
        mode = (await invoke(server, "requester_mode"))["mode_id"]
        relay.call("responder", "response_mode", {})
        try:
            for i in range(8):
                result = await invoke(
                    server,
                    "submit_and_follow",
                    mode_id=mode,
                    operation_id=str(i),
                    prompt="work",
                    ctx=ctx,
                    seconds=0,
                )
                assert result["background"]["state"] == "watching"
            result = await invoke(
                server,
                "submit_and_follow",
                mode_id=mode,
                operation_id="ninth",
                prompt="work",
                ctx=ctx,
                seconds=0,
            )
            assert result["follow_state"] == "waiting"
            assert result["resume"] == {"mode_id": mode, "task_id": result["task_id"]}
            assert result["background"]["state"] == "unavailable"
            assert result["background"]["reason"] == "watch_unavailable"
            assert len(server.watches.jobs) == 8
        finally:
            await server.watches.close()

    asyncio.run(run())
