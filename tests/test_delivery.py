import asyncio
import json

import pytest


class Native:
    def __init__(self):
        self.calls = []
        self.status = "idle"
        self.turns = []
        self.error = None
        self.before_start = None

    async def call(self, method, params):
        self.calls.append((method, params))
        if method == "thread/read":
            return {
                "thread": {
                    "id": "thread",
                    "status": {"type": self.status},
                    "turns": list(self.turns),
                }
            }
        assert method == "turn/start"
        if self.before_start:
            self.before_start()
        if self.error:
            raise self.error
        turn = {"id": f"turn-{len(self.turns)}", "status": "inProgress"}
        self.turns.append(turn)
        self.status = "active"
        return {"turn": turn}


def make(tmp_path, role="responder"):
    from agent_relay.delivery import Binding, DeliveryController

    binding = (
        Binding(role, "generation", "thread", lease_id="lease")
        if role == "responder"
        else (Binding(role, "generation", "thread", mode_id="mode"))
    )
    native = Native()
    controller = DeliveryController.create(tmp_path / "delivery.sqlite", binding, native)
    return controller, native


def event(kind="work_available", marker="task_available", task="task"):
    from agent_relay.delivery import Event

    return Event(kind, task, marker)


def test_binding_requires_one_exact_role_authority():
    from agent_relay.delivery import Binding

    for kwargs in ({}, {"mode_id": "mode"}, {"mode_id": "mode", "lease_id": "lease"}):
        with pytest.raises(ValueError):
            Binding("responder", "generation", "thread", **kwargs)
    with pytest.raises(ValueError):
        Binding("requester", "generation", "thread", mode_id="mode\nattack")


def test_journal_before_send_and_dedup_after_completion(tmp_path):
    async def run():
        controller, native = make(tmp_path)
        try:
            first = controller.offer(event())
            assert controller.offer(event()) == first
            native.before_start = lambda: assert_submitting(controller, first)
            assert await controller.dispatch()
            starts = [p for m, p in native.calls if m == "turn/start"]
            assert len(starts) == 1
            assert starts[0]["threadId"] == "thread"
            assert "lease" in starts[0]["input"][0]["text"]
            native.turns[0]["status"] = "completed"
            native.status = "idle"
            await controller.reconcile()
            assert controller.records()[0]["state"] == "completed"
            controller.offer(event())
            assert not await controller.dispatch()
            assert len([1 for m, _ in native.calls if m == "turn/start"]) == 1
        finally:
            controller.close()

    asyncio.run(run())


def assert_submitting(controller, ident):
    row = controller.records()[0]
    assert row["event_id"] == ident and row["state"] == "submitting"


def test_busy_thread_queues_and_only_matching_completion_releases_it(tmp_path):
    async def run():
        controller, native = make(tmp_path)
        try:
            controller.offer(event())
            native.status = "active"
            assert not await controller.dispatch()
            native.status = "idle"
            assert await controller.dispatch()
            controller.offer(event("answer_ready", "reply:q"))
            controller.observe(
                "generation",
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "other",
                        "turn": {"id": "turn-0", "status": "completed"},
                    },
                },
            )
            controller.observe(
                "old-generation",
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread",
                        "turn": {"id": "turn-0", "status": "completed"},
                    },
                },
            )
            assert not await controller.dispatch()
            native.turns[0]["status"] = "completed"
            native.status = "idle"
            assert await controller.dispatch()
            assert len(native.turns) == 2
        finally:
            controller.close()

    asyncio.run(run())


def test_uncertain_start_is_durable_and_never_replayed(tmp_path):
    from agent_relay.delivery import DeliveryController

    async def run():
        controller, native = make(tmp_path)
        binding = controller.binding
        native.error = TimeoutError("lost response")
        controller.offer(event())
        assert not await controller.dispatch()
        assert controller.state == "stopped"
        assert controller.records()[0]["state"] == "uncertain"
        native.error = None
        native.turns = [{"id": "could-be-human", "status": "completed"}]
        await controller.reconcile()
        assert not await controller.dispatch()
        assert len([1 for m, _ in native.calls if m == "turn/start"]) == 1
        controller.close()
        with pytest.raises(FileExistsError):
            DeliveryController.create(tmp_path / "delivery.sqlite", binding, native)
        assert (
            DeliveryController.inspect(tmp_path / "delivery.sqlite")["events"][0]["state"]
            == "uncertain"
        )

    asyncio.run(run())


def test_revocation_discards_pending_without_a_disconnect_model_turn(tmp_path):
    async def run():
        controller, native = make(tmp_path)
        try:
            controller.offer(event())
            controller.stop("lease_revoked")
            assert not await controller.dispatch()
            assert native.calls == []
            assert controller.records()[0]["state"] == "discarded"
            with pytest.raises(RuntimeError):
                controller.offer(event(task="another"))
        finally:
            controller.close()

    asyncio.run(run())


def test_requester_events_exclude_peer_content_and_stale_questions(tmp_path):
    from agent_relay.delivery import RelayMonitor

    class Relay:
        role = "requester"
        question = True
        calls = []

        def call(self, method, params):
            self.calls.append(method)
            if method == "mode_status":
                return {"workspace_matches": True, "responder_connected": True}
            return {
                "state": "running",
                "questions": [{"question_id": "q", "message": "UNTRUSTED SECRET"}]
                if self.question
                else [],
            }

    async def run():
        controller, native = make(tmp_path, "requester")
        relay = Relay()
        monitor = RelayMonitor(controller, relay)
        monitor.watch_task("task")
        try:
            await monitor.poll()
            assert len(controller.records()) == 1
            assert "UNTRUSTED" not in json.dumps(controller.records())
            relay.question = False
            await monitor.poll()
            assert not await controller.dispatch()
            assert controller.records()[0]["state"] == "discarded"
            assert "heartbeat" not in relay.calls
        finally:
            controller.close()

    asyncio.run(run())


def test_responder_waits_in_code_and_renews_only_bound_lease(tmp_path):
    from agent_relay.delivery import RelayMonitor

    class Relay:
        role = "responder"
        calls = []
        available = False
        revoked = False

        def call(self, method, params):
            self.calls.append((method, params))
            assert params == {"lease_id": "lease"}
            if self.revoked:
                raise RuntimeError("revoked")
            if method == "heartbeat":
                return {"state": "available"}
            return (
                {"state": "queued", "event": "task_available", "task_id": "task"}
                if (self.available)
                else {"state": "idle"}
            )

    async def run():
        controller, native = make(tmp_path)
        relay = Relay()
        now = [0]
        monitor = RelayMonitor(controller, relay, clock=lambda: now[0])
        try:
            for _ in range(20):
                await monitor.poll()
            assert native.calls == []
            assert [m for m, _ in relay.calls].count("heartbeat") == 1
            now[0] = 16
            relay.available = True
            await monitor.poll()
            assert await controller.dispatch()
            assert [m for m, _ in relay.calls].count("heartbeat") == 2
            relay.revoked = True
            await monitor.poll()
            assert controller.state == "stopped"
            assert controller.reason == "relay_unavailable"
        finally:
            controller.close()

    asyncio.run(run())


def test_cancellation_during_start_is_uncertain_not_retriable(tmp_path):
    async def run():
        controller, native = make(tmp_path)
        native.error = asyncio.CancelledError()
        controller.offer(event())
        try:
            with pytest.raises(asyncio.CancelledError):
                await controller.dispatch()
            assert controller.reason == "uncertain_start"
            assert controller.records()[0]["state"] == "uncertain"
        finally:
            controller.close()

    asyncio.run(run())


def test_idle_status_does_not_finish_an_unresolved_turn(tmp_path):
    async def run():
        controller, native = make(tmp_path)
        try:
            controller.offer(event())
            await controller.dispatch()
            controller.offer(event("answer_ready", "reply:q"))
            native.status = "idle"
            native.turns = []
            controller.observe(
                "generation",
                {
                    "method": "thread/status/changed",
                    "params": {"threadId": "thread", "status": {"type": "idle"}},
                },
            )
            assert not await controller.dispatch()
            assert controller.records()[0]["state"] == "started"
        finally:
            controller.close()

    asyncio.run(run())


def test_executable_demo_uses_real_relay_and_no_native_provider():
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "scripts/automatic_delivery_demo.py"],
        text=True,
        capture_output=True,
        env=os.environ,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report == {
        "idle_turns": 0,
        "responder_turns": 2,
        "requester_turns": 2,
        "question_answer": "custom separator",
        "task_state": "succeeded",
        "after_disconnect": "stopped",
        "provider_calls": 0,
    }


def test_event_withdrawn_during_native_status_read_is_not_delivered(tmp_path):
    async def run():
        controller, native = make(tmp_path)
        reading, release = asyncio.Event(), asyncio.Event()
        original_call = native.call

        async def delayed_read(method, params):
            if method == "thread/read":
                reading.set()
                await release.wait()
            return await original_call(method, params)

        native.call = delayed_read
        controller.offer(event())
        task = asyncio.create_task(controller.dispatch())
        try:
            await reading.wait()
            controller.replace_pending([])
            release.set()
            assert not await task
            assert controller.records()[0]["state"] == "discarded"
            assert not native.turns
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            controller.close()

    asyncio.run(run())


def test_answer_wake_names_an_available_mcp_tool_and_exact_question(tmp_path):
    from agent_relay.mcp_server import build_server

    async def run():
        controller, native = make(tmp_path)
        try:
            controller.offer(event("answer_ready", "reply:question-42"))
            assert await controller.dispatch()
            params = next(params for method, params in native.calls if method == "turn/start")
            text = params["input"][0]["text"]
            metadata = json.loads(text.split("Control metadata: ", 1)[1])
            assert metadata["question_id"] == "question-42"
            names = {
                tool.name
                for tool in await build_server(
                    type("Client", (), {"role": "responder"})()
                ).list_tools()
            }
            assert "wait_for_answer" in names and "Use wait_for_answer " in text
            assert "get_reply" not in text
        finally:
            controller.close()

    asyncio.run(run())
