"""Owned Codex app-server lifecycle; never attaches to an existing desktop chat.

The transport defaults to metadata-only operations. Enabling turns is an explicit
integration choice. The dedicated launcher applies restriction checks; complete
local-read isolation and live native/TUI behavior are not established by this transport.
"""

import asyncio
import json
import os
import uuid
from pathlib import Path

from websockets.asyncio.client import unix_connect

from .delivery import identifier


class RPCError(RuntimeError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(f"Native RPC error {code}: {message}")


class NativeRPC:
    METHODS = {
        "initialize",
        "thread/start",
        "thread/resume",
        "thread/read",
        "thread/name/set",
        "config/read",
    }
    NOTIFICATIONS = {"thread/status/changed", "turn/started", "turn/completed"}

    def __init__(self, wire, *, timeout=10, allow_turns=False, tool_handler=None):
        self.wire, self.timeout, self.allow_turns = wire, timeout, allow_turns
        self.serial, self.pending = 0, {}
        self.notifications = asyncio.Queue(maxsize=256)
        self.closed = asyncio.Event()
        self.failure = None
        self.tool_handler, self.requests = tool_handler, {}
        self.reader = asyncio.create_task(self._read())

    @classmethod
    async def connect(
        cls, path, *, timeout=10, allow_turns=False, experimental=False, tool_handler=None
    ):
        wire = await unix_connect(
            path=str(path),
            uri="ws://localhost",
            open_timeout=timeout,
            close_timeout=1,
            max_size=4 * 1024 * 1024,
        )
        return await cls.connect_wire(
            wire,
            timeout=timeout,
            allow_turns=allow_turns,
            experimental=experimental,
            tool_handler=tool_handler,
        )

    @classmethod
    async def connect_wire(
        cls, wire, *, timeout=10, allow_turns=False, experimental=False, tool_handler=None
    ):
        rpc = cls(wire, timeout=timeout, allow_turns=allow_turns, tool_handler=tool_handler)
        try:
            await rpc.call(
                "initialize",
                {
                    "clientInfo": {"name": "peerrelay_owned_session", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": experimental},
                },
            )
            await wire.send(json.dumps({"method": "initialized"}))
            return rpc
        except BaseException:
            await rpc.close()
            raise

    async def _read(self):
        try:
            while True:
                message = json.loads(await self.wire.recv())
                if "id" in message and "method" in message:
                    if message["method"] == "item/tool/call" and self.tool_handler:
                        ident = message["id"]
                        if ident in self.requests or len(self.requests) >= 16:
                            self.failure = "native_tool_request_conflict"
                            return
                        self.requests[ident] = asyncio.create_task(self._tool_request(message))
                        continue
                    # Do not guess approval ownership or answer a native request on
                    # the human's behalf. The next integration must route to the UI.
                    self.failure = "native_request_requires_ui"
                    return
                if "id" in message:
                    future = self.pending.get(message["id"])
                    if future is not None and not future.done():
                        if "error" in message:
                            error = message["error"]
                            future.set_exception(RPCError(error["code"], error["message"]))
                        else:
                            future.set_result(message["result"])
                elif message.get("method") in self.NOTIFICATIONS:
                    self.notifications.put_nowait(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failure = "native_transport_lost"
        finally:
            self.closed.set()
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError(self.failure or "native_closed"))
            await self.wire.close()

    async def _tool_request(self, message):
        try:
            result = await self.tool_handler(message["params"])
            await self.wire.send(json.dumps({"id": message["id"], "result": result}))
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failure = "native_tool_bridge_failed"
            self.reader.cancel()
        finally:
            self.requests.pop(message["id"], None)

    async def call(self, method, params):
        if method not in self.METHODS and not (method == "turn/start" and self.allow_turns):
            raise ValueError("Native operation disabled for this connection")
        if self.closed.is_set():
            raise ConnectionError(self.failure or "native_closed")
        self.serial += 1
        ident = self.serial
        future = asyncio.get_running_loop().create_future()
        self.pending[ident] = future
        try:
            async with asyncio.timeout(self.timeout):
                await self.wire.send(json.dumps({"id": ident, "method": method, "params": params}))
                return await future
        finally:
            self.pending.pop(ident, None)
            if not future.done():
                future.cancel()

    async def close(self):
        self.reader.cancel()
        await asyncio.gather(self.reader, return_exceptions=True)
        jobs = list(self.requests.values())
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        await self.wire.close()


async def wait_resumable(rpc, thread_id, *, timeout=10, interval=0.1):
    """Retry only the known asynchronous rollout-readiness error, with a deadline."""
    identifier(thread_id)
    async with asyncio.timeout(timeout):
        while True:
            try:
                result = await rpc.call("thread/resume", {"threadId": thread_id})
                if result["thread"]["id"] != thread_id:
                    raise RuntimeError("Native resume returned a different thread")
                return result
            except RPCError as error:
                if error.code != -32600 or "no rollout found for thread id" not in error.message:
                    raise
                await asyncio.sleep(interval)


class OwnedCodexSession:
    def __init__(self, directory, cwd, command):
        self.directory, self.cwd, self.command = directory, cwd, tuple(command)
        self.generation = str(uuid.uuid4())
        self.socket = directory / "s"
        self.socket_identity = None
        self.server = self.terminal = self.rpc = None
        self.thread_id = None
        self.env = None

    @classmethod
    async def start(
        cls,
        directory,
        *,
        cwd,
        command=("codex",),
        env=None,
        thread_options=None,
        timeout=10,
        allow_turns=False,
        experimental=False,
        tool_handler=None,
        validate_config=None,
    ):
        directory, cwd = Path(directory).absolute(), Path(cwd).resolve(strict=True)
        thread_options = dict(thread_options or {})
        if "cwd" in thread_options and Path(thread_options["cwd"]).resolve(strict=True) != cwd:
            raise ValueError("thread_options cwd differs from the bound working directory")
        thread_options["cwd"] = str(cwd)
        if directory.parent.resolve() != directory.parent:
            raise ValueError("Session directory must not contain symlinks")
        if len(os.fsencode(directory / "s")) >= 104:
            raise ValueError("Choose a shorter private session directory for the Unix socket")
        directory.mkdir(mode=0o700)  # Must be new: never reuses another generation/socket.
        session = cls(directory, cwd, command)
        session.env = env
        try:
            with open(directory / "server.log", "xb") as log:
                os.chmod(log.name, 0o600)
                session.server = await asyncio.create_subprocess_exec(
                    *command,
                    "app-server",
                    "--listen",
                    "unix://" + str(session.socket),
                    cwd=cwd,
                    env=env,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=log,
                )
            async with asyncio.timeout(timeout):
                while not session.socket.exists():
                    if session.server.returncode is not None:
                        raise RuntimeError("Owned native app-server exited during startup")
                    await asyncio.sleep(0.05)
                st = session.socket.lstat()
                session.socket_identity = (st.st_dev, st.st_ino)
                session.rpc = await NativeRPC.connect(
                    session.socket,
                    timeout=timeout,
                    allow_turns=allow_turns,
                    **(
                        {"experimental": experimental, "tool_handler": tool_handler}
                        if experimental or tool_handler
                        else {}
                    ),
                )
                if validate_config:
                    validate_config((await session.rpc.call("config/read", {}))["config"])
                options = {"cwd": str(cwd), "approvalPolicy": "on-request", "sandbox": "read-only"}
                options.update(thread_options or {})
                result = await session.rpc.call("thread/start", options)
                session.start_result = result
                session.thread_id = identifier(result["thread"]["id"])
                # A fresh no-turn thread may not have a rollout until metadata is written.
                await session.rpc.call(
                    "thread/name/set",
                    {"threadId": session.thread_id, "name": "PeerRelay owned session"},
                )
                await wait_resumable(session.rpc, session.thread_id, timeout=timeout)
            record = {
                "generation": session.generation,
                "thread_id": session.thread_id,
                "socket": str(session.socket),
            }
            with open(directory / "session.json", "x") as file:
                os.chmod(file.name, 0o600)
                json.dump(record, file)
            return session
        except BaseException:
            await session.close()
            raise

    def resume_argv(self):
        if self.thread_id is None or self.server.returncode is not None:
            raise RuntimeError("Owned session is not ready")
        return [
            *self.command,
            "resume",
            self.thread_id,
            "--remote",
            "unix://" + str(self.socket),
            "--no-alt-screen",
            "-C",
            str(self.cwd),
        ]

    async def launch_terminal(self):
        """Inherit a human terminal. Never writes keystrokes or accepts native prompts."""
        if self.terminal is not None:
            raise RuntimeError("Native terminal already launched")
        self.terminal = await asyncio.create_subprocess_exec(
            *self.resume_argv(), cwd=self.cwd, env=self.env
        )
        return self.terminal

    async def run_delivery(self, controller, monitor):
        """Coordinate code waiting, native lifecycle and exact-generation notifications."""
        if (
            controller.binding.thread_id != self.thread_id
            or controller.binding.generation != self.generation
            or controller.native is not self.rpc
            or monitor.controller is not controller
        ):
            raise ValueError("Delivery binding differs from owned native session")
        if self.terminal is None or self.terminal.returncode is not None:
            raise RuntimeError("A running owned native terminal is required")

        async def notices():
            while True:
                message = await self.rpc.notifications.get()
                controller.observe(self.generation, message)

        jobs = [
            asyncio.create_task(monitor.run()),
            asyncio.create_task(notices()),
            asyncio.create_task(self.rpc.closed.wait()),
            asyncio.create_task(self.server.wait()),
            asyncio.create_task(self.terminal.wait()),
        ]
        try:
            await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
        finally:
            controller.stop(self.rpc.failure or "native_session_ended")
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            await self.close()

    async def close(self):
        if self.rpc is not None:
            await self.rpc.close()
        for process in (self.terminal, self.server):
            if process is not None and process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    continue
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except TimeoutError:
                    process.kill()
                    await process.wait()
        if self.socket_identity is not None and self.socket.exists():
            st = self.socket.lstat()
            if (st.st_dev, st.st_ino) == self.socket_identity:
                self.socket.unlink()
