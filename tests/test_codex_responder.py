import asyncio
import json

import pytest


def test_bridge_registers_existing_handlers_and_rejects_other_authority():
    from agent_relay.codex_responder import ResponderBridge
    from agent_relay.delivery import Binding

    class Relay:
        role = "responder"

        def __init__(self):
            self.calls = []

        def call(self, method, arguments):
            self.calls.append((method, arguments))
            return {"content": "from Alice", "version": "v1"}

    async def run():
        relay = Relay()
        bridge = ResponderBridge(relay)
        schemas = await bridge.schemas()
        names = {tool["name"] for tool in schemas}
        assert {"remote_read", "wait_for_task", "complete_task", "disconnect"} <= names
        assert not names & {"response_mode", "watch_requests", "stop_watching", "acknowledge_event"}
        bridge.bind(Binding("responder", "generation", "thread", lease_id="lease"))
        params = {
            "threadId": "thread",
            "turnId": "turn",
            "callId": "call",
            "tool": "remote_read",
            "arguments": {"lease_id": "lease", "task_id": "task", "path": "x"},
        }
        for altered in (
            {"threadId": "other"},
            {"tool": "response_mode"},
            {"arguments": {**params["arguments"], "lease_id": "other"}},
        ):
            result = await bridge.handle({**params, **altered})
            assert not result["success"]
        assert relay.calls == []
        result = await bridge.handle(params)
        assert result["success"]
        assert "from Alice" in result["contentItems"][0]["text"]
        assert relay.calls == [
            ("remote_tool", {"lease_id": "lease", "task_id": "task", "path": "x", "kind": "read"})
        ]
        bridge.stop()
        assert not (await bridge.handle(params))["success"]
        assert len(relay.calls) == 1

    asyncio.run(run())


def test_bridge_waits_for_work_and_answers_in_code_not_model():
    from agent_relay.codex_responder import ResponderBridge
    from agent_relay.delivery import Binding

    class Relay:
        role = "responder"

        async def wait_for_task(self, lease_id, seconds):
            assert lease_id == "lease" and seconds == 0
            return {"state": "idle"}

        def call(self, method, args):
            assert method == "question_status"
            assert "seconds" not in args
            return {"state": "pending"}

    async def run():
        bridge = ResponderBridge(Relay())
        await bridge.schemas()
        bridge.bind(Binding("responder", "g", "t", lease_id="lease"))
        for tool, args in [
            ("wait_for_task", {}),
            ("wait_for_answer", {"task_id": "task", "question_id": "q"}),
        ]:
            result = await bridge.handle(
                {
                    "threadId": "t",
                    "turnId": "turn",
                    "callId": tool,
                    "tool": tool,
                    "arguments": {"lease_id": "lease", "seconds": 50, **args},
                }
            )
            assert result["success"]

    asyncio.run(run())


def test_config_validation_fails_closed_before_activation():
    from agent_relay.codex_responder import native_overrides, validate_config

    config = native_overrides()
    validate_config(config)
    for changed in (
        {"sandbox_mode": "danger-full-access"},
        {"approval_policy": "on-request"},
        {"features": {}},
        {"mcp_servers": {"other": {"command": "tool"}}},
        {"web_search": "live"},
    ):
        with pytest.raises(ValueError):
            validate_config({**config, **changed})


def test_config_accepts_retained_mcp_definitions_only_when_explicitly_disabled():
    from agent_relay.codex_responder import native_overrides, validate_config

    config = native_overrides()
    config["mcp_servers"] = {
        "existing": {"command": "never-start", "enabled": False},
        "remote": {"url": "https://example.invalid/mcp", "enabled": False},
    }
    validate_config(config)
    for enabled in (True, None, 0, "false"):
        config["mcp_servers"]["existing"]["enabled"] = enabled
        with pytest.raises(ValueError, match="mcp_servers"):
            validate_config(config)


@pytest.mark.parametrize("failure", [None, "terminal", "start", "delivery"])
def test_launcher_owns_exact_lease_and_cleans_up(tmp_path, monkeypatch, failure):
    from agent_relay import codex_responder as module

    events = []

    class Relay:
        role = "responder"

        def __init__(self, config):
            self.lease = None

        def call(self, method, args):
            events.append(method)
            assert method == "response_mode"
            self.lease = "lease"
            return {"lease_id": "lease"}

        def close(self):
            events.append("client_closed")
            if self.lease:
                events.append("disconnected")

    class Native:
        thread_id, generation, rpc = "thread", "generation", object()
        start_result = {"approvalPolicy": "never", "sandbox": {"type": "readOnly"}}

        @classmethod
        async def start(cls, directory, **kwargs):
            events.append("native_started")
            assert "dynamicTools" in kwargs["thread_options"]
            assert kwargs["allow_turns"] and kwargs["experimental"]
            assert "response_mode" not in {
                t["name"] for t in kwargs["thread_options"]["dynamicTools"]
            }
            if failure == "start":
                raise RuntimeError("startup refused")
            return cls()

        async def launch_terminal(self):
            events.append("terminal_started")
            if failure == "terminal":
                raise RuntimeError("terminal refused")

        async def run_delivery(self, controller, monitor):
            events.append("delivery_started")
            assert controller.binding.lease_id == "lease"
            assert controller.binding.thread_id == self.thread_id
            assert controller.binding.generation == self.generation
            if failure == "delivery":
                raise RuntimeError("transport lost")

        async def close(self):
            events.append("native_closed")

    monkeypatch.setattr(module, "load", lambda path: {"role": "responder"})
    monkeypatch.setattr(module, "Client", Relay)
    monkeypatch.setattr(module, "OwnedCodexSession", Native)
    monkeypatch.setattr(module, "preflight", lambda cwd: ("codex",))

    async def run():
        if failure:
            with pytest.raises(RuntimeError):
                await module.run_responder("config", tmp_path / "session")
        else:
            await module.run_responder("config", tmp_path / "session")

    asyncio.run(run())
    assert events[-1] in ("client_closed", "disconnected")
    if failure in ("start", "terminal"):
        assert "response_mode" not in events
    else:
        assert events.count("response_mode") == 1
        assert events.index("terminal_started") < events.index("response_mode")
        assert events[-1] == "disconnected"
    if failure != "start":
        assert "native_closed" in events


def test_preflight_rejects_unpinned_cli(monkeypatch):
    from types import SimpleNamespace

    from agent_relay import codex_responder as module

    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout="codex-cli 0.170.0"),
    )
    with pytest.raises(ValueError, match="0.171.0"):
        module.preflight()


def test_preflight_disables_existing_servers_without_changing_user_config(tmp_path, monkeypatch):
    import tomllib
    from types import SimpleNamespace

    from agent_relay import codex_responder as module

    names = ["peerrelay", "team.server", 'quoted"name']

    def run(argv, **kwargs):
        if argv[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.171.0")
        assert argv[-3:] == ["mcp", "list", "--json"]
        assert kwargs["cwd"] == tmp_path
        return SimpleNamespace(returncode=0, stdout=json.dumps([
            {"name": name, "enabled": True, "transport": {"env": {"SECRET": "fixture"}}}
            for name in names
        ]))

    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr(module.subprocess, "run", run)
    command = module.preflight(tmp_path)
    overrides = [command[i + 1] for i, value in enumerate(command) if value == "-c"]
    mcp = next(tomllib.loads(value)["mcp_servers"] for value in overrides
               if value.startswith("mcp_servers="))
    assert mcp == {name: {"enabled": False} for name in names}
    assert not any("fixture" in value or "SECRET" in value for value in command)


@pytest.mark.parametrize("output", ["not json", "{}", '[{"enabled": true}]'])
def test_mcp_inventory_failure_is_not_treated_as_empty(output, monkeypatch):
    from types import SimpleNamespace

    from agent_relay import codex_responder as module

    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout=output))
    with pytest.raises(ValueError, match="MCP"):
        module.discover_mcp_servers(("codex",))


# Reuse the disposable TLS relay fixture, never a user's live pair.
from test_mcp import live_pair  # noqa: E402,F401


def test_disposable_relay_delivery_claim_question_answer_complete(live_pair, tmp_path):  # noqa: F811
    from agent_relay.client import Client, load
    from agent_relay.codex_responder import ResponderBridge
    from agent_relay.delivery import Binding, DeliveryController, RelayMonitor
    from agent_relay.mcp_server import build_server

    pair, root, _ = live_pair
    alice = Client(load(pair / "requester/client.json"))
    bob = Client(load(pair / "responder/client.json"))

    class Native:
        def __init__(self):
            self.turns, self.wakes = [], []

        async def call(self, method, params):
            assert params["threadId"] == "owned-thread"
            if method == "thread/read":
                return {
                    "thread": {
                        "id": "owned-thread",
                        "status": {"type": "idle"},
                        "turns": self.turns,
                    }
                }
            assert method == "turn/start"
            self.wakes.append(params)
            turn = {"id": "turn-" + str(len(self.wakes)), "status": "completed"}
            self.turns.append(turn)
            return {"turn": turn}

    async def run():
        requester = build_server(alice)

        async def request(tool, **args):
            result = await requester.call_tool(tool, args)
            return json.loads("".join(item.text for item in result))

        bridge = ResponderBridge(bob)
        await bridge.schemas()
        lease = await asyncio.to_thread(bob.call, "response_mode", {})
        binding = Binding(
            "responder", "owned-generation", "owned-thread", lease_id=lease["lease_id"]
        )
        bridge.bind(binding)

        async def respond(tool, **args):
            result = await bridge.handle(
                {
                    "threadId": binding.thread_id,
                    "turnId": "turn",
                    "callId": tool,
                    "tool": tool,
                    "arguments": {"lease_id": binding.lease_id, **args},
                }
            )
            assert result["success"], result
            return json.loads(result["contentItems"][0]["text"])

        native = Native()
        controller = DeliveryController.create(tmp_path / "delivery.sqlite", binding, native)
        monitor = RelayMonitor(controller, bob)
        try:
            mode = (await request("requester_mode"))["mode_id"]
            await monitor.poll()
            assert not await controller.dispatch()
            task = (
                await request(
                    "send_task", mode_id=mode, operation_id="one", prompt="Read hello and ask first"
                )
            )["task_id"]
            await monitor.poll()
            assert await controller.dispatch()
            assert len(native.wakes) == 1
            assert (await respond("wait_for_task", seconds=50))["task_id"] == task
            assert (await respond("remote_read", task_id=task, path="hello.txt"))[
                "content"
            ] == "before\n"
            question = await respond(
                "ask_requester",
                task_id=task,
                operation_id="question",
                message="Proceed?",
                choices=["Yes", "No"],
            )
            assert (
                await respond("wait_for_answer", task_id=task, question_id=question["question_id"])
            )["state"] == "pending"
            for _ in range(3):
                await monitor.poll()
                assert not await controller.dispatch()
            await request(
                "answer_question",
                mode_id=mode,
                task_id=task,
                question_id=question["question_id"],
                action="accept",
                answer="Yes",
            )
            await monitor.poll()
            assert await controller.dispatch()
            assert len(native.wakes) == 2
            answer = await respond(
                "wait_for_answer", task_id=task, question_id=question["question_id"]
            )
            assert answer["action"] == "accept" and answer["answer"] == "Yes"
            await respond("complete_task", task_id=task, summary="Read hello.txt", answer="before")
            assert (await request("task_status", mode_id=mode, task_id=task))[
                "state"
            ] == "succeeded"
            for _ in range(3):
                await monitor.poll()
                assert not await controller.dispatch()
            assert len(native.wakes) == 2
            assert (root / "hello.txt").read_text() == "before\n"
        finally:
            controller.close()
            bridge.stop()

    try:
        asyncio.run(run())
    finally:
        bob.close()
        alice.close()


def test_cancelled_activation_settles_before_cleanup():
    import threading

    from agent_relay.codex_responder import activate

    entered, release = threading.Event(), threading.Event()

    class Relay:
        lease = None

        def call(self, method, args):
            entered.set()
            assert release.wait(1)
            self.lease = "owned-lease"
            return {"lease_id": self.lease}

    async def run():
        relay = Relay()
        task = asyncio.create_task(activate(relay))
        await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert relay.lease == "owned-lease"

    asyncio.run(run())


def test_native_start_policy_must_match_restrictions():
    from agent_relay.codex_responder import validate_thread

    validate_thread({"approvalPolicy": "never", "sandbox": {"type": "readOnly"}})
    for result in (
        {"approvalPolicy": "on-request", "sandbox": {"type": "readOnly"}},
        {"approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}},
        {},
    ):
        with pytest.raises(ValueError):
            validate_thread(result)
