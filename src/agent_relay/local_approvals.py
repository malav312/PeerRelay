"""Human-only adapter. No RPC, model, token read, or relay lifecycle mutation."""

import sqlite3
import time
from pathlib import Path

from .approvals import Approvals
from .client import private_file
from .filesystem import Filesystem


class LocalDecisions:
    def __init__(self, config, clock=time.time):
        self.clock = clock
        self.grant = config.get("grant", "rw")  # Preserve pre-grant paired configurations.
        state = Path(config["state_dir"]).absolute()
        if state.resolve() != state:
            raise ValueError("State cannot use symlinks")
        database = private_file(state / "relay.sqlite")
        protected = [config[key] for key in ("_path", "key_file", "ca_file") if key in config]
        protected.extend(config.get("tokens", {}).values())
        self.fs = Filesystem(
            {"project": {"path": config["root"], "mode": self.grant}},
            state / "files",
            protected_paths=protected,
            allow_unavailable_roots=True,
        )
        try:
            self.db = sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=30)
            self.db.row_factory = sqlite3.Row
            self.core = Approvals(self.fs)
        except BaseException:
            self.fs.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()
        self.fs.close()

    def _registered(self, ident):
        row = self.db.execute("SELECT task FROM proposals WHERE id=?", (ident,)).fetchone()
        if not row:
            raise ValueError("Unknown relay proposal")
        with self.fs.journal.connect() as journal:
            captured = journal.execute(
                "SELECT 1 FROM local_proposals WHERE id=?", (ident,)
            ).fetchone()
        if not captured:
            raise ValueError("Legacy proposal lacks local binding; request a fresh proposal")
        return row["task"]

    def inspect(self, ident):
        self._registered(ident)
        return self.core.inspect(ident)

    def _authorize(self, task):
        live = self.db.execute(
            """SELECT t.state,m.active AS mode_active,
            l.active AS lease_active,l.expires FROM tasks t
            JOIN modes m ON m.id=t.mode JOIN lease l ON l.id=t.lease WHERE t.id=?""",
            (task,),
        ).fetchone()
        if (
            self.grant != "rw"
            or not live
            or not live["mode_active"]
            or not live["lease_active"]
            or live["expires"] <= self.clock()
            or live["state"] not in ("running", "succeeded")
        ):
            raise ValueError("Application authority revoked; no application performed")

    def decide(self, ident, digest, decision):
        # Same order as Relay.call: relay transaction -> filesystem execution lock.
        # Disconnect/cancel cannot acknowledge until an already-started apply finishes.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            task = self._registered(ident)
            return self.core.decide(
                ident, digest, decision, authorize=lambda: self._authorize(task)
            )
