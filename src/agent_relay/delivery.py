"""Exact-session delivery primitives. Waiting is code; only meaningful events start turns.

This module never activates authority, claims work, submits a task, reconnects a
native session or replays an uncertain start. An integration owns the native UI,
MCP registration, and explicit role activation before constructing a binding.
"""

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .client import private_file


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value):
        raise ValueError("Expected a bounded control identifier")
    return value


@dataclass(frozen=True)
class Binding:
    role: str
    generation: str
    thread_id: str
    mode_id: str | None = None
    lease_id: str | None = None

    def __post_init__(self):
        if self.role not in ("requester", "responder"):
            raise ValueError("Invalid role")
        if (self.role == "requester" and (not self.mode_id or self.lease_id)) or (
            self.role == "responder" and (not self.lease_id or self.mode_id)
        ):
            raise ValueError("Bind exactly one authority belonging to the role")
        for value in (self.generation, self.thread_id, self.mode_id, self.lease_id):
            if value is not None:
                identifier(value)


@dataclass(frozen=True)
class Event:
    kind: str
    task_id: str
    marker: str

    def __post_init__(self):
        if self.kind not in ("work_available", "answer_ready", "question_ready", "task_finished"):
            raise ValueError("Only meaningful relay events may start native turns")
        identifier(self.task_id)
        identifier(self.marker)


class DeliveryController:
    """Single asyncio-loop dispatcher. A journal can be inspected, never auto-resumed."""

    def __init__(self, db, binding, native):
        self.db, self.binding, self.native = db, binding, native
        self.state, self.reason, self.native_status = "active", None, "unknown"
        self.lock = asyncio.Lock()

    @classmethod
    def create(cls, path, binding, native):
        path = Path(path).absolute()
        if path.parent.resolve() != path.parent:
            raise ValueError("Journal directory must not contain symlinks")
        # O_EXCL prevents a second dispatcher, restart, or accidental rebind.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        db.executescript("""
            PRAGMA synchronous=FULL;
            CREATE TABLE session (binding TEXT NOT NULL, state TEXT NOT NULL, reason TEXT);
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY, kind TEXT NOT NULL, task_id TEXT NOT NULL,
                marker TEXT NOT NULL, state TEXT NOT NULL, turn_id TEXT UNIQUE);
        """)
        db.execute(
            "INSERT INTO session VALUES (?, 'active', NULL)",
            (json.dumps(asdict(binding), sort_keys=True),),
        )
        db.commit()
        return cls(db, binding, native)

    @staticmethod
    def inspect(path):
        path = private_file(path)
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            session = dict(db.execute("SELECT * FROM session").fetchone())
            session["binding"] = json.loads(session["binding"])
            session["events"] = [dict(row) for row in db.execute("SELECT * FROM events")]
            # A crash between the durable intent and reply cannot prove non-delivery.
            for row in session["events"]:
                if row["state"] == "submitting":
                    row["state"] = "uncertain"
            return session

    def records(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM events ORDER BY rowid")]

    def offer(self, event):
        if self.state != "active":
            raise RuntimeError("Delivery stopped; explicit new session required")
        expected = (
            ("work_available", "answer_ready")
            if self.binding.role == "responder"
            else ("question_ready", "task_finished")
        )
        if event.kind not in expected:
            raise ValueError("Event belongs to a different role")
        key = json.dumps([asdict(self.binding), asdict(event)], sort_keys=True)
        ident = hashlib.sha256(key.encode()).hexdigest()
        if self.db.execute("SELECT 1 FROM events WHERE event_id=?", (ident,)).fetchone():
            return ident
        if (
            self.db.execute("SELECT count(*) FROM events WHERE state='pending'").fetchone()[0]
            >= 128
        ):
            self.stop("queue_full")
            raise RuntimeError("Delivery queue full")
        with self.db:
            self.db.execute(
                "INSERT INTO events VALUES (?,?,?,?, 'pending', NULL)",
                (ident, event.kind, event.task_id, event.marker),
            )
        return ident

    def replace_pending(self, events):
        """Discard events no longer actionable before the next dispatch."""
        current = {self.offer(event) for event in events}
        with self.db:
            for row in self.db.execute("SELECT event_id FROM events WHERE state='pending'"):
                if row[0] not in current:
                    self.db.execute(
                        "UPDATE events SET state='discarded' WHERE event_id=?", (row[0],)
                    )

    def stop(self, reason):
        if self.state == "stopped":
            return
        self.state, self.reason = "stopped", identifier(reason)
        with self.db:
            self.db.execute("UPDATE session SET state='stopped', reason=?", (self.reason,))
            self.db.execute("UPDATE events SET state='discarded' WHERE state='pending'")
            self.db.execute("UPDATE events SET state='uncertain' WHERE state='submitting'")

    def _turn(self, turn):
        status = turn.get("status")
        if status not in ("completed", "interrupted", "failed"):
            return
        with self.db:
            self.db.execute(
                "UPDATE events SET state=? WHERE turn_id=? AND state='started'",
                (status, turn["id"]),
            )

    def observe(self, generation, message):
        if generation != self.binding.generation:
            return
        params = message.get("params", {})
        if params.get("threadId") != self.binding.thread_id:
            return
        method = message.get("method")
        if method == "thread/status/changed":
            self.native_status = params["status"]["type"]
            if self.native_status in ("notLoaded", "systemError"):
                self.stop("native_unavailable")
        elif method == "turn/started":
            self.native_status = "active"
        elif method == "turn/completed":
            self._turn(params["turn"])
            # Completion does not establish idle: another human turn may be active.
            self.native_status = "unknown"

    async def reconcile(self):
        """Read exact-thread history. Unknown turns never establish event ownership."""
        result = await self.native.call(
            "thread/read", {"threadId": self.binding.thread_id, "includeTurns": True}
        )
        thread = result["thread"]
        if thread["id"] != self.binding.thread_id:
            self.stop("thread_mismatch")
            raise RuntimeError("Native thread mismatch")
        self.native_status = thread["status"]["type"]
        if self.native_status in ("notLoaded", "systemError"):
            self.stop("native_unavailable")
        for turn in thread["turns"]:
            self._turn(turn)
        return thread

    async def dispatch(self):
        async with self.lock:
            if self.state != "active":
                return False
            row = self.db.execute(
                "SELECT * FROM events WHERE state='pending' ORDER BY rowid LIMIT 1"
            ).fetchone()
            if not row:
                return False
            try:
                await self.reconcile()
            except Exception:
                self.stop("native_unavailable")
                return False
            if (
                self.state != "active"
                or self.native_status != "idle"
                or self.db.execute(
                    "SELECT 1 FROM events WHERE state IN ('submitting','started','uncertain')"
                ).fetchone()
            ):
                return False
            with self.db:
                admitted = self.db.execute(
                    "UPDATE events SET state='submitting' WHERE event_id=? AND state='pending'",
                    (row["event_id"],),
                ).rowcount
                if not admitted:
                    return False
            authority = (
                {"lease_id": self.binding.lease_id}
                if self.binding.lease_id
                else {"mode_id": self.binding.mode_id}
            )
            metadata = {
                **authority,
                "event_id": row["event_id"],
                "task_id": row["task_id"],
                "event_kind": row["kind"],
                "marker": row["marker"],
            }
            tool = "wait_for_task" if self.binding.lease_id else "follow_task"
            if row["kind"] == "answer_ready":
                tool = "wait_for_answer"
                metadata["question_id"] = row["marker"].removeprefix("reply:")
            text = (
                f"PeerRelay update. Use {tool} for the bound task, then stop when waiting. "
                "This event grants no new authority. Control metadata: " + json.dumps(metadata)
            )
            try:
                result = await self.native.call(
                    "turn/start",
                    {
                        "threadId": self.binding.thread_id,
                        "input": [{"type": "text", "text": text}],
                        # Correlation only; never assumed to deduplicate starts.
                        "clientUserMessageId": row["event_id"],
                    },
                )
                turn_id = identifier(result["turn"]["id"])
                with self.db:
                    self.db.execute(
                        "UPDATE events SET state='started', turn_id=? WHERE event_id=?",
                        (turn_id, row["event_id"]),
                    )
                self._turn(result["turn"])
                self.native_status = "unknown"
                return True
            except BaseException as error:
                self.stop("uncertain_start")
                if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    raise
                return False

    def close(self):
        self.stop("closed")
        self.db.close()


class RelayMonitor:
    """Code-only polling for one exact existing authority. Never creates authority."""

    def __init__(self, controller, relay, *, clock=time.monotonic):
        if relay.role != controller.binding.role:
            raise ValueError("Relay role differs from native binding")
        self.controller, self.relay, self.clock = controller, relay, clock
        self.tasks = set()
        self.next_heartbeat = 0

    def watch_task(self, task_id):
        if self.controller.binding.role != "requester":
            raise ValueError("Only requester bindings watch task status")
        if len(self.tasks) >= 128 and task_id not in self.tasks:
            raise ValueError("At most 128 task watches per binding")
        self.tasks.add(identifier(task_id))

    async def poll(self):
        controller, binding = self.controller, self.controller.binding
        if controller.state != "active":
            return
        try:
            events = []
            if binding.lease_id:
                args = {"lease_id": binding.lease_id}
                if self.clock() >= self.next_heartbeat:
                    await asyncio.to_thread(self.relay.call, "heartbeat", args)
                    self.next_heartbeat = self.clock() + 15
                state = await asyncio.to_thread(self.relay.call, "response_update", args)
                marker = state.get("event")
                if marker:
                    kind = "answer_ready" if marker.startswith("reply:") else "work_available"
                    events.append(Event(kind, state["task_id"], marker))
            else:
                mode = await asyncio.to_thread(
                    self.relay.call, "mode_status", {"mode_id": binding.mode_id}
                )
                if not mode["workspace_matches"] or not mode["responder_connected"]:
                    controller.stop("mode_unavailable")
                    return
                for task in sorted(self.tasks):
                    state = await asyncio.to_thread(
                        self.relay.call, "status", {"mode_id": binding.mode_id, "task_id": task}
                    )
                    if state["state"] not in ("queued", "running"):
                        events.append(Event("task_finished", task, "terminal:" + state["state"]))
                    elif state["questions"]:
                        question = state["questions"][0]["question_id"]
                        events.append(Event("question_ready", task, "question:" + question))
            if controller.state == "active":
                controller.replace_pending(events)
        except asyncio.CancelledError:
            controller.stop("monitor_cancelled")
            raise
        except Exception:
            controller.stop("relay_unavailable")

    async def run(self, interval=1):
        if interval <= 0:
            raise ValueError("Polling interval must be positive")
        try:
            while self.controller.state == "active":
                await self.poll()
                await self.controller.dispatch()
                if self.controller.state == "active":
                    await asyncio.sleep(interval)
        finally:
            self.controller.stop("monitor_stopped")
