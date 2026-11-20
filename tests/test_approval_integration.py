"""Host integration boundaries; all records and targets are disposable fixtures."""

import json
import os
import selectors
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agent_relay.approvals import Approvals
from agent_relay.filesystem import Filesystem
from agent_relay.journal import Journal
from agent_relay.schemas import Tool, canonical, payload_hash


@pytest.fixture
def approval(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "sample").write_bytes(b"old\r\n")
    fs = Filesystem({"p": {"path": str(root), "mode": "rw"}}, tmp_path / "state")
    tool = Tool(
        kind="propose_replace",
        root="p",
        path="sample",
        content="new\r\n",
        expected_version=fs.read(Tool(kind="read", root="p", path="sample"))["version"],
    )
    yield fs, Approvals(fs), root, tool.model_dump()
    fs.close()


def test_application_keeps_original_receipt_and_exact_bytes(approval):
    fs, service, root, payload = approval
    receipt = fs.execute("proposal", payload, {"p": "rw"})
    view = service.inspect("proposal")
    before = (root / "sample").stat()
    assert view["proposed_content"] == "new\r\n"
    assert view["root_identity"] == [str(root), root.stat().st_dev, root.stat().st_ino]
    assert view["grants"] == {"p": "rw"}
    assert view["base_sha256"] == payload_hash("old\r\n")
    assert (root / "sample").stat() == before
    assert service.decide("proposal", view["digest"], "approve")["status"] == "applied"
    assert (root / "sample").read_bytes() == b"new\r\n"
    assert fs.journal.lookup("proposal") == receipt
    assert receipt["status"] == "proposed"
    assert fs.execute("proposal", payload, {"p": "rw"}) == receipt


def test_additive_upgrade_preserves_old_roots_operations_and_import_binding(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "sample").write_text("before")
    state = tmp_path / "state"
    state.mkdir()
    # Exact original schema, with a historical operation unrelated to approval.
    with sqlite3.connect(state / "journal.sqlite") as db:
        db.execute("CREATE TABLE operations (id TEXT PRIMARY KEY, hash TEXT NOT NULL, result TEXT)")
        db.execute("CREATE TABLE roots (alias TEXT PRIMARY KEY, identity TEXT NOT NULL)")
        db.execute(
            "INSERT INTO operations VALUES ('historical', 'keep-hash', "
            '\'{"status":"succeeded"}\')'
        )
        root_identity = canonical([str(root), root.stat().st_dev, root.stat().st_ino])
        db.execute("INSERT INTO roots VALUES ('p', ?)", (root_identity,))
        original = db.execute("SELECT * FROM operations").fetchall()
    fs = Filesystem({"p": {"path": str(root), "mode": "rw"}}, state)
    try:
        with fs.journal.connect() as db:
            assert db.execute("SELECT * FROM operations").fetchall() == original
            assert db.execute("SELECT * FROM roots").fetchall() == [("p", root_identity)]
        current = fs.read(Tool(kind="read", root="p", path="sample"))
        tool = Tool(
            kind="propose_replace",
            root="p",
            path="sample",
            content="after",
            expected_version=current["version"],
        ).model_dump()
        receipt = {"status": "proposed", "base_version": current["version"], "content": "after"}
        fs.journal.begin("legacy", tool, {"p": "rw"})
        fs.journal.finish("legacy", receipt)
        service = Approvals(fs)
        (root / "wrong").write_text("before")
        with pytest.raises(ValueError, match="admitted operation"):
            service.import_target("legacy", "p", "wrong", {"p": "rw"})
        view = service.import_target("legacy", "p", "sample", {"p": "rw"})
        assert view["status"] == "pending" and view["base_content"] == "before"
        assert fs.journal.lookup("legacy") == receipt
        assert (root / "sample").read_text() == "before"
        with fs.execution_guard():
            fs.journal.purge(["legacy"])
        with pytest.raises(ValueError, match="unavailable"):
            service.import_target("legacy", "p", "sample", {"p": "rw"})
        assert fs.journal.begin("legacy", tool, {"p": "rw"}) == {"status": "proposed"}
    finally:
        fs.close()


def test_connect_closes_and_rolls_back(tmp_path):
    journal = Journal(tmp_path / "state")
    with pytest.raises(RuntimeError):
        with journal.connect() as connection:
            connection.execute("INSERT INTO operations VALUES ('rollback','hash',NULL)")
            raise RuntimeError("interrupted transaction")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    with journal.connect() as db:
        assert db.execute("SELECT * FROM operations").fetchall() == []


@pytest.mark.parametrize("state", ["pending", "applied", "applying"])
def test_purge_scrubs_content_and_preserves_replay_tombstones(approval, state):
    fs, service, root, payload = approval
    fs.execute("proposal", payload, {"p": "rw"})
    view = service.inspect("proposal")
    if state == "applied":
        service.decide("proposal", view["digest"], "approve")
    elif state == "applying":
        # Durable state left by a dead process, without claiming an effect occurred.
        with fs.journal.connect() as db:
            db.execute("UPDATE local_proposals SET state='applying'")
    before = (root / "sample").stat()
    with fs.execution_guard():
        fs.journal.purge(["proposal"])
    with fs.journal.connect() as db:
        assert db.execute("SELECT binding,base_content FROM local_proposals").fetchone() == (
            "{}",
            None,
        )
        assert db.execute("SELECT hash,result FROM operations").fetchone() == (
            payload_hash([payload, {"p": "rw"}]),
            '{"status":"proposed"}',
        )
        assert all(
            "new\\r\\n" not in row[0] and "old\\r\\n" not in row[0]
            for row in db.execute("SELECT detail FROM local_proposal_events")
        )
    expected = {"pending": "rejected", "applied": "applied", "applying": "uncertain"}[state]
    assert service.decide("proposal", view["digest"], "approve")["status"] == expected
    assert (root / "sample").stat() == before
    assert fs.execute("proposal", payload, {"p": "rw"}) == {"status": "proposed"}


@pytest.mark.parametrize("action", ["purge", "capture"])
def test_companion_purge_and_capture_wait_for_local_execution_lock(approval, action):
    fs, service, root, payload = approval
    fs.execute("proposal", payload, {"p": "rw"})
    script = """
import json,sys
from agent_relay.filesystem import Filesystem
fs=Filesystem({'p':{'path':sys.argv[1],'mode':'rw'}},sys.argv[2])
print('ready',flush=True)
if sys.argv[3]=='purge':
    fs.purge(['proposal']); print('done',flush=True)
else:
    print(fs.execute('second',json.loads(sys.argv[4]),{'p':'rw'})['status'],flush=True)
fs.close()
"""
    with fs.execution_guard():
        child = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-c",
                script,
                str(root),
                str(fs.journal.directory),
                action,
                json.dumps(payload),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        )
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(child.stdout, selectors.EVENT_READ)
                assert ready.select(10), "child did not initialize"
                assert os.read(child.stdout.fileno(), 6) == b"ready\n"
                assert not ready.select(0.2), "companion bypassed the shared execution lock"
        except BaseException:
            child.kill()
            child.communicate()
            raise
    out, err = child.communicate(timeout=10)
    assert child.returncode == 0, err.decode()
    assert out.strip() == (b"done" if action == "purge" else b"proposed")
    if action == "purge":
        assert service.inspect("proposal")["detail"]["purged"]
    else:
        assert service.inspect("second")["status"] == "pending"
