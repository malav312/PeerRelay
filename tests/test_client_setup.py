import json
import os
import subprocess
import sys

import pytest

from agent_relay.setup import initialize


def run_setup(config, directory, client="claude"):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_relay.cli",
            "client-setup",
            "--client",
            client,
            "--config",
            str(config),
            "--directory",
            str(directory),
        ],
        capture_output=True,
        text=True,
    )


@pytest.fixture
def pair(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    initialize(project, tmp_path / "pair", "127.0.0.1", 9443)
    return tmp_path / "pair"


@pytest.mark.parametrize("client", ["claude", "codex"])
@pytest.mark.parametrize(
    "role,mode", [("requester", "requester-mode"), ("responder", "response-mode")]
)
def test_setup_installs_role_without_copying_credentials_or_claiming_connection(
    tmp_path,
    pair,
    client,
    role,
    mode,
):
    config = pair / role / "client.json"
    directory = tmp_path / "chat space ' $(false)"
    result = run_setup(config, directory, client)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["role"] == role
    assert report["connection"] == "not_started"
    assert report["native_acceptance"] == "pending"
    skill_root = ".claude" if client == "claude" else ".agents"
    assert f"name: {mode}" in (directory / skill_root / "skills" / mode / "SKILL.md").read_text()
    mcp = json.loads((directory / "mcp.json").read_text())["mcpServers"]["peerrelay"]
    # The generated process is executable and exposes the real CLI.
    check = subprocess.run(
        [mcp["command"], *mcp["args"][:2], "--help"], capture_output=True, text=True
    )
    assert check.returncode == 0, check.stderr
    assert mcp["args"][-1] == str(config)
    secret = (pair / role / f"{role}.token").read_text().strip()
    for artifact in directory.rglob("*"):
        assert artifact.stat().st_mode & 0o077 == 0
        if artifact.is_file():
            assert secret not in artifact.read_text()
    if client == "codex":
        assert not (directory / "start").exists()


def test_setup_refuses_existing_and_symlinked_destinations(tmp_path, pair):
    destination = tmp_path / "existing"
    destination.mkdir()
    marker = destination / "marker"
    marker.write_text("keep")
    link = tmp_path / "link"
    link.symlink_to(destination)
    for path in (destination, link):
        result = run_setup(pair / "responder/client.json", path)
        assert result.returncode != 0
        assert marker.read_text() == "keep"
        assert list(destination.iterdir()) == [marker]


def test_setup_rejects_invalid_role_and_insecure_credentials(tmp_path, pair):
    config = pair / "responder/client.json"
    original = json.loads(config.read_text())
    config.write_text(json.dumps({**original, "role": "server"}))
    assert run_setup(config, tmp_path / "invalid").returncode != 0
    assert not (tmp_path / "invalid").exists()
    config.write_text(json.dumps(original))
    (pair / "responder/responder.token").chmod(0o644)
    assert run_setup(config, tmp_path / "insecure").returncode != 0
    assert not (tmp_path / "insecure").exists()


def test_launcher_refuses_overrides_and_missing_native_controls(tmp_path, pair):
    directory = tmp_path / "chat space ' $(false)"
    assert run_setup(pair / "responder/client.json", directory).returncode == 0
    start = directory / "start"
    for extra in (["--tools", "Bash"], ["--help"]):
        result = subprocess.run([str(start), *extra], capture_output=True, text=True)
        assert result.returncode != 0
    native = tmp_path / "bin"
    native.mkdir()
    client = native / "claude"
    client.write_text("#!/bin/sh\necho unsupported\n")
    client.chmod(0o700)
    result = subprocess.run(
        [str(start)], env={**os.environ, "PATH": str(native)}, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "required" in result.stderr.lower()


def test_launcher_passes_only_restricted_arguments_to_native_client(tmp_path, pair):
    directory = tmp_path / "chat space ' $(false)"
    assert run_setup(pair / "requester/client.json", directory).returncode == 0
    native = tmp_path / "bin"
    native.mkdir()
    client = native / "claude"
    # Boundary double: capture argv, without running a model/provider or reconnecting.
    client.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        "if '--help' in sys.argv:\n"
        " print('--restricted --tools --strict-mcp-config --mcp-config --no-chrome '"
        "'--permission-mode --allowedTools')\n"
        "else: print(json.dumps({'args':sys.argv[1:], 'cwd':os.getcwd()}))\n"
    )
    client.chmod(0o700)
    result = subprocess.run(
        [str(directory / "start")],
        env={**os.environ, "PATH": str(native)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    launched = json.loads(result.stdout)
    args = launched["args"]
    assert launched["cwd"] == str(directory)
    assert args[args.index("--tools") + 1] == ""
    assert "--restricted" in args and "--strict-mcp-config" in args
    assert args[args.index("--mcp-config") + 1] == str(directory / "mcp.json")
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    assert args[args.index("--allowedTools") + 1] == "mcp__peerrelay__*"


def test_channel_setup_is_explicit_and_never_claims_verified(tmp_path, pair):
    from agent_relay.client_setup import prepare

    directory = tmp_path / "channel"
    report = prepare(
        "claude", pair / "requester/client.json", directory, background="claude-channel"
    )
    assert report["background"] == "claude-channel"
    assert report["background_delivery"] == "configured_unverified"
    args = json.loads((directory / "mcp.json").read_text())["mcpServers"]["peerrelay"]["args"]
    assert args[-2:] == ["--background", "claude-channel"]
    assert "confirmation" in (directory / "NEXT-STEPS.txt").read_text()
    for client, background in [("codex", "claude-channel"), ("claude", "unsupported")]:
        with pytest.raises(ValueError):
            prepare(
                client, pair / "requester/client.json", tmp_path / "invalid", background=background
            )
        assert not (tmp_path / "invalid").exists()


@pytest.mark.parametrize("supported", [False, True])
def test_channel_launcher_handles_hidden_flag_and_native_rejection(tmp_path, pair, supported):
    from agent_relay.client_setup import CLAUDE_FLAGS, prepare

    directory = tmp_path / "channel"
    prepare("claude", pair / "requester/client.json", directory, background="claude-channel")
    native = tmp_path / "bin"
    native.mkdir()
    client = native / "claude"
    flags = " ".join(CLAUDE_FLAGS)
    client.write_text(
        f"#!{sys.executable}\nimport json, sys\n"
        f"if '--help' in sys.argv: print({flags!r})\n"
        f"elif not {supported!r}:\n"
        " print('Unknown option --dangerously-load-development-channels', file=sys.stderr)\n"
        " sys.exit(2)\n"
        "else: print(json.dumps(sys.argv[1:]))\n"
    )
    client.chmod(0o700)
    result = subprocess.run(
        [str(directory / "start")],
        env={**os.environ, "PATH": str(native)},
        capture_output=True,
        text=True,
    )
    if not supported:
        assert result.returncode != 0
        assert "channel" in result.stderr.lower()
    else:
        assert result.returncode == 0, result.stderr
        args = json.loads(result.stdout)
        assert args[args.index("--dangerously-load-development-channels") + 1] == "server:peerrelay"
        assert args[args.index("--tools") + 1] == ""
        assert args[args.index("--permission-mode") + 1] == "dontAsk"
