"""Dedicated native Codex responder. No provider worker or copied authentication."""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

from .client import Client, load
from .delivery import Binding, DeliveryController, RelayMonitor
from .mcp_server import RESPONDER_INSTRUCTIONS, build_server
from .native_codex import OwnedCodexSession

PINNED_CODEX_VERSION = "0.171.0"
DISABLED_FEATURES = (
    "shell_tool",
    "unified_exec",
    "shell_snapshot",
    "apps",
    "plugins",
    "remote_plugin",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "in_app_browser",
    "computer_use",
    "image_generation",
    "view_image",
    "multi_agent",
    "multi_agent_v2",
    "skill_search",
    "skill_mcp_dependency_install",
    "workspace_dependencies",
    "code_mode",
    "code_mode_host",
    "memories",
    "sleep_tool",
    "tool_suggest",
)


def native_overrides():
    return {
        "approval_policy": "never",
        "sandbox_mode": "read-only",
        "web_search": "disabled",
        "project_doc_max_bytes": 0,
        "mcp_servers": {},
        "features": {
            **dict.fromkeys(DISABLED_FEATURES, False),
            "skip_host_skill_discovery": True,
        },
    }


def validate_config(config):
    for key, expected in native_overrides().items():
        actual = config.get(key)
        if key == "features":
            if not isinstance(actual, dict) or any(actual.get(k) != v for k, v in expected.items()):
                raise ValueError("Native feature restrictions were not applied")
        elif key == "mcp_servers":
            if not isinstance(actual, dict) or any(
                not isinstance(server, dict) or server.get("enabled") is not False
                for server in actual.values()
            ):
                raise ValueError("Native mcp_servers restriction was not applied")
        elif actual != expected:
            raise ValueError(f"Native {key} restriction was not applied")


class ResponderBridge:
    """Expose task-only schemas using the same validation and handlers as role MCP."""

    def __init__(self, relay):
        if relay.role != "responder":
            raise ValueError("A responder bundle is required")
        self.server = build_server(relay)
        self.binding = None
        self.allowed = set()
        self.stopped = False

    async def schemas(self):
        tools = await self.server.list_tools()
        # Lease activation and background watches belong to launcher code.
        allowed = [tool for tool in tools if "lease_id" in tool.inputSchema.get("required", [])]
        self.allowed = {tool.name for tool in allowed if tool.name != "watch_requests"}
        return [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description or "",
                "inputSchema": tool.inputSchema,
            }
            for tool in allowed
            if tool.name in self.allowed
        ]

    def bind(self, binding: Binding):
        if self.binding is not None or self.stopped or binding.role != "responder":
            raise ValueError("Bridge can bind exactly one responder session")
        self.binding = binding

    def stop(self):
        self.stopped = True

    async def handle(self, params):
        arguments = params.get("arguments")
        if (
            self.stopped
            or self.binding is None
            or params.get("threadId") != self.binding.thread_id
            or params.get("namespace") is not None
            or params.get("tool") not in self.allowed
            or not isinstance(arguments, dict)
            or arguments.get("lease_id") != self.binding.lease_id
        ):
            return self.result(
                "PeerRelay tool denied: inactive or mismatched session authority", False
            )
        arguments = dict(arguments)
        if params["tool"] in ("wait_for_task", "wait_for_answer"):
            # The monitor owns idle/answer waits. Return immediately and end the turn.
            arguments["seconds"] = 0
        try:
            content = await self.server.call_tool(params["tool"], arguments)
            return self.result("\n".join(item.text for item in content if item.type == "text"))
        except Exception:
            return self.result("PeerRelay tool failed; do not replay uncertain effects", False)

    @staticmethod
    def result(text, success=True):
        return {"success": success, "contentItems": [{"type": "inputText", "text": text}]}


def native_command(executable, disabled_servers=()):
    command = [executable]
    for key, value in native_overrides().items():
        # JSON scalars/arrays are also TOML; tables need inline-table syntax.
        if key == "mcp_servers":
            # Empty tables merge with user config rather than clearing it. Override
            # each entry, using a top-level TOML table so dotted names stay literal.
            entries = ",".join(
                json.dumps(name, ensure_ascii=False) + "={enabled=false}"
                for name in disabled_servers
            )
            command.extend(["-c", "mcp_servers={" + entries + "}"])
        elif isinstance(value, dict) and value:
            for subkey, setting in value.items():
                command.extend(["-c", f"{key}.{subkey}={json.dumps(setting)}"])
        else:
            command.extend(["-c", key + "=" + ("{}" if value == {} else json.dumps(value))])
    return tuple(command)


def discover_mcp_servers(command, *, cwd=None, env=None):
    """Read effective registration names only; never expose transport credentials."""
    try:
        result = subprocess.run(
            [*command, "mcp", "list", "--json"], cwd=cwd, env=env,
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode:
            raise ValueError("MCP inventory failed")
        servers = json.loads(result.stdout)
        if not isinstance(servers, list) or any(
            not isinstance(server, dict)
            or not isinstance(server.get("name"), str)
            or not server["name"]
            for server in servers
        ):
            raise ValueError("Invalid MCP inventory")
        return tuple(sorted({server["name"] for server in servers}))
    except (OSError, subprocess.SubprocessError, ValueError):
        raise ValueError("Cannot inspect native MCP registrations; startup refused") from None


def preflight(cwd=None):
    executable = shutil.which("codex")
    if not executable:
        raise ValueError("Codex CLI is required; install it and sign in normally first")
    result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=15)
    if result.returncode or result.stdout.strip() != "codex-cli " + PINNED_CODEX_VERSION:
        raise ValueError(f"Dedicated responder requires validated Codex CLI {PINNED_CODEX_VERSION}")
    servers = discover_mcp_servers(native_command(executable), cwd=cwd)
    return native_command(executable, servers)


def validate_thread(result):
    if (
        result.get("approvalPolicy") != "never"
        or result.get("sandbox", {}).get("type") != "readOnly"
    ):
        raise ValueError("Native thread did not retain read-only / never restrictions")


async def activate(client):
    # A cancelled to_thread wait does not stop the HTTP operation. Settle it before
    # client.close checks which exact lease it must revoke.
    task = asyncio.create_task(asyncio.to_thread(client.call, "response_mode", {}))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def run_responder(config_path, directory):
    config = load(config_path)
    if config["role"] != "responder":
        raise ValueError("A responder bundle is required")
    directory = Path(directory).expanduser().absolute()
    if directory.parent.resolve() != directory.parent:
        raise ValueError("Session directory must not contain symlinks")
    directory.mkdir(mode=0o700)
    work = directory / "work"
    work.mkdir(mode=0o700)
    command = await asyncio.to_thread(preflight, work)
    client, session, controller, bridge = None, None, None, None
    try:
        client = Client(config)
        bridge = ResponderBridge(client)
        schemas = await bridge.schemas()
        session = await OwnedCodexSession.start(
            directory / "native",
            cwd=work,
            command=command,
            # Remote resume inherits the owned server/thread policy. Codex rejects
            # permission overrides on the attaching terminal command.
            terminal_command=(command[0],),
            allow_turns=True,
            experimental=True,
            tool_handler=bridge.handle,
            validate_config=validate_config,
            thread_options={
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "dynamicTools": schemas,
                "developerInstructions": RESPONDER_INSTRUCTIONS + "\n"
                "This dedicated session is activated by launcher code. Never activate a lease. "
                "Use only the exact lease in a PeerRelay wake. Waiting for tasks and answers is "
                "owned by code: after idle or pending, end the turn. Never poll with more turns. "
                "Use only PeerRelay remote tools for requester work, never local tools.",
            },
        )
        validate_thread(session.start_result)
        await session.launch_terminal()
        # Activate only after configuration validation and terminal creation succeeded.
        activation = await activate(client)
        binding = Binding(
            "responder", session.generation, session.thread_id, lease_id=activation["lease_id"]
        )
        bridge.bind(binding)
        controller = DeliveryController.create(directory / "delivery.sqlite", binding, session.rpc)
        await session.run_delivery(controller, RelayMonitor(controller, client))
    finally:
        if bridge:
            bridge.stop()
        if session:
            await session.close()
        if controller:
            controller.close()
        if client:
            await asyncio.to_thread(client.close)
