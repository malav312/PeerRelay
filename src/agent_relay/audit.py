"""Bounded authentication audit. Never record authorization headers or request bodies."""

import json
import logging
import logging.handlers
import os
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from time import monotonic


class PrivateRotatingHandler(logging.handlers.RotatingFileHandler):
    def _open(self):
        fd = os.open(
            self.baseFilename, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600
        )
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "a", encoding="utf-8")


class AuthAudit:
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.resolve() != directory or directory.stat().st_uid != os.getuid():
            raise ValueError("Unsafe audit directory")
        os.chmod(directory, 0o700)
        path = directory / "auth-audit.jsonl"
        for item in (path, Path(str(path) + ".1"), Path(str(path) + ".2")):
            if item.is_symlink():
                raise ValueError("Audit log cannot be symlinked")
        fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        self.handler = PrivateRotatingHandler(path, maxBytes=1024 * 1024, backupCount=2)
        self.lock = Lock()
        self.entries = OrderedDict()

    def failed(self, peer):
        now = monotonic()
        with self.lock:
            start, count = self.entries.pop(peer, (now, 0))
            if now - start > 60:
                start, count = now, 0
            count += 1
            self.entries[peer] = (start, count)
            while len(self.entries) > 1024:
                self.entries.popitem(last=False)
            # First failure and threshold only, to avoid attacker-driven log floods.
            if count in (1, 20):
                payload = json.dumps(
                    {
                        "at": datetime.now(timezone.utc).isoformat(),
                        "event": "authentication_failed",
                        "peer": peer,
                        "count_in_window": count,
                    }
                )
                self.handler.emit(
                    logging.LogRecord("peerrelay.audit", logging.WARNING, "", 0, payload, (), None)
                )
            return count >= 20

    def close(self):
        self.handler.close()
