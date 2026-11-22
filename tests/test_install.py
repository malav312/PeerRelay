import json
import sys
import tomllib
from pathlib import Path

import pytest

from agent_relay import client_setup


def install(*args, **kwargs):
    from agent_relay.install import install as run

    return run(*args, **kwargs)


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_install_existing_project_preserves_settings_and_is_repeatable(tmp_path, client):
    project = tmp_path / 'project "quoted"'
    project.mkdir()
    marker = project / "work.txt"
    marker.write_text("unfinished work")
    if client == "claude":
        config = project / ".mcp.json"
        config.write_text(
            json.dumps({"extra": True, "mcpServers": {"other": {"command": "other"}}})
        )
    else:
        config = project / ".codex/config.toml"
        config.parent.mkdir()
        config.write_text(
            '# user comment\nmodel = "custom"\n[mcp_servers.other]\ncommand="other"\n'
        )
    state = tmp_path / "private"
    report = install(client, project, state)
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    install(client, project, state)
    assert before == {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    assert marker.read_text() == "unfinished work"
    data = (
        json.loads(config.read_text()) if client == "claude" else tomllib.loads(config.read_text())
    )
    entry = data["mcpServers" if client == "claude" else "mcp_servers"]["peerrelay"]
    assert entry["command"] == sys.executable
    assert entry["args"][-2:] == ["--project", str(project)]
    assert (
        data["mcpServers" if client == "claude" else "mcp_servers"]["other"]["command"] == "other"
    )
    if client == "codex":
        assert config.read_text().startswith('# user comment\nmodel = "custom"')
    assert report["connection"] == "not_started"
    assert report["background_delivery"] == "manual_follow"
    folder = ".claude" if client == "claude" else ".agents"
    for role in ("requester-mode", "response-mode"):
        assert (project / folder / "skills" / role / "SKILL.md").read_text() == client_setup._skill(
            client, role
        )
    assert not state.exists()  # installation does not start the relay or pair


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_conflicting_skill_does_not_partially_install(tmp_path, client):
    folder = ".claude" if client == "claude" else ".agents"
    project = tmp_path / "project"
    skill = project / folder / "skills/response-mode/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("my own skill")
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="conflict|modified"):
        install(client, project, tmp_path / "state")
    assert before == {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_owned_skills_update_but_user_edits_are_preserved(tmp_path, monkeypatch, client):
    project = tmp_path / "project"
    project.mkdir()
    install(client, project, tmp_path / "state")
    from agent_relay import install as module

    original = module._skill
    monkeypatch.setattr(module, "_skill", lambda c, m: original(c, m) + "\nUpdated.\n")
    install(client, project, tmp_path / "state")
    folder = ".claude" if client == "claude" else ".agents"
    skill = project / folder / "skills/requester-mode/SKILL.md"
    assert skill.read_text().endswith("Updated.\n")
    skill.write_text("user edit")
    with pytest.raises(ValueError, match="modified|conflict"):
        install(client, project, tmp_path / "state")
    assert skill.read_text() == "user edit"


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_existing_peerrelay_entry_is_not_replaced(tmp_path, client):
    project = tmp_path / "project"
    project.mkdir()
    config = project / (".mcp.json" if client == "claude" else ".codex/config.toml")
    config.parent.mkdir(exist_ok=True)
    config.write_text(
        '{"mcpServers":{"peerrelay":{"command":"mine"}}}'
        if client == "claude"
        else '[mcp_servers.peerrelay]\ncommand="mine"\n'
    )
    before = config.read_bytes()
    with pytest.raises(ValueError, match="conflict|existing"):
        install(client, project, tmp_path / "state")
    assert config.read_bytes() == before


def test_installer_rejects_state_in_project_and_symlinked_targets(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(ValueError, match="separate|outside"):
        install("claude", project, project / "state")
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".claude").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        install("claude", project, tmp_path / "state")
    assert not (project / ".mcp.json").exists()
    assert not list(outside.iterdir())


def test_claude_channels_are_explicit_and_not_enabled_for_codex(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    report = install("claude", project, tmp_path / "state", background="claude-channel")
    args = json.loads((project / ".mcp.json").read_text())["mcpServers"]["peerrelay"]["args"]
    assert "--background" in args and "claude-channel" in args
    assert report["background_delivery"] == "configured_unverified"
    with pytest.raises(ValueError, match="Claude"):
        install("codex", project, tmp_path / "state", background="claude-channel")


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_invalid_native_config_changes_nothing(tmp_path, client):
    project = tmp_path / "project"
    config = project / (".mcp.json" if client == "claude" else ".codex/config.toml")
    config.parent.mkdir(parents=True)
    config.write_text("this is not configuration {")
    with pytest.raises(ValueError, match="Invalid"):
        install(client, project, tmp_path / "state")
    assert [p for p in project.rglob("*") if p.is_file()] == [config]


def test_install_cli_registers_default_private_state_without_launching(
    tmp_path, monkeypatch, capsys
):
    from agent_relay.cli import main

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(
        sys, "argv", ["peer-relay", "install", "--client", "codex", "--project", str(project)]
    )
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["connection"] == "not_started"
    assert result["modes"] == ["$requester-mode", "$response-mode"]
    assert str(tmp_path / "home") in result["state_dir"]
    assert not Path(result["state_dir"]).exists()


def test_changing_owned_background_preserves_other_settings(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    install("claude", project, tmp_path / "state")
    path = project / ".mcp.json"
    data = json.loads(path.read_text())
    data["mcpServers"]["another"] = {"command": "example"}
    path.write_text(json.dumps(data))
    install("claude", project, tmp_path / "state", background="claude-channel")
    updated = json.loads(path.read_text())
    assert updated["mcpServers"]["another"] == {"command": "example"}
    assert "claude-channel" in updated["mcpServers"]["peerrelay"]["args"]


def test_failed_write_restores_existing_configuration(tmp_path, monkeypatch):
    from agent_relay import install as module

    project = tmp_path / "project"
    project.mkdir()
    config = project / ".mcp.json"
    config.write_text('{"mcpServers":{"other":{"command":"keep"}}}')
    before = config.read_bytes()
    replace = module._replace
    calls = 0

    def fail_once(path, contents, mode):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("disk write failed")
        return replace(path, contents, mode)

    monkeypatch.setattr(module, "_replace", fail_once)
    with pytest.raises(OSError, match="disk write"):
        install("claude", project, tmp_path / "state")
    assert config.read_bytes() == before
    assert [p for p in project.rglob("*") if p.is_file()] == [config]


def test_invalid_manifest_fails_without_modification(tmp_path):
    project = tmp_path / "project"
    path = project / ".claude/peerrelay-install.json"
    path.parent.mkdir(parents=True)
    path.write_text('["not an install manifest"]')
    with pytest.raises(ValueError, match="manifest"):
        install("claude", project, tmp_path / "state")
    assert not (project / ".mcp.json").exists()
