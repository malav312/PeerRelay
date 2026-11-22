import asyncio
import json
import tempfile
from pathlib import Path

import pytest


def test_rpc_routes_notifications_and_late_replies_without_retry():
    from agent_relay.native_codex import NativeRPC

    class Wire:
        def __init__(self):
            self.inbox = asyncio.Queue()
            self.sent = []

        async def send(self, data):
            message = json.loads(data)
            self.sent.append(message)
            if message.get("method") == "initialize":
                await self.inbox.put(json.dumps({"id": message["id"], "result": {}}))

        async def recv(self):
            return await self.inbox.get()

        async def close(self):
            pass

    async def run():
        wire = Wire()
        rpc = await NativeRPC.connect_wire(wire, timeout=0.02)
        try:
            with pytest.raises(ValueError, match="disabled"):
                await rpc.call("turn/start", {"threadId": "thread", "input": []})
            with pytest.raises(TimeoutError):
                await rpc.call("thread/read", {"threadId": "thread"})
            request_id = wire.sent[-1]["id"]
            await wire.inbox.put(json.dumps({"id": request_id, "result": {"late": True}}))
            await wire.inbox.put(
                json.dumps(
                    {
                        "method": "thread/status/changed",
                        "params": {"threadId": "thread", "status": {"type": "idle"}},
                    }
                )
            )
            notice = await asyncio.wait_for(rpc.notifications.get(), 1)
            assert notice["params"]["threadId"] == "thread"
            assert len([m for m in wire.sent if m.get("method") == "thread/read"]) == 1
        finally:
            await rpc.close()

    asyncio.run(run())


def test_transport_server_request_stops_without_granting_approval():
    from agent_relay.native_codex import NativeRPC

    class Wire:
        def __init__(self):
            self.inbox = asyncio.Queue()
            self.sent = []

        async def send(self, data):
            msg = json.loads(data)
            self.sent.append(msg)
            if msg.get("method") == "initialize":
                await self.inbox.put(json.dumps({"id": msg["id"], "result": {}}))

        async def recv(self):
            return await self.inbox.get()

        async def close(self):
            pass

    async def run():
        wire = Wire()
        rpc = await NativeRPC.connect_wire(wire)
        try:
            await wire.inbox.put(
                json.dumps(
                    {
                        "id": "approval",
                        "method": "item/commandExecution/requestApproval",
                        "params": {},
                    }
                )
            )
            await asyncio.wait_for(rpc.closed.wait(), 1)
            assert rpc.failure == "native_request_requires_ui"
            assert all(m.get("id") != "approval" for m in wire.sent)
        finally:
            await rpc.close()

    asyncio.run(run())


@pytest.mark.parametrize("restricted", [False, True])
def test_owned_lifecycle_waits_for_exact_thread_and_cleans_only_owned_process(
    monkeypatch, restricted
):
    from agent_relay.native_codex import NativeRPC, OwnedCodexSession, RPCError

    class RPC:
        attempts = 0
        closed = False
        named = False

        async def call(self, method, params):
            if method == "thread/start":
                return {"thread": {"id": "exact-thread"}}
            if method == "thread/name/set":
                assert params["threadId"] == "exact-thread"
                self.named = True
                return {}
            assert method == "thread/resume"
            assert self.named, "Fresh native rollout needs metadata before resume"
            assert params == {"threadId": "exact-thread"}
            self.attempts += 1
            if self.attempts < 3:
                raise RPCError(-32600, "no rollout found for thread id exact-thread")
            return {"thread": {"id": "exact-thread"}}

        async def close(self):
            self.closed = True

    class Process:
        returncode = None
        terminated = False

        def terminate(self):
            self.terminated = True
            self.returncode = 0

        async def wait(self):
            return self.returncode

    rpc, process, argv = RPC(), Process(), []

    async def spawn(*args, **kwargs):
        argv.extend(args)
        socket = Path(args[args.index("--listen") + 1].removeprefix("unix://"))
        socket.touch()
        return process

    async def connect(path, **kwargs):
        return rpc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(NativeRPC, "connect", connect)

    async def run():
        with tempfile.TemporaryDirectory(prefix="pr-test-", dir="/private/tmp") as tmp:
            options = {}
            if restricted:
                options = {
                    "command": ("codex", "-c", 'approval_policy="never"'),
                    "terminal_command": ("codex",),
                }
            session = await OwnedCodexSession.start(
                Path(tmp) / "native", cwd=tmp, timeout=1, **options
            )
            socket = session.socket
            assert rpc.attempts == 3
            assert session.thread_id == "exact-thread"
            assert session.resume_argv()[1:3] == ["resume", "exact-thread"]
            assert "unix://" + str(socket) in session.resume_argv()
            assert "-c" not in session.resume_argv()
            if restricted:
                assert 'approval_policy="never"' in argv
            assert (session.directory.stat().st_mode & 0o777) == 0o700
            assert session.generation
            await session.close()
            assert process.terminated and rpc.closed
            assert not socket.exists()

    asyncio.run(run())


def test_readiness_is_bounded_and_does_not_retry_unrelated_errors():
    from agent_relay.native_codex import RPCError, wait_resumable

    class RPC:
        calls = 0
        error = RPCError(-32600, "no rollout found for thread id exact")

        async def call(self, method, params):
            self.calls += 1
            raise self.error

    async def run():
        rpc = RPC()
        with pytest.raises(TimeoutError):
            await wait_resumable(rpc, "exact", timeout=0.03, interval=0.005)
        assert 2 <= rpc.calls < 10
        rpc.calls = 0
        rpc.error = RPCError(-32600, "permission denied")
        with pytest.raises(RPCError):
            await wait_resumable(rpc, "exact", timeout=1)
        assert rpc.calls == 1

    asyncio.run(run())


def test_unix_websocket_transport_uses_real_framing_and_exact_id(tmp_path):
    from websockets.asyncio.server import unix_serve

    from agent_relay.native_codex import NativeRPC

    async def run():
        with tempfile.TemporaryDirectory(prefix="pr-ws-", dir="/private/tmp") as tmp:
            socket = Path(tmp) / "s"
            seen = []

            async def handler(wire):
                async for raw in wire:
                    message = json.loads(raw)
                    seen.append(message)
                    if "id" in message:
                        await wire.send(
                            json.dumps(
                                {
                                    "id": message["id"],
                                    "result": {
                                        "thread": {"id": message.get("params", {}).get("threadId")}
                                    },
                                }
                            )
                        )

            async with unix_serve(handler, path=socket):
                rpc = await NativeRPC.connect(socket)
                try:
                    result = await rpc.call("thread/read", {"threadId": "exact"})
                    assert result["thread"]["id"] == "exact"
                finally:
                    await rpc.close()
            assert [m["method"] for m in seen] == ["initialize", "initialized", "thread/read"]

    asyncio.run(run())


def test_existing_session_directory_is_never_reused(tmp_path):
    from agent_relay.native_codex import OwnedCodexSession

    async def run():
        with tempfile.TemporaryDirectory(prefix="pr-existing-", dir="/private/tmp") as tmp:
            with pytest.raises(FileExistsError):
                await OwnedCodexSession.start(tmp, cwd=tmp)

    asyncio.run(run())


def test_startup_failure_terminates_owned_server(monkeypatch):
    from agent_relay.native_codex import NativeRPC, OwnedCodexSession

    class Process:
        returncode = None
        terminated = False

        def terminate(self):
            self.terminated = True
            self.returncode = 0

        async def wait(self):
            return self.returncode

    process = Process()

    async def spawn(*args, **kwargs):
        Path(args[args.index("--listen") + 1].removeprefix("unix://")).touch()
        return process

    async def connect(path, **kwargs):
        raise ConnectionError("handshake failed")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(NativeRPC, "connect", connect)

    async def run():
        with tempfile.TemporaryDirectory(prefix="pr-fail-", dir="/private/tmp") as tmp:
            directory = Path(tmp) / "native"
            with pytest.raises(ConnectionError):
                await OwnedCodexSession.start(directory, cwd=tmp)
            assert process.terminated
            assert not (directory / "s").exists()

    asyncio.run(run())


def test_delivery_lifetime_stops_when_native_transport_is_lost(tmp_path):
    from agent_relay.delivery import Binding, DeliveryController
    from agent_relay.native_codex import OwnedCodexSession

    class Process:
        returncode = None

        def __init__(self):
            self.done = asyncio.Event()

        async def wait(self):
            await self.done.wait()
            return self.returncode

        def terminate(self):
            self.returncode = 0
            self.done.set()

    class RPC:
        failure = "native_transport_lost"

        def __init__(self):
            self.closed = asyncio.Event()
            self.notifications = asyncio.Queue()

        async def close(self):
            self.closed.set()

    class Monitor:
        def __init__(self, controller):
            self.controller = controller
            self.cancelled = False

        async def run(self):
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    async def run():
        session = OwnedCodexSession(tmp_path, tmp_path, ("codex",))
        session.thread_id = "thread"
        session.server, session.terminal, session.rpc = Process(), Process(), RPC()
        controller = DeliveryController.create(
            tmp_path / "journal.sqlite",
            Binding("responder", session.generation, "thread", lease_id="lease"),
            session.rpc,
        )
        monitor = Monitor(controller)
        try:
            running = asyncio.create_task(session.run_delivery(controller, monitor))
            await asyncio.sleep(0)
            session.rpc.closed.set()
            await asyncio.wait_for(running, 1)
            assert controller.reason == "native_transport_lost"
            assert monitor.cancelled
            assert session.server.returncode == session.terminal.returncode == 0
        finally:
            controller.close()

    asyncio.run(run())


def test_rpc_deadline_also_bounds_a_blocked_write():
    from agent_relay.native_codex import NativeRPC

    class Wire:
        def __init__(self):
            self.inbox = asyncio.Queue()

        async def send(self, data):
            message = json.loads(data)
            if message.get("method") == "initialize":
                await self.inbox.put(json.dumps({"id": message["id"], "result": {}}))
            elif message.get("method") == "thread/read":
                await asyncio.Event().wait()

        async def recv(self):
            return await self.inbox.get()

        async def close(self):
            pass

    async def run():
        rpc = await NativeRPC.connect_wire(Wire(), timeout=0.01)
        task = asyncio.create_task(rpc.call("thread/read", {"threadId": "thread"}))
        try:
            done, _ = await asyncio.wait([task], timeout=0.2)
            assert task in done, "RPC timeout must include sending, not only waiting for its reply"
            with pytest.raises(TimeoutError):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await rpc.close()

    asyncio.run(run())


def test_thread_options_cannot_change_the_bound_working_directory(tmp_path, monkeypatch):
    from agent_relay.native_codex import OwnedCodexSession

    async def refuse_spawn(*args, **kwargs):
        raise AssertionError("Conflicting cwd must fail before native launch")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse_spawn)

    async def run():
        with tempfile.TemporaryDirectory(prefix="pr-cwd-", dir="/private/tmp") as tmp:
            directory = Path(tmp) / "native"
            with pytest.raises(ValueError, match="cwd"):
                await OwnedCodexSession.start(
                    directory, cwd=tmp, thread_options={"cwd": str(tmp_path)}
                )
            assert not directory.exists()

    asyncio.run(run())


def test_dynamic_requests_do_not_block_reader_and_require_explicit_handler():
    from agent_relay.native_codex import NativeRPC

    class Wire:
        def __init__(self):
            self.inbox, self.sent = asyncio.Queue(), []

        async def send(self, data):
            message = json.loads(data)
            self.sent.append(message)
            if message.get("method") == "initialize":
                await self.inbox.put(json.dumps({"id": message["id"], "result": {}}))

        async def recv(self):
            return await self.inbox.get()

        async def close(self):
            pass

    async def run():
        wire, release = Wire(), asyncio.Event()

        async def handler(params):
            await release.wait()
            return {
                "success": True,
                "contentItems": [{"type": "inputText", "text": params["tool"]}],
            }

        rpc = await NativeRPC.connect_wire(wire, experimental=True, tool_handler=handler)
        try:
            assert wire.sent[0]["params"]["capabilities"]["experimentalApi"] is True
            await wire.inbox.put(
                json.dumps(
                    {"id": "tool1", "method": "item/tool/call", "params": {"tool": "remote_read"}}
                )
            )
            await wire.inbox.put(json.dumps({"method": "turn/completed", "params": {}}))
            await asyncio.wait_for(rpc.notifications.get(), 1)
            assert not rpc.closed.is_set()
            release.set()
            for _ in range(20):
                if any(m.get("id") == "tool1" for m in wire.sent):
                    break
                await asyncio.sleep(0)
            assert next(m for m in wire.sent if m.get("id") == "tool1")["result"]["success"]
        finally:
            await rpc.close()

    asyncio.run(run())
