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
