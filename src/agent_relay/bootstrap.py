"""Bundle-free MCP entry, with an explicit persistent binding for each native chat.

Pairing consent and codes belong to the local view. MCP only sees setup receipts.
The existing role servers continue to own tool validation and relay policy.
"""

import asyncio
import fcntl
import hashlib
import json
import os
import re
import secrets
import threading
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .client import Client, load, private_file
from .mcp_server import REQUESTER_INSTRUCTIONS, RESPONDER_INSTRUCTIONS
from .mcp_server import build_server as build_role_server
from .pairing_cli import canonical_folder
from .setup import write_private, write_replace

ENTRY = """Use setup_begin with the selected role and a fresh random binding_id for this
chat (at least 16 characters). Retain binding_id and setup_id in this chat and its
continuation. Reuse exactly these IDs on resume; never use another chat's setup.
Open the returned setup_url for the person. Pairing codes, check numbers and consent
stay in that local view, never in tool arguments or model prompts. The host chooses
how to open the URL; do not promise a popup position or a native form.
Use setup_status when the person has finished. Paired means credentials saved, not
Connected. Once paired, role_tools returns the exact role tool schemas; invoke those
through role_call with this setup_id and binding_id, tool name and arguments.
Follow the role instructions below. A timeout is not permission to create a new
pair, replace credentials, reconnect a responder or replay a task. setup_cancel
stops setup only; after pairing use the role's disconnect/exit controls.
"""


def _identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", value):
        raise ValueError(f"{label} must be 16–128 letters, digits, hyphens or underscores")
    return value


class Session:
    def __init__(self, owner, directory, record):
        self.owner, self.directory, self.record = owner, directory, record
        self.lock = threading.RLock()
        self.fd = os.open(directory / "owner.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            raise ValueError(
                "This setup is active in another MCP process; resume its chat"
            ) from None
        self.view_token = secrets.token_urlsafe(32)
        self.view = {}
        self.worker = None
        self.cancelled = threading.Event()
        self.answer = threading.Event()
        self.accepted = False
        self.listener = None
        self.active_socket = None
        self.client = None
        self.server = None
        self.relay_process = None
        if self.record["state"] == "paired":
            self.record["relay"] = "not_checked"
            self.save()
        if self.record["state"] in ("pairing", "confirming", "waiting_peer", "cancelling"):
            # A crash may happen after host credential commit and before acknowledgement.
            # Never automatically repeat that transaction.
            self.record["state"] = "interrupted"
            self.record["error"] = "Setup interrupted; inspect the local view before retrying."
            self.save()

    def save(self):
        write_replace(self.directory / "setup.json", json.dumps(self.record, indent=2))

    def receipt(self):
        with self.lock:
            return {
                **{key: self.record[key] for key in ("setup_id", "role", "state", "relay")},
                "setup_url": f"{self.owner.url}/{self.view_token}",
                "error": self.record.get("error"),
            }

    def reuse(self, config_path):
        with self.lock:
            if self.record["state"] not in ("choose_pairing", "failed", "cancelled", "interrupted"):
                raise ValueError("Setup already started; keep the existing pairing")
            config_path = private_file(Path(config_path).expanduser().absolute())
            config = load(config_path)
            if config["role"] != self.record["role"]:
                raise ValueError("Bundle role does not match this chat role")
            client = Client(config)
            client.close()
            self.record.update(config=str(config_path), state="paired", relay="not_checked")
            self.record.pop("error", None)
            self.save()

    def role_server(self):
        with self.lock:
            if self.record["state"] != "paired":
                raise ValueError("Pairing is not ready; complete the local setup view")
            config = load(self.record["config"])
            if config["role"] != self.record["role"]:
                raise ValueError("Bundle role changed; create a deliberate new chat binding")
            if self.server is None:
                self.client = Client(config)
                self.server = build_role_server(self.client)
            return self.server

    def _confirm(self, details):
        with self.lock:
            if self.cancelled.is_set():
                return False
            # Never expose token digests or certificates to the view.
            self.view["confirmation"] = {
                k: v for k, v in details.items() if k not in ("token_sha256", "certificate")
            }
            self.answer.clear()
            self.record["state"] = "confirming"
            self.save()
        if not self.answer.wait(280):
            return False
        return self.accepted and not self.cancelled.is_set()

    def confirm(self, accepted):
        with self.lock:
            if type(accepted) is not bool or self.record["state"] != "confirming":
                raise ValueError("No pairing confirmation is pending")
            self.accepted = accepted
            self.record["state"] = "waiting_peer"
            self.save()
            self.answer.set()

    def _ready(self, details):
        with self.lock:
            self._before_commit()
            self.view.update(details)

    def _set_socket(self, sock):
        with self.lock:
            self._before_commit()
            self.active_socket = sock

    def _before_commit(self):
        if self.cancelled.is_set():
            raise ValueError("Pairing cancelled before saving credentials")

    def start_pairing(self, settings):
        from . import pairing
        from .setup import check_directories, check_endpoint, execution_config

        with self.lock:
            if self.record["state"] not in ("choose_pairing", "failed", "cancelled", "interrupted"):
                raise ValueError("Pairing already started; resume this setup")
            if self.worker and self.worker.is_alive():
                raise ValueError("Previous pairing is still stopping; wait before retrying")
            pair_dir = self.directory / "pair"
            # A host commit may precede a lost peer acknowledgement. Do not rotate it on retry.
            server_path = pair_dir / "server.json"
            if server_path.exists() and load(server_path).get("token_sha256"):
                raise ValueError("Pairing may already be saved; reuse its bundle, do not re-pair")
            if (pair_dir / "client.json").exists():
                raise ValueError("Pairing already saved; reuse its bundle")
            if self.record["role"] == "requester":
                previous = self.record.get("settings")
                if previous is not None:
                    # Retry uses the pinned original choices, never a mutable latest backend.
                    if settings and settings != previous:
                        raise ValueError("Project/backend choices are pinned; retry unchanged")
                    settings = previous
                else:
                    settings = {
                        "project": str(Path(settings["project"]).expanduser().resolve(strict=True)),
                        "grant": settings.get("grant", "ro"),
                        "backend": settings.get("backend", "docker"),
                        "allow_host_credentials": settings.get("allow_host_credentials", False),
                        "image": settings.get("image", "python:3.13-slim"),
                        "host": settings.get("host") or pairing.local_address(),
                        "port": int(settings.get("port", 9443)),
                        "advertise": settings.get("advertise", True),
                    }
                    check_directories(settings["project"], pair_dir)
                    check_endpoint(settings["host"], settings["port"])
                    if settings["port"] >= 65535:
                        raise ValueError("Relay port must leave room for the pairing port")
                    execution_config(
                        settings["grant"],
                        settings["backend"],
                        settings["allow_host_credentials"],
                        settings["image"],
                    )
                    self.record["settings"] = settings
            else:
                # The spoken code is ephemeral; never save it in setup.json.
                pairing.parse_code(settings["code"])
                settings = dict(settings)
            self.cancelled.clear()
            self.accepted = False
            self.view.clear()
            self.record.update(state="pairing", relay="not_started")
            self.record.pop("error", None)
            self.save()
            self.worker = threading.Thread(target=self._pair, args=(settings,), daemon=True)
            self.worker.start()

    def _pair(self, settings):
        import socket

        from .pairing_cli import pair_host, pair_join, parse_address

        pair_dir = self.directory / "pair"
        try:
            self._before_commit()
            if self.record["role"] == "requester":
                self.listener = socket.create_server((settings["host"], settings["port"] + 1))
                pair_host(
                    settings["project"],
                    pair_dir,
                    grant=settings["grant"],
                    host=settings["host"],
                    port=settings["port"],
                    execution_backend=settings["backend"],
                    allow_host_credentials=settings["allow_host_credentials"],
                    container_image=settings["image"],
                    advertise=settings["advertise"],
                    listener=self.listener,
                    on_ready=self._ready,
                    on_confirm=self._confirm,
                    before_commit=self._before_commit,
                    out=lambda _: None,
                    cancel=self.cancelled,
                    on_socket=self._set_socket,
                )
                config = pair_dir / "requester" / "client.json"
                relay = "not_started"
            else:
                pair_join(
                    pair_dir,
                    code=settings["code"],
                    address=parse_address(settings["address"]) if settings.get("address") else None,
                    on_confirm=self._confirm,
                    out=lambda _: None,
                    on_socket=self._set_socket,
                )
                config = pair_dir / "client.json"
                relay = "not_checked"
            with self.lock:
                self.record.update(state="paired", config=str(config), relay=relay)
                self.view.clear()
                self.save()
        except Exception as error:
            with self.lock:
                # Even after cancellation, a committed credential needs reconciliation.
                server_path = pair_dir / "server.json"
                committed = server_path.exists() and load(server_path).get("token_sha256")
                self.record["state"] = (
                    "interrupted"
                    if committed
                    else "cancelled"
                    if self.cancelled.is_set()
                    else "failed"
                )
                self.record["error"] = str(error)
                self.view.clear()
                self.save()
        finally:
            self.active_socket = None
            if self.listener:
                self.listener.close()
                self.listener = None

    def check_relay(self):
        import httpx

        with self.lock:
            if self.record["state"] != "paired":
                raise ValueError("Pairing is not ready")
            client = Client(load(self.record["config"]))
            try:
                response = client.http.get(
                    "/healthz",
                    headers={
                        "Authorization": "Bearer "
                        + private_file(client.token_file).read_text().strip()
                    },
                    timeout=1,
                )
                self.record["relay"] = "ready" if response.status_code == 200 else "unavailable"
            except httpx.HTTPError:
                self.record["relay"] = "unavailable"
            finally:
                client.close()
            self.save()
            return self.receipt()

    def start_relay(self):
        import subprocess
        import sys
        import time

        with self.lock:
            config = self.directory / "pair" / "server.json"
            if self.record.get("config") != str(config.parent / "requester/client.json"):
                raise ValueError("The selected bundle does not belong to this setup relay")
            if (
                self.record["state"] != "paired"
                or self.record["role"] != "requester"
                or not config.exists()
            ):
                raise ValueError("Only this setup’s newly paired requester can start its relay")
            if self.check_relay()["relay"] == "ready":
                return self.receipt()
            if self.relay_process is None or self.relay_process.poll() is not None:
                self.relay_process = subprocess.Popen(
                    [sys.executable, "-m", "agent_relay.cli", "serve", "--config", str(config)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            for _ in range(50):
                if self.check_relay()["relay"] == "ready":
                    return self.receipt()
                if self.relay_process.poll() is not None:
                    break
                time.sleep(0.1)
            raise ValueError("Relay did not become ready; check its project/backend and port")

    def cancel(self):
        with self.lock:
            if self.record["state"] == "paired":
                raise ValueError(
                    "Pairing already saved; use role disconnect/exit, not setup cancel"
                )
            self.cancelled.set()
            self.answer.set()
            if self.active_socket:
                import socket

                try:
                    self.active_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            if self.listener:
                import socket

                try:
                    self.listener.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.listener.close()
            self.record["state"] = (
                "cancelling" if self.worker and self.worker.is_alive() else "cancelled"
            )
            self.save()
            return self.receipt()

    def close(self):
        if self.record["state"] != "paired":
            self.cancel()
        if self.relay_process is not None:
            import subprocess

            self.relay_process.terminate()
            try:
                self.relay_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.relay_process.kill()
                self.relay_process.wait()
        if self.client:
            self.client.close()
        if self.worker:
            self.worker.join(timeout=2)
        # Do not release ownership while a worker can still commit credentials.
        if not self.worker or not self.worker.is_alive():
            os.close(self.fd)


class Setups:
    def __init__(self, root):
        self.root = canonical_folder(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.stat().st_uid != os.getuid() or self.root.stat().st_mode & 0o077:
            raise ValueError("Setup directory must be owned and private")
        self.sessions = {}
        self.lock = threading.RLock()
        self.http = None
        self.url = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def begin(self, role, binding_id):
        if role not in ("requester", "responder"):
            raise ValueError("Choose requester or responder role")
        binding_id = _identifier(binding_id, "binding_id")
        setup_id = hashlib.sha256(binding_id.encode()).hexdigest()
        with self.lock:
            if setup_id not in self.sessions:
                directory = self.root / setup_id
                directory.mkdir(mode=0o700, exist_ok=True)
                if directory.is_symlink() or directory.stat().st_mode & 0o077:
                    raise ValueError("Setup directory must be private and not symlinked")
                record_path = directory / "setup.json"
                record = {
                    "setup_id": setup_id,
                    "binding_hash": setup_id,
                    "role": role,
                    "state": "choose_pairing",
                    "relay": "not_started",
                }
                try:
                    write_private(record_path, json.dumps(record, indent=2))
                except FileExistsError:
                    record = json.loads(private_file(record_path).read_text())
                if record["role"] != role:
                    raise ValueError("This binding already has a different role")
                session = Session(self, directory, record)
                self.sessions[setup_id] = session
            elif self.sessions[setup_id].record["role"] != role:
                raise ValueError("This binding already has a different role")
            if self.http is None:
                from .setup_view import start_view

                self.http = start_view(self)
                self.url = f"http://127.0.0.1:{self.http.server_port}"
            return self.sessions[setup_id].receipt()

    def session(self, setup_id, binding_id):
        _identifier(setup_id, "setup_id")
        _identifier(binding_id, "binding_id")
        if hashlib.sha256(binding_id.encode()).hexdigest() != setup_id:
            raise ValueError("Setup does not belong to this chat binding")
        with self.lock:
            if setup_id not in self.sessions:
                record = json.loads(private_file(self.root / setup_id / "setup.json").read_text())
                self.begin(record["role"], binding_id)
            return self.sessions[setup_id]

    def status(self, setup_id, binding_id):
        return self.session(setup_id, binding_id).receipt()

    def role_server(self, setup_id, binding_id):
        return self.session(setup_id, binding_id).role_server()

    def close(self):
        if self.http:
            self.http.shutdown()
            self.http.server_close()
        for session in self.sessions.values():
            session.close()


def build_server(setups):
    server = FastMCP("PeerRelay", instructions=ENTRY, log_level="ERROR")

    @server.prompt(name="requester-mode")
    def requester_prompt() -> str:
        return ENTRY + "\n" + REQUESTER_INSTRUCTIONS

    @server.prompt(name="response-mode")
    def responder_prompt() -> str:
        return ENTRY + "\n" + RESPONDER_INSTRUCTIONS

    @server.tool(structured_output=False)
    async def setup_begin(role: str, binding_id: str) -> dict:
        """Open local setup for this chat; role is requester or responder. No bundle required."""
        return await asyncio.to_thread(setups.begin, role, binding_id)

    @server.tool(structured_output=False)
    async def setup_status(setup_id: str, binding_id: str) -> dict:
        """Resume this chat's exact setup without creating another pair or activating a role."""
        return await asyncio.to_thread(setups.status, setup_id, binding_id)

    @server.tool(structured_output=False)
    async def setup_cancel(setup_id: str, binding_id: str) -> dict:
        """Cancel pending setup. Saved pairings require the role's disconnect/exit controls."""
        session = await asyncio.to_thread(setups.session, setup_id, binding_id)
        return await asyncio.to_thread(session.cancel)

    @server.tool(structured_output=False)
    async def role_tools(setup_id: str, binding_id: str) -> dict:
        """Get this bound role's existing tool schemas and instructions after pairing."""
        bound = await asyncio.to_thread(setups.role_server, setup_id, binding_id)
        return {
            "instructions": bound.instructions,
            "tools": [tool.model_dump(mode="json") for tool in await bound.list_tools()],
        }

    @server.tool(structured_output=False)
    async def role_call(setup_id: str, binding_id: str, tool: str, arguments: dict):
        """Invoke one of this bound role's advertised tools; preserve operation/mode/lease IDs."""
        bound = await asyncio.to_thread(setups.role_server, setup_id, binding_id)
        return await bound.call_tool(tool, arguments)

    return server
