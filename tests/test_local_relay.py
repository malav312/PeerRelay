import importlib.util
import json
import subprocess
import sys
import time
from contextlib import contextmanager

import pytest

from agent_relay.client import load
from agent_relay.service import Denied, Relay
from agent_relay.setup import initialize


@pytest.fixture
def paired(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "hello.txt").write_bytes(b"before\r\n")
    (root / "hello.txt").chmod(0o640)
    pair = tmp_path / "pair"
    initialize(root, pair, "127.0.0.1", 9443)
    config = load(pair / "server.json")
    config["grant"] = "rw"
    saved = json.loads((pair / "server.json").read_text())
    saved["grant"] = "rw"
    (pair / "server.json").write_text(json.dumps(saved))
    clock = [time.time()]
    relay = Relay(root, config["state_dir"], clock=lambda: clock[0])
    mode = relay.call("requester", "requester_mode", {})["mode_id"]
    lease = relay.call("responder", "response_mode", {})["lease_id"]
    task = relay.call(
        "requester", "submit", {"mode_id": mode, "operation_id": "t", "prompt": "edit"}
    )["task_id"]
    relay.call("responder", "claim", {"lease_id": lease})
    read = relay.call(
        "responder",
        "remote_tool",
        {"lease_id": lease, "task_id": task, "kind": "read", "path": "hello.txt"},
    )
    args = {
        "lease_id": lease,
        "task_id": task,
        "operation_id": "p",
        "path": "hello.txt",
        "content": "after\r\n",
        "base_version": read["version"],
    }
    proposal = relay.call("responder", "propose", args)
    yield relay, config, root, mode, lease, task, proposal, args, clock
    relay.close()


def local(config, clock=time.time):
    assert importlib.util.find_spec("agent_relay.local_approvals"), "Local decision adapter missing"
    from agent_relay.local_approvals import LocalDecisions

    return LocalDecisions(config, clock=clock)


def test_local_apply_preserves_remote_receipt_and_compact_status(paired):
    relay, config, root, mode, lease, task, p, args, _ = paired
    ident = p["proposal_id"]
    with local(config) as decisions:
        view = decisions.inspect(ident)
        assert view["root_identity"][0] == str(root)
        assert view["grants"] == {"project": "rw"}
        assert view["proposed_escaped"] == '"after\\r\\n"'
        before = relay.fs.journal.lookup(ident)
        assert (root / "hello.txt").read_bytes() == b"before\r\n"
        assert decisions.decide(ident, view["digest"], "approve")["status"] == "applied"
        inode = (root / "hello.txt").stat().st_ino
        assert decisions.decide(ident, view["digest"], "approve")["status"] == "applied"
        assert (root / "hello.txt").stat().st_ino == inode
        assert (root / "hello.txt").read_bytes() == b"after\r\n"
        assert (root / "hello.txt").stat().st_mode & 0o777 == 0o640
        assert relay.fs.journal.lookup(ident) == before
        assert before["status"] == "proposed"
    status = relay.call("requester", "status", {"mode_id": mode, "task_id": task})
    assert status["proposals"] == [{"id": ident, "status": "proposed"}]
    assert "digest" not in p
    assert "diff" not in json.dumps(status)
    assert relay.call("responder", "propose", args) == p


def test_lease_expiring_while_decision_waits_for_guard_blocks_application(paired, monkeypatch):
    _, config, root, _, _, _, proposal, _, clock = paired
    ident = proposal["proposal_id"]
    with local(config, clock=lambda: clock[0]) as decisions:
        digest = decisions.inspect(ident)["digest"]
        original_guard = decisions.fs.execution_guard
        original_decide = decisions.core.decide
        deciding = False

        @contextmanager
        def delayed_guard():
            with original_guard():
                if deciding:
                    # Deterministic elapsed lock-wait time, before the guarded decision.
                    clock[0] += 61
                yield

        def begin_decision(*args, **kwargs):
            nonlocal deciding
            deciding = True
            return original_decide(*args, **kwargs)

        monkeypatch.setattr(decisions.fs, "execution_guard", delayed_guard)
        monkeypatch.setattr(decisions.core, "decide", begin_decision)
        with pytest.raises(ValueError, match="authority revoked"):
            decisions.decide(ident, digest, "approve")
        assert decisions.inspect(ident)["status"] == "pending"
        assert (root / "hello.txt").read_bytes() == b"before\r\n"


def test_purge_does_not_leave_replacement_content_in_relay_store(paired):
    relay, config, root, _, _, _, proposal, _, _ = paired
    ident = proposal["proposal_id"]
    relay.fs.purge([ident])
    with local(config) as decisions:
        view = decisions.inspect(ident)
        assert view["status"] == "rejected"
        assert view["detail"]["purged"] is True
        assert decisions.decide(ident, view["digest"], "approve") == view
    with relay.fs.journal.connect() as journal:
        binding, base = journal.execute(
            "SELECT binding,base_content FROM local_proposals WHERE id=?", (ident,)
        ).fetchone()
        assert json.loads(binding) == {}
        assert base is None
        assert "after" not in json.dumps(relay.fs.journal.lookup(ident))
    stored = relay.db.execute("SELECT tool,hash FROM proposals WHERE id=?", (ident,)).fetchone()
    assert json.loads(stored["tool"]) == {}
    assert stored["hash"]
    assert (root / "hello.txt").read_bytes() == b"before\r\n"


@pytest.mark.parametrize("method", ["apply", "review", "approve", "reject"])
@pytest.mark.parametrize("role", ["requester", "responder"])
def test_no_remote_decision_or_diff_route(paired, role, method):
    relay, _, root, mode, _, _, p, _, _ = paired
    args = {"mode_id": mode, "proposal_id": p["proposal_id"]}
    if method != "review":
        args["digest"] = p.get("digest", "")
    with pytest.raises(Denied):
        relay.call(role, method, args)
    assert (root / "hello.txt").read_bytes() == b"before\r\n"


@pytest.mark.parametrize(
    "event", ["disconnect", "complete_disconnect", "expiry", "cancel", "exit", "restart"]
)
def test_revocation_blocks_pending_local_application(paired, event):
    relay, config, root, mode, lease, task, p, _, clock = paired
    with local(config, clock=lambda: clock[0]) as decisions:
        view = decisions.inspect(p["proposal_id"])
        if event == "complete_disconnect":
            relay.call(
                "responder", "complete", {"lease_id": lease, "task_id": task, "summary": "ready"}
            )
        if event in ("disconnect", "complete_disconnect"):
            relay.call("responder", "disconnect", {"lease_id": lease})
        elif event == "expiry":
            clock[0] += 61
        elif event == "cancel":
            relay.call("requester", "cancel", {"mode_id": mode, "task_id": task})
        elif event == "exit":
            relay.call("requester", "exit_mode", {"mode_id": mode})
        else:
            relay.close()
            restarted = Relay(root, config["state_dir"])
            restarted.close()
        with pytest.raises(ValueError, match="revoked"):
            decisions.decide(p["proposal_id"], view["digest"], "approve")
        assert decisions.decide(p["proposal_id"], view["digest"], "reject")["status"] == "rejected"
    assert (root / "hello.txt").read_bytes() == b"before\r\n"


def test_local_cli_offline_inspect_digest_required_and_reject(paired):
    relay, config, root, _, _, _, p, _, _ = paired
    relay.close()  # No server listening, and no gateway/model access needed.
    command = [sys.executable, "-m", "agent_relay.cli", "proposal", "--config", config["_path"]]
    denied = subprocess.run(command + ["approve", p["proposal_id"]], capture_output=True, text=True)
    assert denied.returncode != 0
    view = subprocess.run(
        command + ["--json", "inspect", p["proposal_id"]], capture_output=True, text=True
    )
    assert view.returncode == 0, view.stderr + view.stdout
    digest = json.loads(view.stdout)["digest"]
    result = subprocess.run(
        command + ["--json", "reject", p["proposal_id"], "--digest", digest],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "interactive terminal" in result.stdout
    from terminal_helpers import decision

    result = decision(
        command + ["--json", "reject", p["proposal_id"], "--digest", digest], "reject", digest
    )
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout)["status"] == "rejected"
    assert (root / "hello.txt").read_bytes() == b"before\r\n"


def test_creation_is_refused(paired):
    relay, _, root, _, _, _, _, args, _ = paired
    with pytest.raises(Denied):
        relay.call(
            "responder",
            "propose",
            dict(args, operation_id="create", path="new.txt", base_version="absent"),
        )
    assert not (root / "new.txt").exists()


def test_new_pair_is_readonly_unless_explicit(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    initialize(root, tmp_path / "ro", "127.0.0.1", 9443)
    assert load(tmp_path / "ro/server.json")["grant"] == "ro"
    initialize(root, tmp_path / "rw", "127.0.0.1", 9443, grant="rw")
    assert load(tmp_path / "rw/server.json")["grant"] == "rw"


def test_legacy_proposals_are_preserved_but_not_imported(paired):
    relay, config, root, _, _, task, _, _, _ = paired
    relay.db.execute(
        "INSERT INTO proposals(id,task,operation,hash,tool,digest,status) "
        "VALUES ('legacy',?,'legacy','hash','{}','old','proposed')",
        (task,),
    )
    relay.db.commit()
    with local(config) as decisions:
        with pytest.raises(ValueError, match="fresh proposal"):
            decisions.inspect("legacy")
    assert relay.db.execute("SELECT digest,status FROM proposals WHERE id='legacy'").fetchone()[
        :
    ] == ("old", "proposed")
    assert (root / "hello.txt").read_bytes() == b"before\r\n"


def test_readonly_grant_cannot_capture_proposal(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "hello").write_text("old")
    relay = Relay(root, tmp_path / "state", grant="ro")
    try:
        mode = relay.call("requester", "requester_mode", {})["mode_id"]
        lease = relay.call("responder", "response_mode", {})["lease_id"]
        task = relay.call(
            "requester", "submit", {"mode_id": mode, "operation_id": "t", "prompt": "read"}
        )["task_id"]
        relay.call("responder", "claim", {"lease_id": lease})
        result = relay.call(
            "responder",
            "remote_tool",
            {"lease_id": lease, "task_id": task, "kind": "read", "path": "hello"},
        )
        with pytest.raises(Denied):
            relay.call(
                "responder",
                "propose",
                {
                    "lease_id": lease,
                    "task_id": task,
                    "operation_id": "p",
                    "path": "hello",
                    "content": "new",
                    "base_version": result["version"],
                },
            )
        assert (root / "hello").read_text() == "old"
    finally:
        relay.close()


def test_local_missing_root_allows_inspection_and_rejection(paired):
    relay, config, root, _, _, _, p, _, _ = paired
    root.rename(root.with_name("moved-project"))
    with local(config) as decisions:
        view = decisions.inspect(p["proposal_id"])
        assert not view["current_matches"]
        assert decisions.decide(p["proposal_id"], view["digest"], "reject")["status"] == "rejected"


def test_disconnect_waits_for_already_started_local_apply(paired):
    import concurrent.futures
    import selectors

    relay, config, root, _, lease, _, p, _, _ = paired
    with local(config) as decisions:
        digest = decisions.inspect(p["proposal_id"])["digest"]
    script = """
import os,sys,json
from agent_relay.client import load
from agent_relay.local_approvals import LocalDecisions
replace=os.replace
def paused(*args,**kwargs):
    print('applying',flush=True)
    sys.stdin.readline()
    return replace(*args,**kwargs)
os.replace=paused
with LocalDecisions(load(sys.argv[1])) as decisions:
    print(json.dumps(decisions.decide(sys.argv[2],sys.argv[3],'approve')),flush=True)
"""
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", script, config["_path"], p["proposal_id"], digest],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(child.stdout, selectors.EVENT_READ)
            assert ready.select(5)
            assert child.stdout.readline().strip() == "applying"
        with concurrent.futures.ThreadPoolExecutor() as pool:
            pending = pool.submit(relay.call, "responder", "disconnect", {"lease_id": lease})
            try:
                time.sleep(0.15)
                assert not pending.done()
            finally:
                child.stdin.write("\n")
                child.stdin.flush()
            assert pending.result(timeout=5)["state"] == "disconnected"
        out, err = child.communicate(timeout=5)
        assert child.returncode == 0, err
        assert json.loads(out)["status"] == "applied"
        assert (root / "hello.txt").read_bytes() == b"after\r\n"
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()


def test_existing_pair_without_grant_preserves_rw_and_journals(paired):
    relay, config, root, _, _, _, p, _, _ = paired
    config.pop("grant")
    with local(config) as decisions:
        view = decisions.inspect(p["proposal_id"])
        assert decisions.decide(p["proposal_id"], view["digest"], "approve")["status"] == "applied"
    assert relay.fs.journal.lookup(p["proposal_id"])["status"] == "proposed"
    assert (root / "hello.txt").read_bytes() == b"after\r\n"
