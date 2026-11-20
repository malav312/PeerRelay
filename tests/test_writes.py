import os

import pytest

from agent_relay.filesystem import Filesystem
from agent_relay.schemas import Tool


@pytest.fixture
def fs(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "hello.txt").write_text("before\n")
    value = Filesystem({"project": {"path": str(root), "mode": "rw"}}, str(tmp_path / "state"))
    yield value, root
    value.close()


def replacement(fs, content="after\n"):
    version = fs.read(Tool(kind="read", root="project", path="hello.txt"))["version"]
    return Tool(
        kind="propose_replace",
        root="project",
        path="hello.txt",
        content=content,
        expected_version=version,
    )


def test_proposal_does_not_write_apply_writes_once(fs):
    value, root = fs
    tool = replacement(value)
    proposal = value.execute("proposal", tool.model_dump(), {"project": "rw"})
    assert proposal["status"] == "proposed"
    assert (root / "hello.txt").read_text() == "before\n"
    receipt = value.apply("approval", tool, {"project": "rw"})
    assert receipt["status"] == "applied"
    assert (root / "hello.txt").read_text() == "after\n"
    inode = (root / "hello.txt").stat().st_ino
    assert value.apply("approval", tool, {"project": "rw"}) == receipt
    assert (root / "hello.txt").stat().st_ino == inode


def test_changed_file_is_preserved(fs):
    value, root = fs
    tool = replacement(value)
    (root / "hello.txt").write_text("local edit")
    assert value.apply("approval", tool, {"project": "rw"})["status"] == "conflict"
    assert (root / "hello.txt").read_text() == "local edit"


def test_create_absence_and_readonly_grant(fs):
    value, root = fs
    tool = Tool(
        kind="create_file", root="project", path="new.txt", content="new", expected_version="absent"
    )
    assert value.apply("no", tool, {"project": "ro"})["status"] == "denied"
    assert value.apply("yes", tool, {"project": "rw"})["status"] == "applied"
    assert (root / "new.txt").read_text() == "new"
    assert value.apply("different", tool, {"project": "rw"})["status"] == "conflict"


def test_symlink_hardlink_and_traversal_refused(fs):
    value, root = fs
    tool = replacement(value)
    outside = root.parent / "outside"
    outside.write_text("private")
    (root / "hello.txt").unlink()
    (root / "hello.txt").symlink_to(outside)
    assert value.apply("symlink", tool, {"project": "rw"})["status"] != "applied"
    assert outside.read_text() == "private"
    (root / "hello.txt").unlink()
    os.link(outside, root / "hello.txt")
    assert value.apply("hardlink", tool, {"project": "rw"})["status"] != "applied"
    tool = Tool(
        kind="create_file",
        root="project",
        path="../escape.txt",
        content="bad",
        expected_version="absent",
    )
    assert value.apply("escape", tool, {"project": "rw"})["status"] == "denied"
    assert not (root.parent / "escape.txt").exists()


def test_crash_after_effect_never_replays(fs, monkeypatch):
    value, root = fs
    tool = replacement(value)
    original = os.replace

    def uncertain(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("lost durability confirmation")

    monkeypatch.setattr(os, "replace", uncertain)
    assert value.apply("approval", tool, {"project": "rw"})["status"] == "unknown"
    assert (root / "hello.txt").read_text() == "after\n"
    (root / "hello.txt").write_text("later edit")
    assert value.apply("approval", tool, {"project": "rw"})["status"] == "unknown"
    assert (root / "hello.txt").read_text() == "later edit"


def test_apply_replay_payload_conflict_and_restart(fs):
    value, root = fs
    tool = replacement(value)
    first = value.apply("approval", tool, {"project": "rw"})
    assert first["status"] == "applied"
    changed = tool.model_copy(update={"content": "evil"})
    assert value.apply("approval", changed, {"project": "rw"})["status"] == "conflict"
    reopened = Filesystem(
        {"project": {"path": str(root), "mode": "rw"}}, str(root.parent / "state")
    )
    try:
        assert reopened.apply("approval", tool, {"project": "rw"}) == first
    finally:
        reopened.close()


def test_nul_containing_files_are_not_text(fs):
    value, root = fs
    (root / "binary.txt").write_bytes("hello".encode("utf-16-le"))
    result = value.execute(
        "binary-read", {"kind": "read", "root": "project", "path": "binary.txt"}, {"project": "ro"}
    )
    assert result["status"] == "denied"


def test_nul_containing_proposals_are_refused():
    with pytest.raises(ValueError, match="NUL"):
        Tool(
            kind="create_file",
            root="project",
            path="binary.txt",
            content="a\u0000b",
            expected_version="absent",
        )
