import json
from pathlib import Path


def test_both_client_packages_have_modes_and_safe_entrypoints():
    for client in ("codex", "claude"):
        root = Path("plugins") / client
        manifest = json.loads((root / f".{client}-plugin/plugin.json").read_text())
        assert manifest["name"] == "peerrelay"
        for mode in ("requester-mode", "response-mode"):
            text = (root / "skills" / mode / "SKILL.md").read_text()
            assert f"name: {mode}" in text
            assert "description:" in text
            assert "PeerRelay" in text
            assert len(text) < 5000
    for mode in ("requester-mode", "response-mode"):
        assert (Path("plugins/codex/skills") / mode / "SKILL.md").read_text() == (
            Path("plugins/claude/skills") / mode / "SKILL.md"
        ).read_text()
