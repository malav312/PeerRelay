"""Durable one-project relay. Roles, chat modes and responder leases are distinct."""

import fcntl
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from .changes import Changes
from .execution import ACTIVE, Executor, workspace
from .filesystem import Filesystem
from .questions import Questions
from .schemas import CONTENT_LIMIT, Tool, payload_hash


class Denied(Exception):
    pass


def bounded(value, limit, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise Denied("Invalid or oversized text")
    return value


def new_id():
    return str(uuid.uuid4())


REQUESTER = {
    "requester_mode",
    "mode_status",
    "submit",
    "status",
    "result",
    "changes",
    "exit_mode",
    "cancel",
    "usage",
    "answer_question",
}
RESPONDER = {
    "response_mode",
    "heartbeat",
    "response_update",
    "claim",
    "remote_tool",
    "propose",
    "complete",
    "disconnect",
    "execute",
    "command_status",
    "command_input",
    "command_cancel",
    "ask_question",
    "question_status",
}


class Relay:
    def __init__(self, root, state, clock=time.time, grant="rw", execution_backend=None):
        if grant not in ("ro", "rw", "exec"):
            raise ValueError("Invalid project grant")
        self.root = Path(root).resolve(strict=True)
        self.grant = grant
        self.execution_config = execution_backend or {"kind": "host"}
        self.clock = clock
        self.lock = threading.RLock()
        self.closed = False
        self.state = Path(state).absolute()
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.state.resolve() != self.state or self.state.stat().st_uid != os.getuid():
            raise ValueError("State must be an owned directory without symlinks")
        os.chmod(self.state, 0o700)
        root = Path(root).resolve(strict=True)
        if root == self.state or root in self.state.parents or self.state in root.parents:
            raise ValueError("Project and private state must be separate")
        self.owner_lock = os.open(
            self.state / "relay.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(self.owner_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.owner_lock)
            raise
        self.fs = self.db = self.executor = None
        try:
            self.fs = Filesystem(
                {"project": {"path": str(root), "mode": "rw" if grant == "exec" else grant}},
                str(self.state / "files"),
            )
            path = self.state / "relay.sqlite"
            if path.is_symlink():
                raise ValueError("State database cannot be a symlink")
            self.db = sqlite3.connect(path, check_same_thread=False)
            os.chmod(path, 0o600)
            self.db.row_factory = sqlite3.Row
            self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS modes(id TEXT PRIMARY KEY,active INTEGER);
            CREATE TABLE IF NOT EXISTS lease(id TEXT PRIMARY KEY,expires REAL,active INTEGER);
            CREATE TABLE IF NOT EXISTS tasks(
              id TEXT PRIMARY KEY,mode TEXT,operation TEXT,hash TEXT,prompt TEXT,state TEXT,
              lease TEXT,summary TEXT DEFAULT '',answer TEXT DEFAULT '',calls INTEGER DEFAULT 0,
              created REAL, UNIQUE(mode,operation));
            CREATE TABLE IF NOT EXISTS proposals(
              id TEXT PRIMARY KEY,task TEXT,operation TEXT,hash TEXT,tool TEXT,digest TEXT,
              status TEXT DEFAULT 'proposed',UNIQUE(task,operation));
            CREATE TABLE IF NOT EXISTS execution_modes(mode TEXT PRIMARY KEY,binding TEXT);
            CREATE TABLE IF NOT EXISTS counters(role TEXT PRIMARY KEY,calls INTEGER);
            """)
            # A server restart never resurrects an authority lease.
            self.db.execute("UPDATE lease SET active=0")
            self.db.execute(
                "UPDATE tasks SET state='interrupted' WHERE state IN ('queued','running')"
            )
            self.db.commit()
            self.executor = Executor(
                self.state, self.execution_authority, self.root, execution_backend
            )
            self.changes = Changes(self.db, self.fs, self.clock)
            self.questions = Questions(self.db)
            self.db.commit()
        except BaseException:
            try:
                if self.executor is not None:
                    self.executor.close()
            finally:
                try:
                    if self.db is not None:
                        self.db.close()
                finally:
                    try:
                        if self.fs is not None:
                            self.fs.close()
                    finally:
                        os.close(self.owner_lock)
            raise

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        self.executor.close()
        with self.lock:
            self.db.close()
            self.fs.close()
            os.close(self.owner_lock)

    def revoke_processes(self):
        active = {
            row[0]
            for row in self.db.execute(
                """SELECT t.id FROM tasks t JOIN modes m ON m.id=t.mode
            JOIN lease l ON l.id=t.lease WHERE t.state='running' AND m.active=1
            AND l.active=1 AND l.expires>?""",
                (self.clock(),),
            )
        }
        self.executor.cancel_inactive(active)
        self.questions.revoke()

    def sweep(self):
        self.db.execute("UPDATE lease SET active=0 WHERE expires<=?", (self.clock(),))
        self.db.execute("""UPDATE tasks SET state='interrupted' WHERE state IN ('queued','running')
            AND lease NOT IN (SELECT id FROM lease WHERE active=1)""")

        self.revoke_processes()
        self.changes.sweep()

    def call(self, role, method, arguments):
        allowed = REQUESTER if role == "requester" else RESPONDER if role == "responder" else set()
        if method not in allowed or not isinstance(arguments, dict):
            raise Denied("Role does not allow this operation")
        with self.lock:
            if self.closed:
                raise Denied("Relay closed")
            self.sweep()
            self.db.commit()
            try:
                self.db.execute("BEGIN IMMEDIATE")
                result = getattr(self, "_" + method)(**arguments)
                self.db.execute(
                    """INSERT INTO counters VALUES (?,1)
                    ON CONFLICT(role) DO UPDATE SET calls=calls+1""",
                    (role,),
                )
                self.revoke_processes()
                self.changes.sweep()
                self.db.commit()
                return result
            except (TypeError, KeyError, ValidationError) as error:
                self.db.rollback()
                raise Denied("Invalid request") from error
            except BaseException:
                self.db.rollback()
                raise

    def mode(self, mode_id, active=True):
        row = self.db.execute("SELECT * FROM modes WHERE id=?", (mode_id,)).fetchone()
        if not row or (active and not row["active"]):
            raise Denied("Requester mode is closed or unknown")
        return row

    def lease(self, lease_id):
        row = self.db.execute("SELECT * FROM lease WHERE id=? AND active=1", (lease_id,)).fetchone()
        if not row or row["expires"] <= self.clock():
            raise Denied("Responder disconnected; no local fallback")
        return row

    def owned(self, mode_id, task_id, active=False):
        self.mode(mode_id, active)
        row = self.db.execute(
            "SELECT * FROM tasks WHERE id=? AND mode=?", (task_id, mode_id)
        ).fetchone()
        if not row:
            raise Denied("Task not in this requester mode")
        return row

    def working(self, lease_id, task_id):
        self.lease(lease_id)
        row = self.db.execute(
            "SELECT * FROM tasks WHERE id=? AND lease=? AND state='running'", (task_id, lease_id)
        ).fetchone()
        if not row:
            raise Denied("Task is not active for this responder")
        self.mode(row["mode"])
        return row

    def execution_binding(self):
        return {"workspace": workspace(self.root), "backend": self.execution_config}

    def _requester_mode(self, execution=False):
        if type(execution) is not bool or (execution and self.grant != "exec"):
            raise Denied("Execution requires an explicit trusted-host exec pair")
        mode = new_id()
        self.db.execute("INSERT INTO modes VALUES (?,1)", (mode,))
        if execution:
            self.db.execute(
                "INSERT INTO execution_modes VALUES (?,?)",
                (mode, json.dumps(self.execution_binding())),
            )
        return {
            "mode_id": mode,
            "routing": "remote_only",
            "requester_model_tokens": None,
            "execution": execution,
            "workspace": str(self.root),
            "permission": (
                "container" if self.executor.backend.name == "docker" else "trusted_host"
            )
            if execution
            else "proposals_only",
        }

    def _mode_status(self, mode_id):
        self.mode(mode_id)
        binding = self.db.execute(
            "SELECT binding FROM execution_modes WHERE mode=?", (mode_id,)
        ).fetchone()
        return {
            "mode_id": mode_id,
            "execution": binding is not None,
            "execution_backend": self.executor.backend.name,
            "execution_quarantined": self.executor.quarantined(),
            "workspace": str(self.root),
            "workspace_matches": not binding or json.loads(binding[0]) == self.execution_binding(),
            "permission": (
                "container" if self.executor.backend.name == "docker" else "trusted_host"
            )
            if binding
            else "proposals_only",
            "responder_connected": bool(
                self.db.execute("SELECT 1 FROM lease WHERE active=1").fetchone()
            ),
            "routing": "remote_only",
        }

    def _response_mode(self):
        if self.db.execute("SELECT 1 FROM lease WHERE active=1").fetchone():
            raise Denied("A responder is already connected")
        lease = new_id()
        self.db.execute("INSERT INTO lease VALUES (?,?,1)", (lease, self.clock() + 60))
        return {"lease_id": lease, "state": "available", "lease_seconds": 60}

    def _response_update(self, lease_id):
        self.lease(lease_id)
        row = self.db.execute(
            "SELECT * FROM tasks WHERE lease=? AND state IN ('queued','running') "
            "ORDER BY CASE state WHEN 'running' THEN 0 ELSE 1 END, rowid LIMIT 1",
            (lease_id,),
        ).fetchone()
        if not row:
            return {"state": "idle"}
        if self.questions.pending(row["id"]):
            return {"state": "waiting_for_requester", "task_id": row["id"]}
        reply = self.db.execute(
            "SELECT id FROM questions WHERE task=? "
            "AND state IN ('answered','declined','cancelled') "
            "ORDER BY rowid DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        return {
            "state": row["state"],
            "task_id": row["id"],
            "event": "reply:" + reply[0] if reply else "task_available",
        }

    def _heartbeat(self, lease_id):
        self.lease(lease_id)
        self.db.execute("UPDATE lease SET expires=? WHERE id=?", (self.clock() + 60, lease_id))
        return {"state": "available"}

    def _submit(self, mode_id, operation_id, prompt):
        self.mode(mode_id)
        bounded(operation_id, 100)
        bounded(prompt, 20000)
        fingerprint = payload_hash(prompt)
        previous = self.db.execute(
            "SELECT * FROM tasks WHERE mode=? AND operation=?", (mode_id, operation_id)
        ).fetchone()
        if previous:
            if previous["hash"] != fingerprint:
                raise Denied("Operation ID reused with different text")
            return {"task_id": previous["id"], "state": previous["state"]}
        lease = self.db.execute("SELECT id FROM lease WHERE active=1").fetchone()
        if not lease:
            raise Denied("Responder unavailable; no local fallback")
        if (
            self.db.execute(
                "SELECT COUNT(*) FROM tasks WHERE state IN ('queued','running')"
            ).fetchone()[0]
            >= 32
        ):
            raise Denied("Pending task limit")
        task = new_id()
        self.db.execute(
            """INSERT INTO tasks(id,mode,operation,hash,prompt,state,lease,created)
            VALUES (?,?,?,?,?,'queued',?,?)""",
            (task, mode_id, operation_id, fingerprint, prompt, lease["id"], self.clock()),
        )
        return {"task_id": task, "state": "queued"}

    def _claim(self, lease_id):
        self.lease(lease_id)
        row = self.db.execute(
            "SELECT * FROM tasks WHERE lease=? AND state='running'", (lease_id,)
        ).fetchone()
        if not row:
            row = self.db.execute(
                "SELECT * FROM tasks WHERE lease=? AND state='queued' ORDER BY rowid LIMIT 1",
                (lease_id,),
            ).fetchone()
        if not row:
            return {"state": "idle"}
        if row["state"] == "queued" and self.db.execute(
            "SELECT 1 FROM execution_modes WHERE mode=?", (row["mode"],)
        ).fetchone():
            self.changes.start(row["id"])
        self.db.execute("UPDATE tasks SET state='running' WHERE id=?", (row["id"],))
        context = self.db.execute(
            """SELECT prompt,summary FROM tasks WHERE mode=? AND id!=?
            AND state='succeeded' ORDER BY rowid DESC LIMIT 3""",
            (row["mode"], row["id"]),
        ).fetchall()
        return {
            "task_id": row["id"],
            "state": "running",
            "prompt": row["prompt"],
            "context": [
                {"prompt": r["prompt"][:1000], "summary": r["summary"][:1000]}
                for r in reversed(context)
            ],
            "root": "project",
            "writes": "commands"
            if self.db.execute(
                "SELECT 1 FROM execution_modes WHERE mode=?", (row["mode"],)
            ).fetchone()
            else "proposals_only",
            "workspace": str(self.root),
        }

    def count_tool(self, task):
        if task["calls"] >= 128:
            raise Denied("Task tool limit reached")
        self.db.execute("UPDATE tasks SET calls=calls+1 WHERE id=?", (task["id"],))

    def _remote_tool(self, lease_id, task_id, kind, path, query=None, cursor=0):
        task = self.working(lease_id, task_id)
        if kind not in ("read", "list", "search"):
            raise Denied("Read tools only; propose changes separately")
        tool = Tool(kind=kind, root="project", path=path, query=query, cursor=cursor)
        self.count_tool(task)
        return self.fs.execute(new_id(), tool.model_dump(), {"project": "ro"})

    def _propose(self, lease_id, task_id, operation_id, path, content, base_version):
        task = self.working(lease_id, task_id)
        if self.grant not in ("rw", "exec") or base_version == "absent":
            raise Denied("Replacement proposals require an existing file and explicit rw grant")
        bounded(operation_id, 100)
        bounded(content, CONTENT_LIMIT, empty=True)
        tool = Tool(
            kind="propose_replace",
            root="project",
            path=path,
            content=content,
            expected_version=base_version,
        )
        fingerprint = payload_hash(tool.model_dump())
        old = self.db.execute(
            "SELECT * FROM proposals WHERE task=? AND operation=?", (task_id, operation_id)
        ).fetchone()
        if old:
            if old["hash"] != fingerprint:
                raise Denied("Proposal ID reused with different content")
            return {"proposal_id": old["id"], "status": old["status"]}
        if (
            self.db.execute("SELECT COUNT(*) FROM proposals WHERE task=?", (task_id,)).fetchone()[0]
            >= 32
        ):
            raise Denied("Proposal limit")
        self.count_tool(task)
        proposal = new_id()
        receipt = self.fs.execute(proposal, tool.model_dump(), {"project": "rw"})
        if receipt["status"] != "proposed":
            raise Denied("Proposal could not be captured; read the target again")
        with self.fs.journal.connect() as journal:
            digest = journal.execute(
                "SELECT digest FROM local_proposals WHERE id=?", (proposal,)
            ).fetchone()[0]
        self.db.execute(
            "INSERT INTO proposals(id,task,operation,hash,tool,digest) VALUES (?,?,?,?,?,?)",
            (proposal, task_id, operation_id, fingerprint, "{}", digest),
        )
        return {"proposal_id": proposal, "status": "proposed"}

    def _complete(self, lease_id, task_id, summary, answer=""):
        self.working(lease_id, task_id)
        if self.executor.quarantined():
            raise Denied("Unresolved container cleanup; task completion is blocked")
        if self.questions.pending(task_id):
            raise Denied("Resolve pending questions before completing")
        if any(job["state"] in ACTIVE for job in self.executor.summaries(task_id)):
            raise Denied("Finish or cancel active commands before completing")
        bounded(summary, 2000)
        bounded(answer, 32768, empty=True)
        self.changes.finish(task_id)
        self.db.execute(
            "UPDATE tasks SET state='succeeded',summary=?,answer=? WHERE id=?",
            (summary, answer, task_id),
        )
        return {"task_id": task_id, "state": "succeeded"}

    def _status(self, mode_id, task_id):
        task = self.owned(mode_id, task_id)
        proposals = self.db.execute(
            "SELECT id,status FROM proposals WHERE task=?", (task_id,)
        ).fetchall()
        return {
            "task_id": task_id,
            "state": task["state"],
            "summary": task["summary"],
            "changes": self.changes.summary(task_id),
            "proposals": [dict(p) for p in proposals],
            "commands": self.executor.summaries(task_id),
            "questions": self.questions.pending(task_id),
            "responder_connected": bool(
                self.db.execute(
                    "SELECT 1 FROM lease WHERE id=? AND active=1", (task["lease"],)
                ).fetchone()
            ),
        }

    def _changes(self, mode_id, task_id):
        self.owned(mode_id, task_id, active=True)
        return self.changes.get(task_id)

    def _result(self, mode_id, task_id):
        task = self.owned(mode_id, task_id)
        return {"task_id": task_id, "summary": task["summary"], "answer": task["answer"]}

    def _cancel(self, mode_id, task_id):
        self.owned(mode_id, task_id)
        self.db.execute(
            "UPDATE tasks SET state='cancelled' WHERE id=? AND state IN ('queued','running')",
            (task_id,),
        )
        return self._status(mode_id, task_id)

    def _exit_mode(self, mode_id):
        self.mode(mode_id, active=False)
        self.db.execute("UPDATE modes SET active=0 WHERE id=?", (mode_id,))
        self.db.execute(
            "UPDATE tasks SET state='cancelled' WHERE mode=? AND state IN ('queued','running')",
            (mode_id,),
        )
        return {"mode_id": mode_id, "state": "closed"}

    def _disconnect(self, lease_id):
        self.lease(lease_id)
        self.db.execute("UPDATE lease SET active=0 WHERE id=?", (lease_id,))
        self.db.execute(
            "UPDATE tasks SET state='interrupted' WHERE lease=? AND state IN ('queued','running')",
            (lease_id,),
        )
        return {
            "state": "disconnected",
            "active_work": "access_revoked",
            "model_process": "not_controlled",
        }

    def _usage(self, mode_id):
        self.mode(mode_id, active=False)
        counters = {r["role"]: r["calls"] for r in self.db.execute("SELECT * FROM counters")}
        return {
            "scope": "paired relay lifetime",
            "relay_requests": counters,
            "relay_model_calls": 0,
            "requester_model_tokens": None,
            "responder_model_tokens": None,
            "note": "Native client model usage is not visible to this MCP service.",
        }

    @contextmanager
    def execution_authority(self, lease_id, task_id):
        with self.lock:
            if self.closed:
                raise Denied("Relay closed")
            task = self.working(lease_id, task_id)
            if self.executor.quarantined():
                raise Denied("Unresolved container cleanup; execution is quarantined")
            if self.questions.pending(task_id):
                raise Denied("Requester response required before further execution")
            row = self.db.execute(
                "SELECT binding FROM execution_modes WHERE mode=?", (task["mode"],)
            ).fetchone()
            if self.grant != "exec" or not row or json.loads(row[0]) != self.execution_binding():
                raise Denied(
                    "Execution not authorized or workspace/branch changed; activate a new mode"
                )
            # Git workspace validation can outlast the lease while holding this lock.
            self.working(lease_id, task_id)
            yield task

    def _execute(
        self, lease_id, task_id, operation_id, command, cwd=".", timeout=300, terminal=False
    ):
        bounded(operation_id, 100)
        bounded(command, 65536)
        bounded(cwd, 4096)
        if type(timeout) is not int or not 1 <= timeout <= 86400 or type(terminal) is not bool:
            raise Denied("Invalid command options")
        with self.execution_authority(lease_id, task_id) as task:
            directory = (self.root / cwd).resolve(strict=True)
            if not directory.is_dir() or (
                directory != self.root and self.root not in directory.parents
            ):
                raise Denied("Command cwd must be inside the registered workspace")
            self.count_tool(task)
            try:
                return self.executor.submit(
                    task_id,
                    lease_id,
                    operation_id,
                    dict(command=command, cwd=str(directory), timeout=timeout, terminal=terminal),
                )
            except ValueError as error:
                raise Denied(str(error)) from error

    def command_owned(self, lease_id, task_id, command_id):
        self.working(lease_id, task_id)
        row = self.executor.record(command_id)
        if row["task"] != task_id or row["lease"] != lease_id:
            raise Denied("Command belongs to another task")

    def _command_status(self, lease_id, task_id, command_id, offset=0):
        self.command_owned(lease_id, task_id, command_id)
        return self.executor.status(command_id, offset)

    def _command_input(self, lease_id, task_id, command_id, operation_id, data, eof=False):
        bounded(operation_id, 100)
        bounded(data, 16384, empty=True)
        if len(data.encode()) > 16384 or type(eof) is not bool:
            raise Denied("Invalid command input")
        with self.execution_authority(lease_id, task_id):
            self.command_owned(lease_id, task_id, command_id)
            try:
                return self.executor.input(command_id, operation_id, data, eof)
            except ValueError as error:
                raise Denied(str(error)) from error

    def _command_cancel(self, lease_id, task_id, command_id):
        self.command_owned(lease_id, task_id, command_id)
        return self.executor.cancel(command_id)

    def _ask_question(self, lease_id, task_id, operation_id, message, choices=None):
        self.working(lease_id, task_id)
        bounded(operation_id, 100)
        bounded(message, 4000)
        choices = [] if choices is None else choices
        if not isinstance(choices, list) or len(choices) > 8:
            raise Denied("At most eight suggested choices")
        for choice in choices:
            bounded(choice, 200)
        return self.questions.ask(task_id, operation_id, message, choices)

    def _question_status(self, lease_id, task_id, question_id):
        self.working(lease_id, task_id)
        return self.questions.get(task_id, question_id)

    def _answer_question(self, mode_id, task_id, question_id, action, answer=""):
        task = self.owned(mode_id, task_id, active=True)
        self.working(task["lease"], task_id)
        if action not in ("accept", "decline", "cancel"):
            raise Denied("Invalid response action")
        bounded(answer, 8000, empty=action != "accept")
        if action != "accept" and answer:
            raise Denied("Decline/cancel must not carry an answer")
        return self.questions.answer(task_id, question_id, action, answer)
