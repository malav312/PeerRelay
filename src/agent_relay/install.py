"""Install project-local MCP configuration and skills without replacing user settings."""

import hashlib
import json
import os
import sys
import tempfile
import tomllib
from pathlib import Path

from .client_setup import _skill
from .pairing_cli import canonical_folder

START = "# BEGIN PeerRelay managed MCP\n"
END = "# END PeerRelay managed MCP\n"


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    # Configuration folders are sometimes symlinks; do not write through them.
    if path.resolve() != path:
        raise ValueError(f"Installation target is symlinked: {path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"Expected a file: {path}")
    return path.read_bytes() if path.exists() else None


def _replace(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".peerrelay-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def install(client, project, state_dir=None, background="off"):
    if client not in ("claude", "codex"):
        raise ValueError("Choose claude or codex")
    if background not in ("off", "claude-channel"):
        raise ValueError("Unsupported background adapter")
    if background == "claude-channel" and client != "claude":
        raise ValueError("Claude channels require the Claude client")
    project = canonical_folder(project)
    if not project.is_dir():
        raise ValueError("Choose an existing project directory")
    if state_dir is None:
        key = hashlib.sha256(str(project).encode()).hexdigest()[:24]
        state_dir = Path.home() / ".local/share/peerrelay/projects" / key / client
    state_dir = canonical_folder(state_dir)
    if state_dir == project or project in state_dir.parents or state_dir in project.parents:
        raise ValueError("Private setup state must be separate and outside the project")
    config_path = project / (".mcp.json" if client == "claude" else ".codex/config.toml")
    manifest_path = (
        project / (".claude" if client == "claude" else ".codex") / "peerrelay-install.json"
    )
    manifest_bytes = _read(manifest_path)
    previous = json.loads(manifest_bytes) if manifest_bytes else {}
    if not isinstance(previous, dict) or (
        previous
        and (
            previous.get("version") != 1
            or previous.get("client") != client
            or not isinstance(previous.get("skills"), dict)
            or not isinstance(previous.get("entry"), dict)
        )
    ):
        raise ValueError("Unrecognized PeerRelay installation manifest")
    args = ["-m", "agent_relay.cli", "bootstrap", "--state-dir", str(state_dir)]
    if background != "off":
        args += ["--background", background]
    args += ["--project", str(project)]
    entry = {"command": sys.executable, "args": args}
    original = _read(config_path)
    text = original.decode() if original is not None else ""
    try:
        data = (json.loads(text) if text else {}) if client == "claude" else tomllib.loads(text)
        key = "mcpServers" if client == "claude" else "mcp_servers"
        if not isinstance(data, dict) or not isinstance(data.get(key, {}), dict):
            raise ValueError("MCP configuration must be an object")
        existing = data.get(key, {}).get("peerrelay")
        if existing is not None and existing not in (entry, previous.get("entry")):
            raise ValueError(
                "Existing peerrelay MCP entry conflicts; preserve or remove it explicitly"
            )
        if client == "claude":
            data.setdefault(key, {})["peerrelay"] = entry
            new_config = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()
            if existing == entry:
                new_config = original
        else:
            block = START + "[mcp_servers.peerrelay]\n"
            block += f"command = {json.dumps(entry['command'], ensure_ascii=False)}\n"
            block += f"args = {json.dumps(args, ensure_ascii=False)}\n" + END
            if existing == entry:
                new_config = original
            elif existing is not None:
                old_block = previous.get("toml_block")
                if not old_block or text.count(old_block) != 1:
                    raise ValueError(
                        "Managed PeerRelay config was modified; preserve it explicitly"
                    )
                new_config = text.replace(old_block, block).encode()
            else:
                new_config = (
                    text + ("\n" if text and not text.endswith("\n") else "") + block
                ).encode()
            parsed = tomllib.loads(new_config.decode())
            if parsed[key]["peerrelay"] != entry:
                raise ValueError("PeerRelay config conflicts with existing TOML")
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, UnicodeError) as error:
        raise ValueError("Invalid native configuration; no files changed") from error

    writes = {config_path: new_config}
    hashes = {}
    skill_root = project / (".claude" if client == "claude" else ".agents") / "skills"
    for role in ("requester-mode", "response-mode"):
        path = skill_root / role / "SKILL.md"
        desired = _skill(client, role).encode()
        current = _read(path)
        relative = str(path.relative_to(project))
        if current is not None and current != desired:
            if _digest(current) != previous.get("skills", {}).get(relative):
                raise ValueError(f"Skill conflict or locally modified file: {path}")
        writes[path] = desired
        hashes[relative] = _digest(desired)
    manifest = dict(version=1, client=client, entry=entry, skills=hashes)
    if client == "codex":
        # Only claim ownership of a block we actually wrote or previously owned.
        manifest["toml_block"] = block if block in new_config.decode() else None
    writes[manifest_path] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
    snapshots = {path: _read(path) for path in writes}
    modes = {path: path.stat().st_mode & 0o777 if path.exists() else 0o600 for path in writes}
    changed = []
    try:
        for path, contents in writes.items():
            if snapshots[path] == contents:
                continue
            # Detect ordinary concurrent edits before replacing any target.
            if _read(path) != snapshots[path]:
                raise ValueError(f"File changed during installation: {path}")
            _replace(path, contents, modes[path])
            changed.append(path)
    except Exception:
        for path in reversed(changed):
            if snapshots[path] is None:
                path.unlink()
            else:
                _replace(path, snapshots[path], modes[path])
        raise
    prefix = "/" if client == "claude" else "$"
    return dict(
        client=client,
        project=str(project),
        config=str(config_path),
        state_dir=str(state_dir),
        connection="not_started",
        background_delivery="configured_unverified" if background != "off" else "manual_follow",
        modes=[prefix + "requester-mode", prefix + "response-mode"],
        next_step="Reload MCP configuration or reopen this project in your client, "
        "accept native project/MCP trust, then invoke a role skill.",
        background_note="Enable the native custom channel and acknowledge a delivery probe."
        if background != "off"
        else "Automatic wake is not enabled in this session; bounded follow is available.",
    )
