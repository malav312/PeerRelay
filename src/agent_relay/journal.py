import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from agent_relay.schemas import canonical, payload_hash


class Journal:
    def __init__(self, directory):
        self.directory = Path(directory).absolute()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink() or self.directory.resolve() != self.directory:
            raise ValueError("Journal must not use symlinks")
        os.chmod(self.directory, 0o700)
        self.path = self.directory / "journal.sqlite"
        if self.path.is_symlink():
            raise ValueError("Journal must be a regular file")
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS operations "
                "(id TEXT PRIMARY KEY, hash TEXT NOT NULL, result TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS roots (alias TEXT PRIMARY KEY, identity TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS local_proposals ("
                "id TEXT PRIMARY KEY, binding TEXT NOT NULL, digest TEXT NOT NULL, "
                "base_content TEXT, state TEXT NOT NULL, detail TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS local_proposal_events ("
                "sequence INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL, "
                "state TEXT NOT NULL, detail TEXT NOT NULL, "
                "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))"
            )
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def proposal(self, operation_id, binding, base_content, result):
        """Capture admitted proposal and its receipt in one durable transaction."""
        binding = {**binding, "base_sha256": payload_hash(base_content)}
        digest = payload_hash(binding)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT hash FROM operations WHERE id=?", (operation_id,)).fetchone()
            if not row or row[0] != payload_hash([binding["payload"], binding["grants"]]):
                raise ValueError("Proposal does not match admitted operation")
            db.execute(
                "INSERT OR IGNORE INTO local_proposals VALUES (?, ?, ?, ?, 'pending', '{}')",
                (operation_id, canonical(binding), digest, base_content),
            )
            db.execute(
                "UPDATE operations SET result=? WHERE id=? AND result IS NULL",
                (canonical(result), operation_id),
            )

    def root(self, alias, identity):
        value = canonical(identity)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT identity FROM roots WHERE alias=?", (alias,)).fetchone()
            if previous and previous[0] != value:
                raise ValueError("Registered root changed; explicit re-registration required")
            db.execute("INSERT OR IGNORE INTO roots VALUES (?, ?)", (alias, value))

    def begin(self, operation_id, payload, grants):
        fingerprint = payload_hash([payload, grants])
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT hash, result FROM operations WHERE id=?", (operation_id,)
            ).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    return {"status": "conflict"}
                return json.loads(previous[1]) if previous[1] is not None else {"status": "unknown"}
            db.execute("INSERT INTO operations VALUES (?, ?, NULL)", (operation_id, fingerprint))
        return None

    def finish(self, operation_id, result):
        with self.connect() as db:
            db.execute(
                "UPDATE operations SET result=? WHERE id=? AND result IS NULL",
                (canonical(result), operation_id),
            )

    def purge(self, operation_ids):
        # Keep hashes/tombstones; never allow an old operation to execute again.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for operation_id in operation_ids:
                row = db.execute(
                    "SELECT result FROM operations WHERE id=?", (operation_id,)
                ).fetchone()
                if row:
                    status = json.loads(row[0])["status"] if row[0] else "unknown"
                    db.execute(
                        "UPDATE operations SET result=? WHERE id=?",
                        (canonical({"status": status}), operation_id),
                    )
                proposal = db.execute(
                    "SELECT state FROM local_proposals WHERE id=?", (operation_id,)
                ).fetchone()
                if proposal:
                    state = proposal[0]
                    if state == "pending":
                        state = "rejected"
                    elif state in ("approved", "applying"):
                        state = "uncertain"
                    detail = canonical({"purged": True, "reason": "Retention purge; cannot apply"})
                    db.execute(
                        "UPDATE local_proposals SET binding='{}',base_content=NULL,"
                        "state=?,detail=? WHERE id=?",
                        (state, detail, operation_id),
                    )
                    db.execute(
                        "INSERT INTO local_proposal_events (proposal_id,state,detail) "
                        "VALUES (?,?,?)",
                        (operation_id, state, detail),
                    )

    def lookup(self, operation_id):
        with self.connect() as db:
            row = db.execute("SELECT result FROM operations WHERE id=?", (operation_id,)).fetchone()
            return json.loads(row[0]) if row and row[0] is not None else None
