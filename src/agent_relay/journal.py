import json
import os
import sqlite3
from pathlib import Path

from .schemas import canonical, payload_hash


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
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def root(self, alias, identity):
        value = canonical(identity)
        with self.connect() as db:
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

    def lookup(self, operation_id):
        with self.connect() as db:
            row = db.execute("SELECT result FROM operations WHERE id=?", (operation_id,)).fetchone()
            return json.loads(row[0]) if row and row[0] is not None else None
