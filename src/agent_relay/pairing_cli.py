"""`peer-relay pair` (Alice) and `peer-relay join` (Bob): spoken-code LAN pairing."""

import fcntl
import json
import os
import secrets
import socket
import sys
from contextlib import contextmanager
from pathlib import Path

from . import pairing
from .client import load, private_file
from .setup import client_config, execution_config, initialize_host, write_private, write_replace

ACCESS = {
    "ro": "READ-ONLY: can read and search project files",
    "rw": "PROPOSALS: can read files and propose changes you approve locally",
    "exec": "EXEC: can run commands as {user}'s OS user on {host} (not a sandbox)",
}


def describe(grant, host, user="the host", backend=None):
    if grant == "exec" and backend == "docker":
        return "EXEC: project-only Docker containers, no network or host credentials"
    if grant == "exec":
        return ACCESS[grant].format(user=user, host=host) + "; relay/provider credentials exposed"
    return ACCESS[grant].format(user=user, host=host)


def yes(ask, prompt):
    try:
        return ask(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def boxed(text):
    inner = f"   {text}   "
    bar = "─" * len(inner)
    return f"  ┌{bar}┐\n  │{inner}│\n  └{bar}┘"


@contextmanager
def pairing_guard(server_path):
    """Hold the same owner lock as serve throughout the pairing transaction."""
    config = load(server_path)
    state, root = Path(config["state_dir"]).absolute(), Path(config["root"]).resolve(strict=True)
    if root == state or root in state.parents or state in root.parents:
        raise ValueError("Project and private state must be separate")
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state.resolve() != state or state.stat().st_uid != os.getuid():
        raise ValueError("State must be an owned directory without symlinks")
    os.chmod(state, 0o700)
    fd = os.open(state / "relay.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Stop `peer-relay serve` or the other pairing session first") from None
        # Read after acquiring the lock; a preceding pair may have just replaced credentials.
        yield json.loads(private_file(server_path).read_text())
    finally:
        os.close(fd)


def canonical_folder(path):
    """Resolve symlinks in the user-typed parent (e.g. macOS /tmp); the folder itself must
    not be a symlink. Stored paths are then symlink-free, as private_file requires."""
    path = Path(path).expanduser().absolute()
    if path.is_symlink():
        raise ValueError("The pairing folder itself must not be a symlink")
    return path.parent.resolve() / path.name


def pair_host(
    project,
    pair_dir,
    grant=None,
    host=None,
    port=9443,
    name=None,
    advertise=True,
    code=None,
    listener=None,
    ask=input,
    out=print,
    window=pairing.WINDOW,
    execution_backend=None,
    allow_host_credentials=None,
    container_image=None,
    on_ready=None,
    on_confirm=None,
    before_commit=None,
    cancel=None,
    on_socket=None,
):
    host = host or pairing.local_address()
    name = pairing.display_name(name or pairing.default_name())
    pair_dir = canonical_folder(pair_dir)
    server_path = pair_dir / "server.json"
    if not server_path.exists():
        initialize_host(
            project,
            pair_dir,
            host,
            port,
            grant or "ro",
            execution_backend=execution_backend or "docker",
            allow_host_credentials=allow_host_credentials or False,
            container_image=container_image or "python:3.13-slim",
        )
    with pairing_guard(server_path) as raw:
        if grant is not None and grant != raw.get("grant", "rw"):
            raise ValueError("This pair already has a different access level; use a new --pair-dir")
        backend = raw.get("execution_backend")
        if raw.get("grant") == "exec":
            if not isinstance(backend, dict):
                raise ValueError(
                    "Exec requires explicit backend configuration; use a new --pair-dir"
                )
            execution_config(
                raw["grant"],
                backend.get("kind"),
                backend.get("allow_host_credentials"),
                backend.get("image"),
            )
        for key, value in (
            ("kind", execution_backend),
            ("allow_host_credentials", allow_host_credentials),
            ("image", container_image),
        ):
            if value is not None and (backend or {}).get(key) != value:
                raise ValueError(
                    "This pair has a different execution backend; use a new --pair-dir"
                )
        if Path(project).resolve() != Path(raw["root"]):
            raise ValueError("This pair is registered to a different project")
        grant = raw.get("grant", "rw")
        backend_kind = raw.get("execution_backend", {}).get("kind")
        relay_port = raw["port"]
        certificate = (pair_dir / raw["ca_file"]).read_text()
        user = os.environ.get("USER", "your")

        own = listener is None
        if own:
            listener = socket.create_server((host, relay_port + 1))
        advertisement = None
        try:
            bound = listener.getsockname()
            if code is None:
                taken = []
                if advertise:
                    taken = [e["channel"] for e in pairing.discover(1.5)]
                code = pairing.new_code(taken)
            channel, code = pairing.parse_code(code)
            if advertise:
                advertisement = pairing.Advertisement(bound[0], bound[1], channel, name)

            out("")
            out("PeerRelay · pair a responder")
            out(f"  Project   {Path(raw['root']).name}   ({raw['root']})")
            out(f"  Access    {describe(grant, name, user, backend_kind)}")
            out(f"  This Mac  {name} · {bound[0]}")
            out("")
            out(boxed(f"Pairing code:  {code}"))
            out("")
            out("  Read it aloud to the other person. They run:  peer-relay join --dir <folder>")
            how = "advertising on this network" if advertise else "not advertised"
            out(f"  Expires in {window // 60} min · {pairing.ATTEMPTS} attempts · {how}")
            out(f"  If they can't find you:  peer-relay join --address {bound[0]}:{bound[1]}")
            out("")
            out("  Waiting for a device…")

            if on_ready is not None:
                on_ready(
                    {
                        "code": code,
                        "address": f"{bound[0]}:{bound[1]}",
                        "project": raw["root"],
                        "grant": grant,
                        "execution_backend": backend_kind,
                    }
                )

            def confirm(peer):
                if on_confirm is not None:
                    return on_confirm({**peer, "grant": grant, "execution_backend": backend_kind})
                out(f"  → “{peer['device']}” ({peer['address']}) entered the right code.")
                out(f"    Check number:  {peer['check']}    ← must match their screen")
                return yes(ask, f"    Allow “{peer['device']}” with {grant.upper()} access? [y/N] ")

            def commit(peer):
                if before_commit is not None:
                    before_commit()
                current = json.loads(private_file(server_path).read_text())
                current["tokens"].pop("responder", None)  # A re-pair replaces any responder.
                current["token_sha256"] = {"responder": peer["token_sha256"]}
                current["responder"] = {
                    "name": peer["device"],
                    "address": peer["address"],
                    "paired_at": pairing.now(),
                }
                current["host"] = bound[0]
                write_replace(server_path, json.dumps(current, indent=2))
                requester = pair_dir / "requester" / "client.json"
                write_replace(
                    requester,
                    client_config(
                        "requester",
                        f"https://{bound[0]}:{relay_port}",
                        "requester.token",
                        pin=True,
                    ),
                )

            offer = {
                "host_name": name,
                "project": Path(raw["root"]).name[:128] or "project",
                "grant": grant,
                "execution_backend": backend_kind,
                "user": user[:64],
                "relay_port": relay_port,
                "certificate": certificate,
            }
            peer = pairing.host(
                listener,
                code,
                offer,
                confirm,
                commit,
                log=out,
                window=window,
                cancel=cancel,
                on_socket=on_socket,
            )
        finally:
            if advertisement is not None:
                advertisement.close()
            if own:
                listener.close()
        out("")
        out(f"  ✓ Paired with “{peer['device']}”. Pairing window closed.")
        out("  Start the relay:")
        out(f"      peer-relay serve --config {server_path}")
        out("  Requester MCP config:")
        out(f"      {pair_dir / 'requester' / 'client.json'}")
        return peer


def parse_address(text):
    host, _, port = text.rpartition(":")
    if not host or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("Use --address HOST:PORT as printed on the other screen")
    return host, int(port)


def pair_join(
    directory,
    code=None,
    address=None,
    name=None,
    ask=input,
    out=print,
    discover_seconds=6.0,
    on_confirm=None,
    on_socket=None,
):
    directory = canonical_folder(directory)
    if directory.exists():
        raise ValueError("Use a new folder for this pairing (it must not exist yet)")
    name = pairing.display_name(name or pairing.default_name())
    out("")
    out("PeerRelay · join as responder")
    channel, code = pairing.parse_code(code or ask("  Pairing code: "))
    if address is not None:
        candidates = [{"name": address, "address": address[0], "port": address[1]}]
    else:
        out(f"  Looking for channel {channel} on this network…")
        candidates = pairing.discover(discover_seconds, channel)
        if not candidates:
            raise pairing.PairingFailed(
                "No device is pairing on this channel. Check you're on the same Wi-Fi, "
                "or use --address HOST:PORT from the other screen (some networks block mDNS)."
            )
    token = secrets.token_urlsafe(48)
    last = None
    for candidate in candidates:
        out(f"  Found “{candidate['name']}” at {candidate['address']}. Checking code…")

        def confirm(offer, check):
            if on_confirm is not None:
                return on_confirm(
                    {
                        key: value
                        for key, value in {**offer, "check": check}.items()
                        if key != "certificate"
                    }
                )
            out("  ✓ Code accepted.")
            out(f"    Device    {offer['host_name']}")
            out(f"    Project   {offer['project']}")
            access = describe(
                offer["grant"], offer["host_name"], offer["user"], offer.get("execution_backend")
            )
            out(f"    Access    {access}")
            out(f"    Check number:  {check}    ← must match their screen")
            accepted = yes(ask, "    Accept this pairing? [y/N] ")
            if accepted:
                out("  Waiting for the other person to confirm…")
            return accepted

        try:
            offer = pairing.join(
                (candidate["address"], candidate["port"]),
                code,
                name,
                token,
                confirm,
                on_socket=on_socket,
            )
            break
        except pairing.WrongCode as error:
            last = error
            # The final failure is reported once by the caller; only narrate retries.
            if candidate is not candidates[-1]:
                out(f"  ✗ {error}; trying the next device on this channel…")
    else:
        raise last or pairing.PairingFailed("Pairing failed")
    directory.mkdir(mode=0o700, parents=True)
    write_private(directory / "gateway.crt", offer["certificate"])
    write_private(directory / "responder.token", token)
    write_private(
        directory / "client.json",
        client_config(
            "responder",
            f"https://{candidate['address']}:{offer['relay_port']}",
            "responder.token",
            pin=True,
        ),
    )
    executable = Path(sys.executable).parent / "peer-relay"
    out("")
    out(f"  ✓ Paired with “{offer['host_name']}”. Saved to {directory}")
    out("  Add this to your MCP config:")
    snippet = {
        "mcpServers": {
            "peerrelay": {
                "command": str(executable),
                "args": ["mcp", "--config", str(directory / "client.json")],
            }
        }
    }
    for line in json.dumps(snippet, indent=2).splitlines():
        out("    " + line)
    return offer
