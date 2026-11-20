"""Durable one-project relay. Roles, chat modes and responder leases are distinct."""

import difflib
import fcntl
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from pydantic import ValidationError

from .filesystem import Filesystem
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
    "submit",
    "status",
    "result",
    "review",
    "apply",
    "exit_mode",
    "cancel",
    "usage",
}
RESPONDER = {
    "response_mode",
    "heartbeat",
    "claim",
    "remote_tool",
    "propose",
    "complete",
    "disconnect",
}


class Relay:
    def __init__(self, root, state, clock=time.time):
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
        self.fs = Filesystem(
            {"project": {"path": str(root), "mode": "rw"}}, str(self.state / "files")
        )
        path = self.state / "relay.sqlite"
        if path.is_symlink():
            self.fs.close()
            os.close(self.owner_lock)
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
        CREATE TABLE IF NOT EXISTS counters(role TEXT PRIMARY KEY,calls INTEGER);
        """)
        # A server restart never resurrects an authority lease.
        self.db.execute("UPDATE lease SET active=0")
        self.db.execute("UPDATE tasks SET state='interrupted' WHERE state IN ('queued','running')")
        self.db.commit()

    def close(self):
        with self.lock:
            if not self.closed:
                self.db.close()
                self.fs.close()
                os.close(self.owner_lock)
                self.closed = True

    def sweep(self):
        self.db.execute("UPDATE lease SET active=0 WHERE expires<=?", (self.clock(),))
        self.db.execute("""UPDATE tasks SET state='interrupted' WHERE state IN ('queued','running')
            AND lease NOT IN (SELECT id FROM lease WHERE active=1)""")

    def call(self, role, method, arguments):
        allowed = REQUESTER if role == "requester" else RESPONDER if role == "responder" else set()
        if method not in allowed or not isinstance(arguments, dict):
            raise Denied("Role does not allow this operation")
        with self.lock:
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

    def _requester_mode(self):
        mode = new_id()
        self.db.execute("INSERT INTO modes VALUES (?,1)", (mode,))
        return {"mode_id": mode, "routing": "remote_only", "requester_model_tokens": None}

    def _response_mode(self):
        if self.db.execute("SELECT 1 FROM lease WHERE active=1").fetchone():
            raise Denied("A responder is already connected")
        lease = new_id()
        self.db.execute("INSERT INTO lease VALUES (?,?,1)", (lease, self.clock() + 60))
        return {"lease_id": lease, "state": "available", "lease_seconds": 60}

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
            "writes": "proposals_only",
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
        bounded(operation_id, 100)
        bounded(content, CONTENT_LIMIT, empty=True)
        tool = Tool(
            kind="create_file" if base_version == "absent" else "propose_replace",
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
            return {"proposal_id": old["id"], "digest": old["digest"], "status": old["status"]}
        if (
            self.db.execute("SELECT COUNT(*) FROM proposals WHERE task=?", (task_id,)).fetchone()[0]
            >= 32
        ):
            raise Denied("Proposal limit")
        self.count_tool(task)
        # Check traversal and current identity now, again at review, and again at apply.
        if base_version == "absent":
            with self.fs.parent("project", path) as (parent, name, _):
                try:
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise Denied("Create target already exists")
        else:
            current = self.fs.read(tool)
            if current["version"] != base_version:
                raise Denied("Proposal base changed")
        proposal = new_id()
        digest = payload_hash([proposal, task_id, lease_id, tool.model_dump()])
        self.db.execute(
            "INSERT INTO proposals(id,task,operation,hash,tool,digest) VALUES (?,?,?,?,?,?)",
            (proposal, task_id, operation_id, fingerprint, json.dumps(tool.model_dump()), digest),
        )
        return {"proposal_id": proposal, "digest": digest, "status": "proposed"}

    def _complete(self, lease_id, task_id, summary, answer=""):
        self.working(lease_id, task_id)
        bounded(summary, 2000)
        bounded(answer, 32768, empty=True)
        self.db.execute(
            "UPDATE tasks SET state='succeeded',summary=?,answer=? WHERE id=?",
            (summary, answer, task_id),
        )
        return {"task_id": task_id, "state": "succeeded"}

    def _status(self, mode_id, task_id):
        task = self.owned(mode_id, task_id)
        proposals = self.db.execute(
            "SELECT id,digest,status FROM proposals WHERE task=?", (task_id,)
        ).fetchall()
        return {
            "task_id": task_id,
            "state": task["state"],
            "summary": task["summary"],
            "proposals": [dict(p) for p in proposals],
            "responder_connected": bool(
                self.db.execute(
                    "SELECT 1 FROM lease WHERE id=? AND active=1", (task["lease"],)
                ).fetchone()
            ),
        }

    def _result(self, mode_id, task_id):
        task = self.owned(mode_id, task_id)
        return {"task_id": task_id, "summary": task["summary"], "answer": task["answer"]}

    def proposal(self, mode_id, proposal_id):
        proposal = self.db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if not proposal:
            raise Denied("Unknown proposal")
        task = self.owned(mode_id, proposal["task"])
        return proposal, task, Tool.model_validate_json(proposal["tool"])

    def _review(self, mode_id, proposal_id, offset=0):
        proposal, task, tool = self.proposal(mode_id, proposal_id)
        if type(offset) is not int or not 0 <= offset <= 2 * CONTENT_LIMIT:
            raise Denied("Invalid diff offset")
        if tool.expected_version == "absent":
            base = ""
        else:
            current = self.fs.read(tool)
            if current["version"] != tool.expected_version:
                raise Denied("Proposal base changed; request a fresh proposal")
            base = current["content"]
        diff = "".join(
            difflib.unified_diff(
                base.splitlines(keepends=True),
                tool.content.splitlines(keepends=True),
                fromfile=tool.path,
                tofile=tool.path,
            )
        )
        return {
            "proposal_id": proposal_id,
            "path": tool.path,
            "digest": proposal["digest"],
            "base_version": tool.expected_version,
            "diff": diff[offset : offset + 16000],
            "truncated": len(diff) > offset + 16000,
            "next_offset": offset + 16000 if len(diff) > offset + 16000 else None,
            "status": proposal["status"],
        }

    def _apply(self, mode_id, proposal_id, digest):
        proposal, task, tool = self.proposal(mode_id, proposal_id)
        if digest != proposal["digest"]:
            raise Denied("Approval digest mismatch")
        self.mode(mode_id)
        if proposal["status"] in ("applied", "unknown", "conflict", "denied"):
            return {"proposal_id": proposal_id, "status": proposal["status"]}
        self.lease(task["lease"])
        if task["state"] not in ("running", "succeeded"):
            raise Denied("Task no longer permits changes")
        receipt = self.fs.apply(proposal_id, tool, {"project": "rw"})
        self.db.execute(
            "UPDATE proposals SET status=? WHERE id=?", (receipt["status"], proposal_id)
        )
        return {"proposal_id": proposal_id, **receipt}

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
