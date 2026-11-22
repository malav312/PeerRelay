import asyncio
import json

import pytest

from agent_relay.delivery import Binding, DeliveryController, RelayMonitor


class Relay:
    role = "requester"

    def __init__(self):
        self.calls = []
        self.state = "running"
        self.questions = []

    def call(self, method, args):
        self.calls.append((method, args))
        if method == "mode_status":
            return {"workspace_matches": True, "responder_connected": True}
        if method == "submit":
            return {"task_id": "task", "state": "queued"}
        if method == "status":
            return {
                "task_id": "task",
                "state": self.state,
                "summary": "",
                "questions": self.questions,
                "commands": [],
                "proposals": [],
            }
        if method == "answer_question":
            self.questions = []
            return {"state": "answered"}
        if method == "exit_mode":
            return {"state": "exited"}
        raise AssertionError(method)


class Native:
    def __init__(self):
        self.turns = []

    async def call(self, method, args):
        if method == "thread/read":
            return {"thread": {"id": "thread", "status": {"type": "idle"}, "turns": self.turns}}
        assert method == "turn/start"
        turn = {"id": f"turn-{len(self.turns)}", "status": "completed"}
        self.turns.append(turn)
        return {"turn": turn}


def test_requester_bridge_automatic_watch_question_answer_and_completion(tmp_path):
    from agent_relay.codex_requester import RequesterBridge

    async def run():
        relay, native = Relay(), Native()
        binding = Binding("requester", "generation", "thread", mode_id="mode")
        controller = DeliveryController.create(tmp_path / "delivery.sqlite", binding, native)
        monitor = RelayMonitor(controller, relay)
        bridge = RequesterBridge(relay)
        schemas = await bridge.schemas()
        names = {tool["name"] for tool in schemas}
        assert {"requester_session", "submit_and_follow", "follow_task", "answer_question"} <= names
        assert not names & {"requester_mode", "watch_task", "probe_background"}
        bridge.bind(binding, monitor)

        async def call(tool, **args):
            result = await bridge.handle({"threadId": "thread", "tool": tool, "arguments": args})
            assert result["success"], result
            return json.loads(result["contentItems"][0]["text"])

        assert (await call("requester_session"))["mode_id"] == "mode"
        await call(
            "submit_and_follow", mode_id="mode", operation_id="op", prompt="work", seconds=50
        )
        assert monitor.tasks == {"task"}
        for _ in range(3):
            await monitor.poll()
            await controller.dispatch()
        assert native.turns == []
        relay.questions = [{"question_id": "q", "message": "Filename?", "choices": []}]
        await monitor.poll()
        await controller.dispatch()
        assert len(native.turns) == 1
        response = await call("follow_task", mode_id="mode", task_id="task", seconds=50)
        assert response["questions"][0]["message"] == "Filename?"
        await monitor.poll()
        await controller.dispatch()
        assert len(native.turns) == 1
        await call(
            "answer_question",
            mode_id="mode",
            task_id="task",
            question_id="q",
            action="accept",
            answer="custom.txt",
        )
        await monitor.poll()
        relay.state = "succeeded"
        await monitor.poll()
        await controller.dispatch()
        assert len(native.turns) == 2
        result = await call("follow_task", mode_id="mode", task_id="task")
        assert result["display"] == "Done."
        assert monitor.tasks == set()
        assert len([c for c in relay.calls if c[0] == "submit"]) == 1
        await call("exit_requester_mode", mode_id="mode")
        assert controller.state == "stopped"
        assert not (
            await bridge.handle(
                {"threadId": "thread", "tool": "requester_session", "arguments": {}}
            )
        )["success"]
        await bridge.close()
        controller.close()

    asyncio.run(run())


def test_bridge_rejects_foreign_authority_before_any_remote_calls(tmp_path):
    from agent_relay.codex_requester import RequesterBridge

    async def run():
        relay = Relay()
        binding = Binding("requester", "g", "thread", mode_id="mode")
        controller = DeliveryController.create(tmp_path / "journal", binding, Native())
        bridge = RequesterBridge(relay)
        await bridge.schemas()
        bridge.bind(binding, RelayMonitor(controller, relay))
        for change in (
            {"threadId": "other"},
            {"arguments": {"mode_id": "other"}},
            {"tool": "requester_mode"},
            {"namespace": "other"},
        ):
            result = await bridge.handle(
                {
                    "threadId": "thread",
                    "tool": "task_status",
                    "arguments": {"mode_id": "mode", "task_id": "task"},
                    **change,
                }
            )
            assert result["success"] is False
        assert relay.calls == []
        await bridge.close()
        controller.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", [None, "terminal", "activation", "delivery"])
def test_launcher_owns_requester_mode_and_revokes_it(tmp_path, monkeypatch, failure):
    from agent_relay import codex_requester as module

    events = []

    class Client(Relay):
        def __init__(self, config):
            super().__init__()

        def call(self, method, args):
            events.append((method, args))
            if method == "requester_mode":
                assert args == {"execution": True}
                if failure == "activation":
                    raise RuntimeError("lost connection")
                return {"mode_id": "mode"}
            return super().call(method, args)

        def close(self):
            events.append(("closed", {}))

    class Session:
        thread_id, generation, rpc = "thread", "g", Native()
        start_result = {"approvalPolicy": "never", "sandbox": {"type": "readOnly"}}

        @classmethod
        async def start(cls, directory, **kwargs):
            assert kwargs["terminal_command"] == ("codex",)
            return cls()

        async def launch_terminal(self):
            if failure == "terminal":
                raise RuntimeError("terminal stopped")

        async def run_delivery(self, controller, monitor):
            assert controller.binding.mode_id == "mode"
            if failure == "delivery":
                raise RuntimeError("transport lost")

        async def close(self):
            pass

    monkeypatch.setattr(module, "load", lambda p: {"role": "requester"})
    monkeypatch.setattr(module, "Client", Client)
    monkeypatch.setattr(module, "preflight", lambda cwd: ("codex",))
    monkeypatch.setattr(module, "OwnedCodexSession", Session)

    async def run():
        if failure:
            with pytest.raises(RuntimeError):
                await module.run_requester("config", tmp_path / "new", execution=True)
        else:
            await module.run_requester("config", tmp_path / "new", execution=True)

    asyncio.run(run())
    exits = [c for c in events if c[0] == "exit_mode"]
    assert exits == (
        [] if failure in ("terminal", "activation") else [("exit_mode", {"mode_id": "mode"})]
    )


# Real pinned TLS relay; no user's pairing or provider account.
from test_mcp import live_pair  # noqa: E402,F401


def test_requester_bridge_real_relay_questions_followup_and_revoke(live_pair, tmp_path):  # noqa: F811
    from agent_relay.client import Client, load
    from agent_relay.codex_requester import RequesterBridge

    pair, _, _ = live_pair
    alice = Client(load(pair / "requester/client.json"))
    bob = Client(load(pair / "responder/client.json"))

    async def run():
        lease = await asyncio.to_thread(bob.call, "response_mode", {})
        mode = await asyncio.to_thread(alice.call, "requester_mode", {})
        binding = Binding("requester", "g", "thread", mode_id=mode["mode_id"])
        native = Native()
        controller = DeliveryController.create(tmp_path / "delivery", binding, native)
        monitor = RelayMonitor(controller, alice)
        bridge = RequesterBridge(alice)
        await bridge.schemas()
        bridge.bind(binding, monitor)

        async def call(tool, **extra):
            result = await bridge.handle(
                {
                    "threadId": "thread",
                    "tool": tool,
                    "arguments": {"mode_id": mode["mode_id"], **extra},
                }
            )
            assert result["success"], result
            return json.loads(result["contentItems"][0]["text"])

        try:
            for index in range(2):
                result = await call("submit_and_follow", operation_id=f"op-{index}", prompt="work")
                task = result["task_id"]
                claimed = await bob.wait_for_task(lease["lease_id"], seconds=0)
                assert claimed["task_id"] == task
                question = await asyncio.to_thread(
                    bob.call,
                    "ask_question",
                    {
                        "lease_id": lease["lease_id"],
                        "task_id": task,
                        "operation_id": "q",
                        "message": "Filename?",
                        "choices": ["custom.txt"],
                    },
                )
                await monitor.poll()
                await controller.dispatch()
                shown = await call("follow_task", task_id=task)
                assert shown["questions"][0]["question_id"] == question["question_id"]
                await call(
                    "answer_question",
                    task_id=task,
                    question_id=question["question_id"],
                    action="accept",
                    answer="custom.txt",
                )
                await asyncio.to_thread(
                    bob.call,
                    "complete",
                    {
                        "lease_id": lease["lease_id"],
                        "task_id": task,
                        "summary": "long success narrative",
                        "completion": {
                            "version": 1,
                            "status": "completed",
                            "verification": "not_applicable",
                            "limitations": [],
                        },
                    },
                )
                await monitor.poll()
                await controller.dispatch()
                done = await call("follow_task", task_id=task)
                assert "summary" not in done
                assert done["display"] == "Done."
                assert not monitor.tasks
            assert len(native.turns) == 4
            await call("exit_requester_mode")
            assert controller.state == "stopped"
        finally:
            await bridge.close()
            controller.close()

    try:
        asyncio.run(run())
    finally:
        alice.close()
        bob.close()


def test_watch_capacity_is_checked_before_submission(tmp_path):
    from agent_relay.codex_requester import RequesterBridge

    async def run():
        relay = Relay()
        binding = Binding("requester", "g", "thread", mode_id="mode")
        controller = DeliveryController.create(tmp_path / "journal", binding, Native())
        monitor = RelayMonitor(controller, relay)
        bridge = RequesterBridge(relay)
        await bridge.schemas()
        bridge.bind(binding, monitor)
        for n in range(128):
            monitor.watch_task(f"task-{n}")
        for tool in ("send_task", "submit_and_follow"):
            result = await bridge.handle(
                {
                    "threadId": "thread",
                    "tool": tool,
                    "arguments": {"mode_id": "mode", "operation_id": "new", "prompt": "new work"},
                }
            )
            assert not result["success"]
        assert relay.calls == []
        assert controller.state == "active"
        await bridge.close()
        controller.close()

    asyncio.run(run())
