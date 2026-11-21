import os

import pytest


def test_host_execution_requires_explicit_credential_acknowledgement(tmp_path):
    from agent_relay.setup import initialize

    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ValueError, match="credential"):
        initialize(
            root, tmp_path / "pair", "127.0.0.1", 9443, grant="exec", execution_backend="host"
        )
    assert not (tmp_path / "pair").exists()


def test_container_command_has_only_project_mount_no_network_or_host_home(tmp_path, monkeypatch):
    from agent_relay.backends import DockerBackend

    monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/docker")
    root = tmp_path / "root"
    root.mkdir()
    backend = DockerBackend(root, {"image": "python:3.13-slim"})
    argv, cwd, env = backend.creation(
        "peerrelay-test", dict(cwd=str(root), command="echo hello", terminal=False)
    )
    assert argv[-3:] == ["/bin/sh", "-c", "echo hello"]
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--pull") + 1] == "never"
    assert "--read-only" in argv and "--privileged" not in argv
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv.count("--mount") == 1
    mount = argv[argv.index("--mount") + 1]
    assert mount == f"type=bind,src={root},dst=/workspace"
    assert argv[argv.index("--user") + 1] != "0:0"
    assert "/var/run/docker.sock" not in mount
    assert "DOCKER_HOST" not in env
    with pytest.raises(ValueError):
        backend.creation("peerrelay-test", dict(cwd=str(tmp_path), command="bad", terminal=False))


def test_missing_docker_never_falls_back_to_host(tmp_path, monkeypatch):
    from agent_relay.backends import DockerBackend

    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(ValueError, match="Docker"):
        DockerBackend(tmp_path, {"image": "python:3.13-slim"})


def test_host_home_is_disposable_and_does_not_inherit_secrets(tmp_path, monkeypatch):
    from agent_relay.backends import HostBackend

    monkeypatch.setenv("SENSITIVE_PROVIDER_TOKEN", "never-forward")
    backend = HostBackend(tmp_path / "state")
    try:
        _, _, env = backend.launch("test", dict(cwd=str(tmp_path), command="true", terminal=False))
        assert env["HOME"] != os.environ["HOME"]
        assert env["TMPDIR"].startswith(env["HOME"])
        assert "SENSITIVE_PROVIDER_TOKEN" not in env
        assert os.path.isdir(env["HOME"])
    finally:
        backend.close()
    assert not os.path.exists(env["HOME"])


def test_unremoved_containers_block_host_restart(tmp_path):
    import sqlite3

    from agent_relay.service import Relay

    root = tmp_path / "root"
    root.mkdir()
    state = tmp_path / "state"
    relay = Relay(root, state)
    relay.close()
    with sqlite3.connect(state / "execution.sqlite") as db:
        db.execute(
            "INSERT INTO container_resources(command,name,removed) VALUES (?,?,0)",
            ("command", "peerrelay-command"),
        )
    with pytest.raises(ValueError, match="Unremoved containers"):
        Relay(root, state)


def test_container_cleanup_failure_leaves_uncertain_durable_record(tmp_path, monkeypatch):
    from test_execution import finish, run, task

    from agent_relay import execution
    from agent_relay.service import Relay

    class ContainerContract:
        name = "docker"

        def launch(self, ident, spec):
            return ["/bin/sh", "-c", "printf done"], spec["cwd"], {"PATH": "/usr/bin:/bin"}

        def prepare(self, ident, spec):
            pass

        def cleanup(self, ident, confirmed=True):
            return False

        def close(self):
            pass

    monkeypatch.setattr(execution, "backend", lambda *a: ContainerContract())
    root = tmp_path / "root"
    root.mkdir()
    relay = Relay(root, tmp_path / "state", grant="exec")
    try:
        _, lease, ident = task(relay)
        result = finish(relay, lease, ident, run(relay, lease, ident, "ignored by test backend"))
        assert result["state"] == "uncertain"
        assert (
            relay.executor.db.execute("SELECT removed FROM container_resources").fetchone()[0] == 0
        )
    finally:
        relay.close()
    with pytest.raises(ValueError, match="cleanup"):
        Relay(root, tmp_path / "state", grant="exec")


@pytest.mark.parametrize("changed", [{"kind": "host"}, {"kind": "docker", "image": "different"}])
def test_mode_cannot_inherit_changed_backend_after_restart(tmp_path, monkeypatch, changed):
    from test_execution import task

    from agent_relay import execution
    from agent_relay.backends import HostBackend
    from agent_relay.service import Denied, Relay

    root = tmp_path / "root"
    root.mkdir()
    state = tmp_path / "state"
    monkeypatch.setattr(execution, "backend", lambda root, state, config: HostBackend(state))
    first = Relay(root, state, grant="exec", execution_backend={"kind": "docker", "image": "first"})
    mode, lease, ident = task(first)
    first.close()
    second = Relay(root, state, grant="exec", execution_backend=changed)
    try:
        lease = second.call("responder", "response_mode", {})["lease_id"]
        ident = second.call(
            "requester", "submit", dict(mode_id=mode, operation_id="next", prompt="continue")
        )["task_id"]
        second.call("responder", "claim", dict(lease_id=lease))
        with pytest.raises(Denied):
            with second.execution_authority(lease, ident):
                pass
        assert (
            second.call("requester", "mode_status", dict(mode_id=mode))["workspace_matches"]
            is False
        )
    finally:
        second.close()


def test_unresolved_container_blocks_new_execution_and_success(tmp_path):
    from test_execution import run, task

    from agent_relay.service import Denied, Relay

    root = tmp_path / "root"
    root.mkdir()
    relay = Relay(root, tmp_path / "state", grant="exec")
    try:
        _, lease, ident = task(relay)
        relay.executor.db.execute(
            "INSERT INTO container_resources(command,name,removed) VALUES (?,?,0)",
            ("lost", "peerrelay-lost"),
        )
        relay.executor.db.commit()
        with pytest.raises(Denied):
            run(relay, lease, ident, "touch forbidden")
        with pytest.raises(Denied):
            relay.call("responder", "complete", dict(lease_id=lease, task_id=ident, summary="done"))
        assert not (root / "forbidden").exists()
    finally:
        relay.close()


def test_unconfirmed_create_absence_is_not_cleanup_success(tmp_path, monkeypatch):
    import subprocess

    from agent_relay.backends import DockerBackend

    monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/docker")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a[0], 1, b"", b"Error response from daemon: No such container: peerrelay-late"
        ),
    )
    backend = DockerBackend(tmp_path, {"image": "python:3.13-slim"})
    assert backend.cleanup("peerrelay-late", confirmed=False) is False
    assert backend.cleanup("peerrelay-known", confirmed=True) is True


@pytest.mark.parametrize("event", ["lease_expiry", "workspace_change"])
def test_container_prepare_revalidates_authority_before_start(tmp_path, monkeypatch, event):
    from test_execution import run, task

    from agent_relay import execution
    from agent_relay.service import Relay

    clock = [1000.0]
    root = tmp_path / "root"
    root.mkdir()
    starts = []

    class PreparedContainer:
        name = "docker"

        def prepare(self, ident, spec):
            if event == "lease_expiry":
                clock[0] += 61
            else:
                root.rename(tmp_path / "original")
                root.mkdir()

        def launch(self, ident, spec):
            starts.append(ident)
            return ["/bin/sh", "-c", "touch forbidden"], str(root), {"PATH": "/usr/bin:/bin"}

        def cleanup(self, ident, confirmed=True):
            return True

        def close(self):
            pass

    monkeypatch.setattr(execution, "backend", lambda *a: PreparedContainer())
    relay = Relay(root, tmp_path / "state", grant="exec", clock=lambda: clock[0])
    try:
        _, lease, ident = task(relay)
        job = run(relay, lease, ident, "touch forbidden")
        # Expired tasks cannot use remote status; inspect the local durable executor record.
        import time

        deadline = time.monotonic() + 2
        while relay.executor.status(job["command_id"])["state"] in (
            "queued",
            "starting",
            "running",
        ):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert not starts
        assert not (root / "forbidden").exists()
    finally:
        relay.close()


def test_interrupted_create_never_starts_workload_and_keeps_tombstone(tmp_path, monkeypatch):
    import subprocess
    import time

    from test_execution import run, task

    from agent_relay import execution
    from agent_relay.service import Relay

    starts = []

    class DelayedCreate:
        name = "docker"

        def prepare(self, ident, spec):
            # The daemon may admit a stopped container after the client times out.
            raise subprocess.TimeoutExpired("docker create", 10)

        def launch(self, ident, spec):
            starts.append(ident)
            raise AssertionError("uncertain creation must never start a workload")

        def cleanup(self, ident, confirmed=True):
            return False  # rm saw absence, which is insufficient for unconfirmed creation.

        def close(self):
            pass

    monkeypatch.setattr(execution, "backend", lambda *a: DelayedCreate())
    root = tmp_path / "root"
    root.mkdir()
    relay = Relay(root, tmp_path / "state", grant="exec")
    try:
        _, lease, ident = task(relay)
        job = run(relay, lease, ident, "touch forbidden")
        deadline = time.monotonic() + 2
        while relay.executor.status(job["command_id"])["state"] in (
            "queued",
            "starting",
            "running",
        ):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert not starts and not (root / "forbidden").exists()
        row = relay.executor.db.execute(
            "SELECT removed,confirmed FROM container_resources"
        ).fetchone()
        assert tuple(row) == (0, 0)
        assert relay.executor.quarantined()
    finally:
        relay.close()
