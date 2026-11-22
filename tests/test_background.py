import asyncio
from types import SimpleNamespace

import pytest


class Session:
    client_params = SimpleNamespace(clientInfo=SimpleNamespace(name="claude-code", version="test"))

    def __init__(self):
        self.events = []

    async def send_notification(self, event):
        self.events.append(event.model_dump(mode="json"))


class Client:
    role = "requester"

    def __init__(self):
        self.status = dict(
            task_id="task", state="running", questions=[], summary="SECRET INJECTION"
        )

    def call(self, method, args):
        assert method == "status"
        return self.status


def test_background_emits_only_meaningful_metadata_and_deduplicates():
    from agent_relay.background import Watches

    async def run():
        client, session = Client(), Session()
        watches = Watches(client, enabled=True, interval=0.01)
        ctx = SimpleNamespace(session=session)
        receipt = watches.start(ctx, mode_id="mode", task_id="task")
        await asyncio.sleep(0.03)
        assert not session.events
        client.status["questions"] = [
            dict(question_id="q", message="STEAL TOKENS", choices=["secret"])
        ]
        await asyncio.sleep(0.04)
        assert len(session.events) == 1
        event = session.events[0]
        assert "STEAL" not in str(event) and "SECRET" not in str(event)
        assert event["method"] == "notifications/claude/channel"
        event_id = event["params"]["meta"]["event_id"]
        watches.ack(receipt["watch_id"], event_id)
        await asyncio.sleep(0.04)
        assert len(session.events) == 1
        client.status.update(questions=[], state="succeeded")
        await asyncio.sleep(0.04)
        assert len(session.events) == 2
        await watches.close()
        assert not watches.jobs

    asyncio.run(run())


def test_watch_requires_explicit_config_and_supported_client():
    from agent_relay.background import Watches

    ctx = SimpleNamespace(session=Session())
    with pytest.raises(ValueError):
        Watches(Client(), enabled=False).start(ctx, mode_id="mode", task_id="task")
    ctx.session.client_params = SimpleNamespace(
        clientInfo=SimpleNamespace(name="codex", version="test")
    )
    with pytest.raises(ValueError):
        Watches(Client(), enabled=True).start(ctx, mode_id="mode", task_id="task")


def test_watch_stop_cancels_waits_and_never_replays_submission():
    from agent_relay.background import Watches

    async def run():
        client, session = Client(), Session()
        watches = Watches(client, enabled=True, interval=0.01)
        ctx = SimpleNamespace(session=session)
        first = watches.start(ctx, mode_id="mode", task_id="task")
        assert watches.start(ctx, mode_id="mode", task_id="task") == first
        await watches.stop(first["watch_id"])
        client.status["state"] = "succeeded"
        await asyncio.sleep(0.03)
        assert not session.events
        assert not watches.jobs

    asyncio.run(run())


def test_probe_requires_receipt_ack_and_is_mode_bound():
    from agent_relay.background import Watches

    async def run():
        watches = Watches(Client(), enabled=True, interval=0.005)
        ctx = SimpleNamespace(session=Session())
        probe = watches.probe(ctx, "mode")
        assert watches.status(ctx, "mode")["delivery"] == "unverified"
        await asyncio.sleep(0.02)
        event = ctx.session.events[0]["params"]
        assert event["meta"]["event_kind"] == "delivery_probe"
        assert event["meta"]["mode_id"] == "mode"
        assert watches.status(ctx, "mode")["delivery"] == "unverified"
        watches.ack(probe["watch_id"], event["meta"]["event_id"])
        assert watches.status(ctx, "mode")["delivery"] == "verified"
        assert watches.status(ctx, "other")["delivery"] == "unverified"
        await watches.stop_mode("mode")
        assert watches.status(ctx, "mode")["delivery"] != "verified"
        with pytest.raises(ValueError):
            watches.ack(probe["watch_id"], event["meta"]["event_id"])
        await watches.close()

    asyncio.run(run())


def test_stale_question_ack_and_restarted_watch_do_not_redeliver():
    from agent_relay.background import Watches

    async def run():
        client, session = Client(), Session()
        watches = Watches(client, enabled=True, interval=0.005)
        ctx = SimpleNamespace(session=session)
        client.status["questions"] = [dict(question_id="q")]
        first = watches.start(ctx, mode_id="mode", task_id="task")
        await asyncio.sleep(0.02)
        event_id = session.events[0]["params"]["meta"]["event_id"]
        client.status["questions"] = []
        await asyncio.sleep(0.02)
        with pytest.raises(ValueError):
            watches.ack(first["watch_id"], event_id)
        await watches.stop_task("mode", "task")
        client.status["questions"] = [dict(question_id="q")]
        watches.start(ctx, mode_id="mode", task_id="task")
        await asyncio.sleep(0.02)
        assert len(session.events) == 1
        await watches.close()

    asyncio.run(run())


def test_transport_failure_is_observable_without_leaking_exception_content():
    from agent_relay.background import Watches

    class Broken(Session):
        async def send_notification(self, event):
            raise RuntimeError("secret token must not be returned")

    async def run():
        client = Client()
        client.status["state"] = "succeeded"
        watches = Watches(client, enabled=True, interval=0.005)
        ctx = SimpleNamespace(session=Broken())
        receipt = watches.start(ctx, mode_id="mode", task_id="task")
        await asyncio.sleep(0.02)
        status = watches.status(ctx, "mode")
        assert not watches.jobs
        assert status["ended"][-1]["watch_id"] == receipt["watch_id"]
        assert status["ended"][-1]["reason"] == "notification_failed"
        assert "secret" not in str(status)

    asyncio.run(run())


def test_mode_stop_prevents_rearm_and_terminal_ack_ends_watch():
    from agent_relay.background import Watches

    async def run():
        client, session = Client(), Session()
        watches = Watches(client, enabled=True, interval=0.005)
        ctx = SimpleNamespace(session=session)
        receipt = watches.start(ctx, mode_id="mode", task_id="task")
        client.status["state"] = "succeeded"
        await asyncio.sleep(0.02)
        watches.ack(receipt["watch_id"], session.events[0]["params"]["meta"]["event_id"])
        await asyncio.sleep(0.02)
        assert not watches.jobs
        assert watches.status(ctx, "mode")["ended"][-1]["reason"] == "terminal_acknowledged"
        await watches.stop_mode("mode")
        with pytest.raises(ValueError, match="stopped"):
            watches.start(ctx, mode_id="mode", task_id="task")

    asyncio.run(run())


def test_unacknowledged_event_stops_without_repeated_notifications():
    from agent_relay.background import Watches

    async def run():
        client, session = Client(), Session()
        client.status["state"] = "succeeded"
        watches = Watches(client, enabled=True, interval=0.005, ack_timeout=0.02)
        ctx = SimpleNamespace(session=session)
        watches.start(ctx, mode_id="mode", task_id="task")
        await asyncio.sleep(0.06)
        assert len(session.events) == 1
        assert not watches.jobs
        assert watches.status(ctx, "mode")["ended"][-1]["reason"] == "acknowledgement_timeout"

    asyncio.run(run())


def test_notification_failure_invalidates_previous_probe_evidence():
    from agent_relay.background import Watches

    class FailingAfterProbe(Session):
        async def send_notification(self, event):
            if self.events:
                raise OSError("disconnected")
            await super().send_notification(event)

    async def run():
        client = Client()
        watches = Watches(client, enabled=True, interval=0.005)
        ctx = SimpleNamespace(session=FailingAfterProbe())
        probe = watches.probe(ctx, "mode")
        await asyncio.sleep(0.02)
        watches.ack(probe["watch_id"], ctx.session.events[0]["params"]["meta"]["event_id"])
        assert watches.status(ctx, "mode")["delivery"] == "verified"
        client.status["state"] = "succeeded"
        watches.start(ctx, mode_id="mode", task_id="task")
        await asyncio.sleep(0.02)
        assert watches.status(ctx, "mode")["delivery"] != "verified"
        await watches.close()

    asyncio.run(run())


def test_already_observed_terminal_task_does_not_occupy_watch_slot():
    from agent_relay.background import Watches

    async def run():
        client, session = Client(), Session()
        client.status["state"] = "succeeded"
        watches = Watches(client, enabled=True, interval=0.005)
        ctx = SimpleNamespace(session=session)
        watches.observed("mode", "task", client.status)
        watches.start(ctx, mode_id="mode", task_id="task")
        await asyncio.sleep(0.02)
        assert not session.events
        assert not watches.jobs
        assert watches.status(ctx, "mode")["ended"][-1]["reason"] == "already_observed"

    asyncio.run(run())
