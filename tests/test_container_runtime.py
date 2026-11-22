"""Opt-in acceptance tests against the backend's real local Docker API endpoint.

Run with PEERRELAY_CONTAINER_TEST=1 and docker on PATH. No runtime mocks.
The configured image must already exist; tests never pull images or touch live pairs.
"""

import os
import shlex
import subprocess
import time

import pytest
from test_execution import run, task

from agent_relay.service import Relay

pytestmark = pytest.mark.skipif(
    os.environ.get("PEERRELAY_CONTAINER_TEST") != "1",
    reason="requires explicit local container runtime acceptance opt-in",
)
CONFIG = {"kind": "docker", "image": "python:3.13-slim"}


def docker(*args):
    return subprocess.run(
        ["docker", "--host", "unix:///var/run/docker.sock", *args],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    ).stdout


def wait(relay, job, predicate=None):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        result = relay.executor.status(job["command_id"])
        if (
            predicate(result)
            if predicate
            else result["state"] not in ("queued", "starting", "running")
        ):
            return result
        time.sleep(0.05)
    pytest.fail(f"Runtime command did not reach expected state: {result}")


@pytest.fixture
def runtime(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    relay = Relay(root, tmp_path / "state", grant="exec", execution_backend=CONFIG)
    mode, lease, ident = task(relay)
    try:
        yield relay, root, mode, lease, ident
    finally:
        with relay.executor.lock:
            names = [
                row[0] for row in relay.executor.db.execute("SELECT name FROM container_resources")
            ]
        relay.close()
        for name in names:
            if docker("ps", "-aq", "--filter", "name=^" + name + "$").strip():
                docker("rm", "--force", name)


def test_real_runtime_write_and_replay(runtime):
    relay, root, _, lease, ident = runtime
    cmd = (
        "mkdir sub; printf hello > sub/first; mv sub/first sub/result; "
        "printf x >> count; cat sub/result"
    )
    job = run(relay, lease, ident, cmd)
    result = wait(relay, job)
    assert result["state"] == "exited" and result["exit_code"] == 0, result
    assert result["output"] == "hello"
    assert (root / "sub/result").read_text() == "hello"
    assert run(relay, lease, ident, cmd)["command_id"] == job["command_id"]
    assert (root / "count").read_text() == "x"
    assert relay.executor.db.execute("SELECT removed FROM container_resources").fetchone()[0] == 1


def test_real_runtime_isolation(runtime):
    relay, root, _, lease, ident = runtime
    sentinel = root.parent / "host-only-sentinel"
    sentinel.write_text("synthetic-test-secret")
    (root / "escape").symlink_to(sentinel)
    code = """
import os, pathlib, socket
assert os.getuid() != 0
assert os.environ['HOME'] == '/home/relay/private'
assert not pathlib.Path('/var/run/docker.sock').exists()
assert not pathlib.Path('/workspace/escape').exists()
assert not pathlib.Path(SENTINEL).exists()
assert not pathlib.Path('/workspace/../state').exists()
try:
    pathlib.Path('/etc/peerrelay-test').write_text('forbidden')
except OSError:
    pass
else:
    raise AssertionError('root filesystem writable')
assert set(os.listdir('/sys/class/net')) == {'lo'}
with socket.socket() as s:
    s.settimeout(1)
    assert s.connect_ex(('192.0.2.1', 9)) != 0
status = pathlib.Path('/proc/self/status').read_text()
assert 'CapEff:\\t0000000000000000' in status
assert 'NoNewPrivs:\\t1' in status
home = pathlib.Path(os.environ['HOME'])
assert home.stat().st_uid == os.getuid()
assert home.stat().st_mode & 0o777 == 0o700
(home / 'probe').write_text('ok')
print('isolated')
""".replace("SENTINEL", repr(str(sentinel)))
    result = wait(relay, run(relay, lease, ident, "python -c " + shlex.quote(code)))
    assert result["exit_code"] == 0 and result["output"].strip() == "isolated", result


@pytest.mark.parametrize("terminal", [False, True])
def test_real_runtime_interactive_input(runtime, terminal):
    relay, root, _, lease, ident = runtime
    code = "print('ready',flush=True); x=input(); open('answer','w').write(x); print('got:'+x)"
    job = run(relay, lease, ident, "python -u -c " + shlex.quote(code), terminal=terminal)
    wait(
        relay,
        job,
        lambda result: (
            "ready" in result["output"] or result["state"] in ("refused", "uncertain", "exited")
        ),
    )
    relay.call(
        "responder",
        "command_input",
        dict(
            lease_id=lease,
            task_id=ident,
            command_id=job["command_id"],
            operation_id="answer",
            data="Alice\n",
        ),
    )
    result = wait(relay, job)
    assert result["exit_code"] == 0 and "got:Alice" in result["output"], result
    assert (root / "answer").read_text() == "Alice"


def test_real_runtime_disconnect_removes_detached_child(runtime):
    relay, root, _, lease, ident = runtime
    child = "import time,pathlib; time.sleep(3); pathlib.Path('escaped').write_text('bad')"
    code = (
        f"import subprocess,time; subprocess.Popen(['python','-c',{child!r}],"
        "start_new_session=True); print('ready',flush=True); time.sleep(30)"
    )
    job = run(relay, lease, ident, "python -u -c " + shlex.quote(code))
    result = wait(
        relay,
        job,
        lambda r: "ready" in r["output"] or r["state"] in ("refused", "uncertain", "exited"),
    )
    assert "ready" in result["output"], result
    name = relay.executor.db.execute("SELECT name FROM container_resources").fetchone()[0]
    relay.call("responder", "disconnect", {"lease_id": lease})
    assert wait(relay, job)["state"] == "cancelled"
    assert not docker("ps", "-aq", "--filter", "name=^" + name + "$").strip()
    time.sleep(3.2)
    assert not (root / "escaped").exists()


def test_real_runtime_timeout(runtime):
    relay, root, _, lease, ident = runtime
    job = run(relay, lease, ident, "sleep 10; touch too-late", timeout=1)
    assert wait(relay, job)["state"] == "timed_out"
    assert not (root / "too-late").exists()


def test_real_runtime_restart_removes_crash_orphan_without_replay(tmp_path):
    import json
    import sqlite3
    import sys

    root = tmp_path / "project"
    root.mkdir()
    state = tmp_path / "state"
    script = """
import json, os, sys, time
from pathlib import Path
from agent_relay.service import Relay
r=Relay(Path(sys.argv[1]),Path(sys.argv[2]),grant='exec',
        execution_backend={'kind':'docker','image':'python:3.13-slim'})
m=r.call('requester','requester_mode',{'execution':True})['mode_id']
l=r.call('responder','response_mode',{})['lease_id']
t=r.call('requester','submit',dict(mode_id=m,operation_id='t',prompt='test'))['task_id']
r.call('responder','claim',dict(lease_id=l))
j=r.call('responder','execute',dict(lease_id=l,task_id=t,operation_id='x',
       command='printf x >> count; sleep 60; touch escaped'))
print(json.dumps(j),flush=True)
deadline=time.monotonic()+15
while not (r.root/'count').exists():
    if time.monotonic()>deadline: raise RuntimeError(r.executor.status(j['command_id']))
    time.sleep(.05)
os._exit(17)
"""
    names = []
    relay = None
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, str(root), str(state)],
            capture_output=True,
            text=True,
            timeout=20,
        )
        with sqlite3.connect(state / "execution.sqlite") as db:
            names = [r[0] for r in db.execute("SELECT name FROM container_resources")]
        assert result.returncode == 17, result.stderr
        job = json.loads(result.stdout)
        assert len(names) == 1
        assert docker("ps", "-q", "--filter", "name=^" + names[0] + "$").strip()
        relay = Relay(root, state, grant="exec", execution_backend=CONFIG)
        assert relay.executor.status(job["command_id"])["state"] == "uncertain"
        assert not docker("ps", "-aq", "--filter", "name=^" + names[0] + "$").strip()
        assert (root / "count").read_text() == "x"
        assert not (root / "escaped").exists()
        assert not relay.executor.quarantined()
    finally:
        if relay:
            relay.close()
        for name in names:
            if docker("ps", "-aq", "--filter", "name=^" + name + "$").strip():
                docker("rm", "--force", name)


def test_spoken_pair_runs_real_container_over_pinned_tls(tmp_path):
    import socket
    import sys

    import httpx
    from test_pairing import Host, join

    from agent_relay.client import Client, load
    from agent_relay.setup import initialize_host

    root = tmp_path / "project"
    root.mkdir()
    pair = tmp_path / "pair"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    initialize_host(root, pair, "127.0.0.1", port, grant="exec")
    with Host(tmp_path, grant=None, backend=None) as host:
        offer, _ = join(tmp_path, host.address)
    assert host.error is None
    assert offer["execution_backend"] == "docker"
    process = subprocess.Popen(
        [sys.executable, "-m", "agent_relay.cli", "serve", "--config", str(pair / "server.json")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    alice = Client(load(pair / "requester/client.json"))
    bob = Client(load(tmp_path / "bob/client.json"))
    try:
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail("Relay startup failed: " + process.stderr.read().decode()[-2000:])
            try:
                if (
                    bob.http.get(
                        "/healthz",
                        headers={"Authorization": "Bearer " + bob.token_file.read_text().strip()},
                    ).status_code
                    == 200
                ):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        else:
            pytest.fail("Paired relay startup timed out")
        mode = alice.call("requester_mode", {"execution": True})["mode_id"]
        lease = bob.call("response_mode", {})["lease_id"]
        task_id = alice.call("submit", dict(mode_id=mode, operation_id="t", prompt="write"))[
            "task_id"
        ]
        bob.call("claim", dict(lease_id=lease))
        common = dict(lease_id=lease, task_id=task_id)
        job = bob.call(
            "execute", dict(**common, operation_id="x", command="printf paired > result")
        )
        for _ in range(200):
            result = bob.call("command_status", dict(**common, command_id=job["command_id"]))
            if result["state"] not in ("queued", "starting", "running"):
                break
            time.sleep(0.05)
        assert result["state"] == "exited" and result["exit_code"] == 0, result
        assert (root / "result").read_text() == "paired"
        bob.call("disconnect", dict(lease_id=lease))
    finally:
        alice.close()
        bob.close()
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stderr.close()
