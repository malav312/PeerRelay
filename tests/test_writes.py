import pytest

from agent_relay.filesystem import Filesystem
from agent_relay.schemas import Tool


@pytest.fixture
def fs(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    value = Filesystem({"project": {"path": str(root), "mode": "rw"}}, str(tmp_path / "state"))
    yield value, root
    value.close()


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
