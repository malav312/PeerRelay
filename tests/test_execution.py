import json
import shlex
import subprocess
import sys
import time

import pytest

from agent_relay.service import Denied, Relay
from agent_relay.setup import initialize


def task(relay, execution=True):
    mode = relay.call("requester", "requester_mode", {"execution": execution})["mode_id"]
    lease = relay.call("responder", "response_mode", {})["lease_id"]
    ident = relay.call(
        "requester", "submit", {"mode_id": mode, "operation_id": "task", "prompt": "Do the work"}
    )["task_id"]
    relay.call("responder", "claim", {"lease_id": lease})
    return mode, lease, ident


@pytest.fixture
def session(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    relay = Relay(root, tmp_path / "state", grant="exec")
    mode, lease, ident = task(relay)
    yield relay, root, mode, lease, ident
    relay.close()


def run(relay, lease, ident, command, operation="command", **kwargs):
    return relay.call(
        "responder",
        "execute",
        dict(lease_id=lease, task_id=ident, operation_id=operation, command=command, **kwargs),
    )


def status(relay, lease, ident, job, **kwargs):
    return relay.call(
        "responder",
        "command_status",
        dict(lease_id=lease, task_id=ident, command_id=job["command_id"], **kwargs),
    )


def finish(relay, lease, ident, job):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        result = status(relay, lease, ident, job)
        if result["state"] not in ("queued", "starting", "running"):
            return result
        time.sleep(0.02)
    pytest.fail("Command did not finish")


def python(code):
    return shlex.quote(sys.executable) + " -u -c " + shlex.quote(code)


def test_commands_change_same_worktree_and_retry_never_reexecutes(session):
    relay, root, _, lease, ident = session
    command = (
        "mkdir sub; printf 'hello' > sub/new; mv sub/new sub/renamed; cp sub/renamed gone; "
        "rm gone; printf x >> count; cat sub/renamed"
    )
    job = run(relay, lease, ident, command)
    result = finish(relay, lease, ident, job)
    assert result["state"] == "exited" and result["exit_code"] == 0
    assert result["output"] == "hello"
    assert (root / "sub/renamed").read_text() == "hello"
    assert not (root / "gone").exists()
    assert run(relay, lease, ident, command)["command_id"] == job["command_id"]
    assert (root / "count").read_text() == "x"
    with pytest.raises(Denied):
        run(relay, lease, ident, "printf y >> count")


@pytest.mark.parametrize("grant", ["ro", "rw"])
def test_old_grants_cannot_enable_execution(tmp_path, grant):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises((Denied, ValueError)):
        with_relay = Relay(root, tmp_path / "state", grant=grant)
        try:
            mode, lease, ident = task(with_relay)
            run(with_relay, lease, ident, "touch forbidden")
        finally:
            with_relay.close()
    assert not (root / "forbidden").exists()


def test_exec_pair_requires_explicit_session_activation(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    relay = Relay(root, tmp_path / "state", grant="exec")
    try:
        mode, lease, ident = task(relay, execution=False)
        with pytest.raises(Denied):
            run(relay, lease, ident, "touch forbidden")
        with pytest.raises(Denied):
            relay.call("requester", "execute", {})
    finally:
        relay.close()
    assert not (root / "forbidden").exists()


def test_setup_exec_is_explicit_and_defaults_remain_ro(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    initialize(root, tmp_path / "exec", "127.0.0.1", 9443, grant="exec")
    initialize(root, tmp_path / "ro", "127.0.0.1", 9443)
    assert json.loads((tmp_path / "exec/server.json").read_text())["grant"] == "exec"
    assert json.loads((tmp_path / "ro/server.json").read_text())["grant"] == "ro"


@pytest.mark.parametrize("terminal", [False, True])
def test_interactive_input_and_eof_are_idempotent(session, terminal):
    relay, root, _, lease, ident = session
    command = python(
        "print('ready', flush=True); x=input(); open('answer','w').write(x); print('got:'+x)"
    )
    job = run(relay, lease, ident, command, terminal=terminal)
    for _ in range(100):
        if "ready" in status(relay, lease, ident, job)["output"]:
            break
        time.sleep(0.02)
    else:
        pytest.fail("No interactive prompt")
    args = dict(
        lease_id=lease,
        task_id=ident,
        command_id=job["command_id"],
        operation_id="input1",
        data="Alice\n",
    )
    first = relay.call("responder", "command_input", args)
    assert relay.call("responder", "command_input", args) == first
    result = finish(relay, lease, ident, job)
    assert result["exit_code"] == 0
    assert "got:Alice" in result["output"]
    assert (root / "answer").read_text() == "Alice"
    with pytest.raises(Denied):
        relay.call("responder", "command_input", {**args, "data": "different"})


def test_output_is_bounded_and_nonzero_exit_is_reported(session):
    relay, _, _, lease, ident = session
    job = run(relay, lease, ident, python("import sys; print('x'*1100000); sys.exit(7)"))
    result = finish(relay, lease, ident, job)
    assert result["exit_code"] == 7
    assert result["truncated"] is True
    assert len(result["output"].encode()) <= 16384
    assert result["next_offset"] > 0
    result = status(relay, lease, ident, job, offset=1048576)
    assert result["output"] == ""


def test_timeout_and_cancel_stop_process_groups(session):
    relay, root, _, lease, ident = session
    job = run(relay, lease, ident, "sleep 3; touch too_late", timeout=1)
    assert finish(relay, lease, ident, job)["state"] == "timed_out"
    job = run(relay, lease, ident, "sleep 3; touch cancelled", operation="other")
    relay.call(
        "responder",
        "command_cancel",
        dict(lease_id=lease, task_id=ident, command_id=job["command_id"]),
    )
    assert finish(relay, lease, ident, job)["state"] == "cancelled"
    assert not (root / "too_late").exists()
    assert not (root / "cancelled").exists()


@pytest.mark.parametrize("event", ["disconnect", "cancel", "exit", "expiry"])
def test_revocation_stops_running_job_and_denies_new_effects(session, event):
    relay, root, mode, lease, ident = session
    job = run(relay, lease, ident, "printf started; sleep 30; touch forbidden")
    for _ in range(100):
        if "started" in status(relay, lease, ident, job)["output"]:
            break
        time.sleep(0.02)
    if event == "disconnect":
        relay.call("responder", "disconnect", {"lease_id": lease})
    elif event == "cancel":
        relay.call("requester", "cancel", {"mode_id": mode, "task_id": ident})
    elif event == "exit":
        relay.call("requester", "exit_mode", {"mode_id": mode})
    else:
        relay.clock = lambda: time.time() + 61
        with relay.lock:
            relay.sweep()
            relay.db.commit()
    with pytest.raises(Denied):
        run(relay, lease, ident, "touch new", operation="new")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if relay.executor.status(job["command_id"])["state"] == "cancelled":
            break
        time.sleep(0.02)
    assert relay.executor.status(job["command_id"])["state"] == "cancelled"
    assert not (root / "forbidden").exists()


def test_git_branch_is_bound_and_commands_start_in_requested_subdirectory(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "alice"], cwd=root, check=True)
    (root / "sub").mkdir()
    relay = Relay(root, tmp_path / "state", grant="exec")
    try:
        _, lease, ident = task(relay)
        job = run(relay, lease, ident, "pwd; git symbolic-ref --short HEAD", cwd="sub")
        result = finish(relay, lease, ident, job)
        assert result["output"] == str(root / "sub") + "\nalice\n"
        subprocess.run(
            ["git", "symbolic-ref", "HEAD", "refs/heads/elsewhere"], cwd=root, check=True
        )
        with pytest.raises((Denied, ValueError)):
            run(relay, lease, ident, "touch forbidden", operation="changed")
        assert not (root / "forbidden").exists()
    finally:
        relay.close()


def test_root_replacement_and_cwd_escape_are_refused(session, tmp_path):
    relay, root, _, lease, ident = session
    with pytest.raises((Denied, ValueError)):
        run(relay, lease, ident, "pwd", cwd="..")
    root.rename(tmp_path / "original")
    root.mkdir()
    with pytest.raises((Denied, ValueError)):
        run(relay, lease, ident, "touch forbidden", operation="changed")
    assert not (root / "forbidden").exists()


def test_completion_waits_for_commands_and_shutdown_does_not_replay(session):
    relay, root, _, lease, ident = session
    job = run(relay, lease, ident, "sleep 30; touch forbidden")
    with pytest.raises(Denied):
        relay.call("responder", "complete", dict(lease_id=lease, task_id=ident, summary="done"))
    relay.close()
    replacement = Relay(root, relay.state, grant="exec")
    try:
        assert replacement.executor.status(job["command_id"])["state"] in ("cancelled", "uncertain")
        with pytest.raises(Denied):
            run(replacement, lease, ident, "sleep 30; touch forbidden")
    finally:
        replacement.close()
    assert not (root / "forbidden").exists()
