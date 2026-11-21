"""Durable, explicitly trusted-host command execution. This is not a sandbox."""

import json
import os
import pty
import selectors
import signal
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .backends import backend
from .schemas import payload_hash

ACTIVE = ("queued", "starting", "running")
LOG_LIMIT = 1024 * 1024
PAGE = 16384


def environment():
    # Do not forward the server's arbitrary environment or any peer credentials.
    env = {
        k: os.environ[k]
        for k in ("PATH", "HOME", "USER", "LOGNAME", "LANG", "TMPDIR")
        if k in os.environ
    }
    env.setdefault("PATH", "/usr/bin:/bin")
    env["TERM"] = "xterm-256color"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def workspace(root):
    root = Path(root)
    stat = root.stat()
    if root.resolve() != root:
        raise ValueError("Workspace identity changed")
    binding = {"root": str(root), "device": stat.st_dev, "inode": stat.st_ino, "git": None}
    env = environment()
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")

    def git(*args):
        return subprocess.run(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=/dev/null",
                "-C",
                str(root),
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=3,
            env=env,
        )

    result = git("rev-parse", "--show-toplevel", "--absolute-git-dir")
    if result.returncode == 0:
        branch = git("symbolic-ref", "-q", "HEAD")
        head = (
            branch.stdout.strip()
            if branch.returncode == 0
            else git("rev-parse", "HEAD").stdout.strip()
        )
        if not head:
            raise ValueError("Cannot identify workspace branch")
        binding["git"] = [result.stdout.strip(), head]
    return binding


class Executor:
    def __init__(self, state, authorize, root=None, execution_backend=None):
        path = Path(state) / "execution.sqlite"
        if path.is_symlink():
            raise ValueError("Execution journal cannot be symlinked")
        self.db = sqlite3.connect(path, check_same_thread=False)
        try:
            os.chmod(path, 0o600)
            self.db.row_factory = sqlite3.Row
            self.lock = threading.RLock()
            self.authorize = authorize
            self.live = {}
            self.closed = False
            self.backend = backend(root, state, execution_backend)
            self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS container_resources(
              command TEXT PRIMARY KEY, name TEXT, removed INTEGER DEFAULT 0,
              confirmed INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS commands(
              id TEXT PRIMARY KEY, task TEXT, lease TEXT, operation TEXT, hash TEXT,
              spec TEXT, state TEXT, output BLOB DEFAULT X'', truncated INTEGER DEFAULT 0,
              exit_code INTEGER, detail TEXT DEFAULT '', UNIQUE(task,operation));
            CREATE TABLE IF NOT EXISTS command_inputs(
              command TEXT, operation TEXT, hash TEXT, result TEXT,
              PRIMARY KEY(command,operation));
            UPDATE commands SET state='uncertain',detail='Server restarted; never replay'
              WHERE state IN ('queued','starting','running');
            """)
            self.db.commit()
            if "confirmed" not in {
                r[1] for r in self.db.execute("PRAGMA table_info(container_resources)")
            }:
                self.db.execute(
                    "ALTER TABLE container_resources ADD COLUMN confirmed INTEGER DEFAULT 0"
                )
            pending = self.db.execute(
                "SELECT command,name,confirmed FROM container_resources WHERE removed=0"
            ).fetchall()
            if pending and self.backend.name != "docker":
                raise ValueError("Unremoved containers require the Docker backend before startup")
            for row in pending:
                if not self.backend.cleanup(row["name"], confirmed=bool(row["confirmed"])):
                    raise ValueError(
                        "Container cleanup could not be verified; restore Docker first"
                    )
                self.db.execute(
                    "UPDATE container_resources SET removed=1 WHERE command=?", (row["command"],)
                )
            self.db.commit()
        except BaseException:
            self.db.close()
            if hasattr(self, "backend"):
                self.backend.close()
            raise

    def submit(self, task, lease, operation, spec):
        fingerprint = payload_hash(spec)
        with self.lock, self.db:
            old = self.db.execute(
                "SELECT id,hash FROM commands WHERE task=? AND operation=?", (task, operation)
            ).fetchone()
            if old:
                if old["hash"] != fingerprint:
                    raise ValueError("Command operation reused with different payload")
                return {"command_id": old["id"]}
            if (
                self.closed
                or self.db.execute(
                    "SELECT COUNT(*) FROM commands WHERE task=? "
                    "AND state IN ('queued','starting','running')",
                    (task,),
                ).fetchone()[0]
                >= 4
            ):
                raise ValueError("Command concurrency limit or executor closed")
            ident = str(uuid.uuid4())
            self.db.execute(
                "INSERT INTO commands(id,task,lease,operation,hash,spec,state) "
                "VALUES (?,?,?,?,?,?,'queued')",
                (ident, task, lease, operation, fingerprint, json.dumps(spec)),
            )
        control = {"process": None, "stop": None, "input": None, "terminal": spec["terminal"]}
        thread = threading.Thread(
            target=self._run, args=(ident, task, lease, spec, control), daemon=True
        )
        control["thread"] = thread
        with self.lock:
            self.live[ident] = control
        try:
            thread.start()
        except RuntimeError:
            with self.lock:
                self.live.pop(ident, None)
                self._update(ident, state="refused", detail="Command worker could not start")
        return {"command_id": ident}

    def _update(self, ident, **values):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE commands SET " + ",".join(k + "=?" for k in values) + " WHERE id=?",
                (*values.values(), ident),
            )

    def _append(self, ident, chunk):
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT output,truncated FROM commands WHERE id=?", (ident,)
            ).fetchone()
            remaining = LOG_LIMIT - len(row["output"])
            self.db.execute(
                "UPDATE commands SET output=?,truncated=? WHERE id=?",
                (
                    row["output"] + chunk[:remaining],
                    int(row["truncated"] or len(chunk) > remaining),
                    ident,
                ),
            )

    @staticmethod
    def _signal(control, signum):
        process = control["process"]
        if process is not None:
            try:
                os.killpg(process.pid, signum)
            except ProcessLookupError:
                pass
            except PermissionError:
                # macOS can briefly return EPERM while a process group exits.
                # The worker retries, then records uncertainty if exit cannot be confirmed.
                return False
        return True

    def _run(self, ident, task, lease, spec, control):
        process = None
        container_name = "peerrelay-" + ident
        container_started = False
        container_confirmed = False
        master = slave = None
        selector = selectors.DefaultSelector()
        try:
            # The caller holds the relay admission lock through spawn. Cancellation
            # acquires that same lock, so acknowledged revocation cannot race launch.
            with self.authorize(lease, task), self.lock:
                if control["stop"] or self.closed:
                    self._update(ident, state="cancelled")
                    return
                self._update(ident, state="starting")  # Durable intent before any effect.
                if spec["terminal"]:
                    master, slave = pty.openpty()
                if self.backend.name == "docker":
                    with self.db:
                        self.db.execute(
                            "INSERT INTO container_resources(command,name) VALUES (?,?)",
                            (ident, container_name),
                        )
                    container_started = True  # Durable before creating a STOPPED container.
                    self.backend.prepare(container_name, spec)
                    container_confirmed = True
                    with self.db:
                        self.db.execute(
                            "UPDATE container_resources SET confirmed=1 WHERE command=?", (ident,)
                        )
                    if control["stop"] or self.closed:
                        self._update(ident, state="cancelled")
                        return
                with self.authorize(lease, task):
                    argv, directory, env = self.backend.launch(container_name, spec)
                    process = subprocess.Popen(
                        argv,
                        cwd=directory,
                        env=env,
                        stdin=slave if slave is not None else subprocess.PIPE,
                        stdout=slave if slave is not None else subprocess.PIPE,
                        stderr=slave if slave is not None else subprocess.STDOUT,
                        start_new_session=True,
                        bufsize=0,
                    )
                control["process"] = process
                fd = master if master is not None else process.stdout.fileno()
                control["input"] = master if master is not None else process.stdin.fileno()
                os.set_blocking(fd, False)
                os.set_blocking(control["input"], False)
                if slave is not None:
                    os.close(slave)
                    slave = None
                selector.register(fd, selectors.EVENT_READ)
                self._update(ident, state="running")
            deadline = time.monotonic() + spec["timeout"]
            stopping = None
            exited_at = None
            while True:
                with self.lock:
                    if time.monotonic() >= deadline and not control["stop"]:
                        control["stop"] = "timed_out"
                    if control["stop"]:
                        if stopping is None:
                            self._signal(control, signal.SIGTERM)
                            stopping = time.monotonic()
                        elif time.monotonic() - stopping >= 0.3:
                            self._signal(control, signal.SIGKILL)
                            if time.monotonic() - stopping > 2 and process.poll() is None:
                                raise RuntimeError("Process termination could not be confirmed")
                for key, _ in selector.select(0.05):
                    try:
                        chunk = os.read(key.fd, PAGE)
                    except BlockingIOError:
                        continue
                    except OSError:
                        chunk = b""  # PTYs report EIO at EOF.
                    if chunk:
                        self._append(ident, chunk)
                    else:
                        selector.unregister(key.fd)
                if process.poll() is not None:
                    # Do not leave background children holding pipes after the shell exits.
                    with self.lock:
                        self._signal(control, signal.SIGKILL)
                    if not selector.get_map():
                        break
                    if exited_at is None:
                        exited_at = time.monotonic()
                    elif time.monotonic() - exited_at > 1:
                        raise RuntimeError("Output stream stayed open after leader exit")
            if container_started:
                if not self.backend.cleanup(container_name, confirmed=container_confirmed):
                    raise RuntimeError("Container removal could not be verified")
                with self.lock, self.db:
                    self.db.execute(
                        "UPDATE container_resources SET removed=1 WHERE command=?", (ident,)
                    )
                container_started = False
            self._update(ident, state=control["stop"] or "exited", exit_code=process.returncode)
        except BaseException as error:
            try:
                with self.lock:
                    self._signal(control, signal.SIGKILL)
                if process is not None:
                    process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
            self._update(
                ident,
                state="uncertain" if process else (control["stop"] or "refused"),
                detail=type(error).__name__,
            )
        finally:
            if container_started:
                if self.backend.cleanup(container_name, confirmed=container_confirmed):
                    with self.lock, self.db:
                        self.db.execute(
                            "UPDATE container_resources SET removed=1 WHERE command=?", (ident,)
                        )
                else:
                    self._update(
                        ident,
                        state="uncertain",
                        detail="Container cleanup unverified; restore Docker",
                    )
            selector.close()
            with self.lock:
                control["input"] = None
                self.live.pop(ident, None)
            for fd in (master, slave):
                if fd is not None:
                    os.close(fd)
            if process is not None:
                for stream in (process.stdin, process.stdout):
                    if stream:
                        stream.close()

    def record(self, ident):
        with self.lock:
            row = self.db.execute("SELECT * FROM commands WHERE id=?", (ident,)).fetchone()
            if row is None:
                raise ValueError("Unknown command")
            return dict(row)

    def status(self, ident, offset=0):
        if type(offset) is not int or not 0 <= offset <= LOG_LIMIT:
            raise ValueError("Invalid output offset")
        row = self.record(ident)
        output = row["output"]
        # Byte offsets allow bounded reads even when UTF-8 was split by process writes.
        chunk = output[offset : offset + PAGE]
        return dict(
            command_id=ident,
            state=row["state"],
            exit_code=row["exit_code"],
            output=chunk.decode("utf-8", errors="replace"),
            next_offset=offset + len(chunk),
            truncated=bool(row["truncated"]),
            detail=row["detail"],
        )

    def input(self, ident, operation, data, eof=False):
        fingerprint = payload_hash([data, eof])
        with self.lock:
            old = self.db.execute(
                "SELECT hash,result FROM command_inputs WHERE command=? AND operation=?",
                (ident, operation),
            ).fetchone()
            if old:
                if old["hash"] != fingerprint:
                    raise ValueError("Input operation reused")
                return json.loads(old["result"])
            control = self.live.get(ident)
            if not control or control["input"] is None or control["stop"]:
                raise ValueError("Command does not accept input")
            result = {"state": "uncertain", "bytes_written": 0}
            with self.db:
                self.db.execute(
                    "INSERT INTO command_inputs VALUES (?,?,?,?)",
                    (ident, operation, fingerprint, json.dumps(result)),
                )
            try:
                raw = data.encode()
                written = os.write(control["input"], raw) if raw else 0
                result = dict(
                    state="sent" if written == len(raw) else "partial", bytes_written=written
                )
                if eof and written == len(raw):
                    if control["terminal"]:
                        if os.write(control["input"], b"\x04") != 1:
                            result["state"] = "uncertain"
                    else:
                        control["process"].stdin.close()
                        control["input"] = None
            except (OSError, ValueError):
                result["state"] = "uncertain"  # Never replay potentially delivered input.
            with self.db:
                self.db.execute(
                    "UPDATE command_inputs SET result=? WHERE command=? AND operation=?",
                    (json.dumps(result), ident, operation),
                )
            return result

    def cancel(self, ident):
        with self.lock:
            control = self.live.get(ident)
            if control and not control["stop"]:
                control["stop"] = "cancelled"
                self._signal(control, signal.SIGTERM)
        return self.status(ident)

    def cancel_inactive(self, active_tasks):
        with self.lock:
            rows = self.db.execute(
                "SELECT id,task FROM commands WHERE state IN ('queued','starting','running')"
            ).fetchall()
            for row in rows:
                if row["task"] not in active_tasks:
                    self.cancel(row["id"])

    def quarantined(self):
        with self.lock:
            return bool(
                self.db.execute(
                    "SELECT 1 FROM container_resources r LEFT JOIN commands c ON c.id=r.command "
                    "WHERE r.removed=0 AND (c.id IS NULL OR c.state NOT IN "
                    "('queued','starting','running'))"
                ).fetchone()
            )

    def summaries(self, task):
        with self.lock:
            return [
                dict(row)
                for row in self.db.execute(
                    "SELECT id AS command_id,state,exit_code FROM commands "
                    "WHERE task=? ORDER BY rowid",
                    (task,),
                )
            ]

    def close(self):
        with self.lock:
            self.closed = True
            controls = list(self.live.items())
            for ident, _ in controls:
                self.cancel(ident)
        for _, control in controls:
            control["thread"].join(timeout=5)
        with self.lock:
            if any(c["thread"].is_alive() for _, c in controls):
                raise RuntimeError("Command workers did not stop")
            self.db.close()
            if hasattr(self, "backend"):
                self.backend.close()
