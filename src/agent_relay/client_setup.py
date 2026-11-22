"""Prepare native chat setup without touching global settings or connecting a lease."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .client import Client, load, private_file

CLAUDE_FLAGS = (
    "--restricted",
    "--tools",
    "--strict-mcp-config",
    "--mcp-config",
    "--no-chrome",
    "--permission-mode",
    "--allowedTools",
)


def _write(path, content, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x") as stream:
        stream.write(content)
    path.chmod(0o700 if executable else 0o600)


def _skill(client, mode):
    package = Path(__file__).parent
    relative = Path(client) / "skills" / mode / "SKILL.md"
    for root in (package / "_skills", package.parent.parent / "plugins"):
        path = root / relative
        if path.is_file():
            return path.read_text()
    raise ValueError("Role skill is missing; reinstall PeerRelay from a complete package")


def prepare(client, config_path, directory, background="off"):
    if client not in ("claude", "codex"):
        raise ValueError("Choose claude or codex")
    if background not in ("off", "claude-channel"):
        raise ValueError("Unsupported background adapter")
    if background == "claude-channel" and client != "claude":
        raise ValueError("Claude channels require the Claude client")
    config_path = private_file(Path(config_path).expanduser().absolute())
    config = load(config_path)
    # Validates role, URL, certificate and credential permissions without network I/O.
    with_client = Client(config)
    with_client.close()
    mode = "requester-mode" if config["role"] == "requester" else "response-mode"
    skill = _skill(client, mode)
    directory = Path(directory).expanduser().absolute()
    if directory.exists() or directory.is_symlink():
        raise ValueError("Chat directory already exists; choose a new private directory")
    if directory.parent.resolve() != directory.parent or not directory.parent.is_dir():
        raise ValueError("Chat directory needs an existing, non-symlinked parent")
    directory.mkdir(mode=0o700)
    args = ["-m", "agent_relay.cli", "mcp", "--config", str(config_path)]
    if background == "claude-channel":
        args.extend(["--background", background])
    mcp = {"mcpServers": {"peerrelay": {"command": sys.executable, "args": args}}}
    _write(directory / "mcp.json", json.dumps(mcp, indent=2) + "\n")
    skill_root = ".claude" if client == "claude" else ".agents"
    _write(directory / skill_root / "skills" / mode / "SKILL.md", skill)
    report = {
        "client": client,
        "role": config["role"],
        "config": str(config_path),
        "directory": str(directory),
        "connection": "not_started",
        "native_acceptance": "pending",
        "local_tools": "launch_controls_configured" if client == "claude" else "unverified",
        "background": background,
        "background_delivery": "configured_unverified" if background != "off" else "off",
    }
    _write(directory / "setup.json", json.dumps(report, indent=2) + "\n")
    if client == "claude":
        command = shlex.join(
            [
                sys.executable,
                "-m",
                "agent_relay.cli",
                "client-start",
                "--directory",
                str(directory),
            ]
        )
        _write(
            directory / "start",
            '#!/bin/sh\nif [ "$#" -ne 0 ]; then\n'
            "  echo 'This launcher accepts no additional arguments' >&2\n  exit 2\nfi\n"
            f"exec {command}\n",
            executable=True,
        )
        next_step = (
            "Run ./start in this directory to open a NEW Claude Code session.\n"
            "The launcher removes built-in tools and selects only this MCP server.\n"
            "Inspect /mcp and complete the native acceptance checklist before use.\n"
        )
    else:
        add = shlex.join(["codex", "mcp", "add", "peerrelay", "--", sys.executable, *args])
        next_step = (
            "Codex local-tool restrictions are UNVERIFIED; no launcher is generated.\n"
            "mcp.json contains the MCP snippet. After enforcing local tool restrictions\n"
            "or OS isolation, install it in your selected client. For CLI, this command\n"
            "changes your global PeerRelay MCP entry (review any existing entry first):\n"
            f"{add}\n"
            "Restart the client and open this directory. A shell-disable toggle or\n"
            "read-only sandbox alone does not establish local-read isolation.\n"
        )
    if background == "claude-channel":
        next_step += (
            "Claude channels are a research preview. Complete native human confirmation;\n"
            "organization policy still applies. Run probe_background for your mode and\n"
            "acknowledge the received event. Delivery stays unverified until that ack.\n"
            "If unavailable, use bounded follow/resume; never claim automatic delivery.\n"
        )
    _write(
        directory / "NEXT-STEPS.txt",
        next_step
        + (
            f"Invoke {'/' if client == 'claude' else '$'}{mode} explicitly.\n"
            "Activate Bob first, then Alice. Setup has NOT connected either role.\n"
            "Alice: verify the relay reports the intended registered checkout/branch.\n"
            "Test remote write, command, question/answer, and disconnect on disposable data.\n"
            "Test local read/write/command denial using harmless markers on EACH laptop.\n"
            "See docs/client-sessions.md in PeerRelay for the full acceptance checklist.\n"
        ),
    )
    return report


def start(directory):
    directory = Path(directory).expanduser().absolute()
    if directory.resolve() != directory or directory.stat().st_mode & 0o077:
        raise ValueError("Use the original private chat directory")
    report = json.loads(private_file(directory / "setup.json").read_text())
    if report["client"] != "claude":
        raise ValueError("Codex local-tool restrictions are unverified; automatic launch refused")
    if report["directory"] != str(directory):
        raise ValueError("Chat directory moved; generate a new setup")
    config = load(report["config"])
    if config["role"] != report["role"]:
        raise ValueError("Role changed; generate a new setup")
    # Ensure the MCP process validates the current bundle before native startup.
    with_client = Client(config)
    with_client.close()
    mcp_path = private_file(directory / "mcp.json")
    executable = shutil.which("claude")
    if not executable:
        raise ValueError("Claude Code is required; install it and sign in normally first")
    help_result = subprocess.run([executable, "--help"], capture_output=True, text=True, timeout=15)
    if help_result.returncode or any(flag not in help_result.stdout for flag in CLAUDE_FLAGS):
        raise ValueError("Installed Claude Code lacks required restriction flags; launch refused")
    background = report.get("background", "off")
    if background not in ("off", "claude-channel"):
        raise ValueError("Unsupported background adapter; generate a new setup")
    channel_args = []
    if background == "claude-channel":
        flag = "--dangerously-load-development-channels"
        # Claude hides this documented option from --help. Let its native parser
        # reject unsupported versions; a successful launch still is not receipt evidence.
        channel_args = [flag, "server:peerrelay"]
    os.chdir(directory)
    os.execv(
        executable,
        [
            executable,
            "--restricted",
            "--tools",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            str(mcp_path),
            "--no-chrome",
            "--permission-mode",
            "dontAsk",
            "--allowedTools",
            "mcp__peerrelay__*",
            *channel_args,
        ],
    )


def prepare_bootstrap(client, state_dir, directory):
    """Package the stable MCP registration and BOTH entry skills before a pair exists."""
    from .pairing_cli import canonical_folder

    if client not in ("claude", "codex"):
        raise ValueError("Choose claude or codex")
    state_dir = canonical_folder(state_dir)
    directory = canonical_folder(directory)
    if directory.exists():
        raise ValueError("Chat directory already exists; choose a new private directory")
    if not directory.parent.is_dir():
        raise ValueError("Chat directory needs an existing parent")
    skills = {mode: _skill(client, mode) for mode in ("requester-mode", "response-mode")}
    directory.mkdir(mode=0o700)
    args = ["-m", "agent_relay.cli", "bootstrap", "--state-dir", str(state_dir)]
    _write(
        directory / "mcp.json",
        json.dumps(
            {"mcpServers": {"peerrelay": {"command": sys.executable, "args": args}}}, indent=2
        )
        + "\n",
    )
    skill_root = ".claude" if client == "claude" else ".agents"
    for mode, skill in skills.items():
        _write(directory / skill_root / "skills" / mode / "SKILL.md", skill)
    report = {
        "client": client,
        "role": "unbound",
        "directory": str(directory),
        "state_dir": str(state_dir),
        "connection": "not_started",
        "native_acceptance": "pending",
    }
    _write(directory / "bootstrap.json", json.dumps(report, indent=2) + "\n")
    _write(
        directory / "NEXT-STEPS.txt",
        "Install mcp.json in your selected native client; keep the entry skills together.\n"
        "This does not edit global settings or replace a running integration.\n"
        "Open this directory in a fresh chat and invoke requester-mode or response-mode.\n"
        "Pairing takes place in the local setup URL. Never paste codes or credentials into chat.\n"
        "Preserve the returned setup_id and chat binding_id on resume.\n"
        "Native local-tool restrictions and automatic delivery still need acceptance.\n",
    )
    return report
