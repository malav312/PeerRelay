"""Dedicated native requester: code owns waits, one mode and task delivery."""

import asyncio
import json
from pathlib import Path

from .client import Client, load
from .codex_responder import ResponderBridge, preflight, validate_config, validate_thread
from .delivery import Binding, DeliveryController, RelayMonitor
from .mcp_server import REQUESTER_INSTRUCTIONS, build_server
from .native_codex import OwnedCodexSession
from .policy import untrusted
from .presentation import task_view


class RequesterBridge:
    """Same role handlers, with immediate follow and automatic task registration.

    Native questions are returned to the chat, never answered by this bridge.
    A human reply is forwarded through answer_question on the same mode/task.
    """

    TOOLS = {
        "submit_and_follow",
        "send_task",
        "follow_task",
        "task_status",
        "answer_question",
        "cancel_task",
        "exit_requester_mode",
        "get_result",
        "view_changes",
        "get_changes_for_analysis",
        "usage",
        "resume_requester_mode",
    }

    def __init__(self, relay):
        if relay.role != "requester":
            raise ValueError("A requester bundle is required")
        self.relay, self.server = relay, build_server(relay)
        self.binding = self.monitor = None
        self.allowed = set()
        self.stopped = False

    async def schemas(self):
        tools = await self.server.list_tools()
        schemas = [
            {
                "type": "function",
                "name": t.name,
                "description": t.description or "",
                "inputSchema": t.inputSchema,
            }
            for t in tools
            if t.name in self.TOOLS
        ]
        schemas.append(
            {
                "type": "function",
                "name": "requester_session",
                "description": "Get this session's existing mode and automatic delivery status.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            }
        )
        self.allowed = {t["name"] for t in schemas}
        return schemas

    def bind(self, binding, monitor):
        if (
            self.binding is not None
            or self.stopped
            or binding.role != "requester"
            or monitor.controller.binding != binding
            or monitor.relay is not self.relay
        ):
            raise ValueError("Bridge can bind exactly one owned requester mode")
        self.binding, self.monitor = binding, monitor

    def stop(self):
        self.stopped = True

    async def close(self):
        self.stop()
        await self.server.watches.close()
        await asyncio.to_thread(self.server.changes_viewer.close)

    async def status(self, task_id):
        raw = await asyncio.to_thread(
            self.relay.call,
            "status",
            {
                "mode_id": self.binding.mode_id,
                "task_id": task_id,
            },
        )
        self.monitor.consume(task_id, raw)
        view = task_view(raw)
        view["resume"] = {"mode_id": self.binding.mode_id, "task_id": task_id}
        view["follow_state"] = (
            "complete"
            if raw["state"] not in ("queued", "running")
            else "needs_input"
            if raw["questions"]
            else "waiting"
        )
        view["background_delivery"] = "owned_native_session"
        return untrusted(view, "Bob")

    async def handle(self, params):
        args = params.get("arguments")
        tool = params.get("tool")
        if (
            self.stopped
            or self.binding is None
            or params.get("threadId") != self.binding.thread_id
            or params.get("namespace") is not None
            or tool not in self.allowed
            or not isinstance(args, dict)
            or (tool != "requester_session" and args.get("mode_id") != self.binding.mode_id)
        ):
            return ResponderBridge.result(
                "PeerRelay tool denied: mismatched session authority", False
            )
        try:
            async with self.monitor.lock:
                if self.stopped or self.monitor.controller.state != "active":
                    raise ValueError("Delivery stopped")
                if tool in ("submit_and_follow", "send_task") and len(self.monitor.tasks) >= 128:
                    return ResponderBridge.result(
                        "Task watch limit reached. Follow existing tasks before submitting more.",
                        False,
                    )
                if tool == "requester_session":
                    if args:
                        raise ValueError("Unexpected arguments")
                    mode = await asyncio.to_thread(
                        self.relay.call,
                        "mode_status",
                        {
                            "mode_id": self.binding.mode_id,
                        },
                    )
                    result = {
                        "mode_id": self.binding.mode_id,
                        "mode": mode,
                        "background_delivery": "owned_native_session",
                    }
                elif tool in ("submit_and_follow", "follow_task"):
                    if tool == "submit_and_follow":
                        mode = await asyncio.to_thread(
                            self.relay.call,
                            "mode_status",
                            {
                                "mode_id": self.binding.mode_id,
                            },
                        )
                        if not mode["workspace_matches"]:
                            raise ValueError("Workspace changed")
                        # Submit through the existing role handler, never another mode.
                        content = await self.server.call_tool(
                            "send_task", {k: args[k] for k in ("mode_id", "operation_id", "prompt")}
                        )
                        submitted = json.loads("".join(x.text for x in content if x.type == "text"))
                        task = submitted["task_id"]
                        # Watch before status read, so a failed response never loses delivery.
                        self.monitor.watch_task(task)
                    else:
                        task = args["task_id"]
                    result = await self.status(task)
                else:
                    content = await self.server.call_tool(tool, args)
                    result = json.loads("".join(x.text for x in content if x.type == "text"))
                    if tool == "send_task":
                        self.monitor.watch_task(result["task_id"])
                    elif tool == "task_status":
                        self.monitor.consume(args["task_id"], result)
                    elif tool == "answer_question":
                        # Return the next question/status in the same tool roundtrip.
                        result = await self.status(args["task_id"])
                    elif tool == "cancel_task":
                        self.monitor.forget_task(args["task_id"])
                    elif tool == "exit_requester_mode":
                        self.stop()
                        self.monitor.controller.stop("mode_exited")
                return ResponderBridge.result(json.dumps(result))
        except Exception:
            return ResponderBridge.result(
                "PeerRelay tool failed; inspect existing task status. "
                "Do not replay uncertain work.",
                False,
            )


async def run_requester(config_path, directory, *, execution=False):
    config = load(config_path)
    if config["role"] != "requester":
        raise ValueError("A requester bundle is required")
    directory = Path(directory).expanduser().absolute()
    if directory.parent.resolve() != directory.parent:
        raise ValueError("Session directory must not contain symlinks")
    directory.mkdir(mode=0o700)
    work = directory / "work"
    work.mkdir(mode=0o700)
    command = await asyncio.to_thread(preflight, work)
    client = session = controller = bridge = None
    mode_id = None
    try:
        client = Client(config)
        bridge = RequesterBridge(client)
        session = await OwnedCodexSession.start(
            directory / "native",
            cwd=work,
            command=command,
            terminal_command=(command[0],),
            allow_turns=True,
            experimental=True,
            tool_handler=bridge.handle,
            validate_config=validate_config,
            thread_options={
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "dynamicTools": await bridge.schemas(),
                "developerInstructions": REQUESTER_INSTRUCTIONS + "\n"
                "This dedicated requester is activated by launcher code. Never create a mode. "
                "Use requester_session to retrieve the existing mode before the first task. "
                "All tasks and follow-ups use that mode. Background waiting is already owned "
                "by code; do not probe channels, register watches or poll. Follow tools return "
                "immediately. Ask pending questions normally in chat and forward only the "
                "user's answer with answer_question. When waiting, end the turn. "
                "Use only PeerRelay tools for requester work. No local task solving.",
            },
        )
        validate_thread(session.start_result)
        await session.launch_terminal()

        async def activate():
            nonlocal mode_id
            result = await asyncio.to_thread(
                client.call, "requester_mode", {"execution": execution}
            )
            mode_id = result["mode_id"]

        activation = asyncio.create_task(activate())
        try:
            await asyncio.shield(activation)
        except asyncio.CancelledError:
            await activation
            raise
        binding = Binding("requester", session.generation, session.thread_id, mode_id=mode_id)
        controller = DeliveryController.create(directory / "delivery.sqlite", binding, session.rpc)
        monitor = RelayMonitor(controller, client)
        bridge.bind(binding, monitor)
        await session.run_delivery(controller, monitor)
    finally:
        if bridge:
            await bridge.close()
        if session:
            await session.close()
        if controller:
            controller.close()
        if client:
            try:
                if mode_id:
                    await asyncio.to_thread(client.call, "exit_mode", {"mode_id": mode_id})
            finally:
                await asyncio.to_thread(client.close)
