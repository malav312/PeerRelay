"""Bounded task-interval reviews. Artifacts are data, never authority to apply/undo."""

import difflib
import hashlib
import json
import os
import stat
import time

from .filesystem import Changed, Refused, reserved
from .policy import blocked

FILE_BYTES = 128 * 1024
TOTAL_BYTES = 1024 * 1024
MAX_ENTRIES = 10000
MAX_FILES = 2000
RETENTION = 86400
MAX_ARTIFACTS = 64
REVIEW_EXCLUDES = (
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".next",
    ".turbo",
)


def stamp(st):
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_mode]


def snapshot(fs):
    """Use descriptor-relative, no-follow reads and the existing project exclusions.

    An incomplete traversal fails closed; otherwise an unvisited file could appear
    deleted. Large/binary files retain metadata/digests only, never their contents.
    """
    patterns = (*fs.policy("project"), *REVIEW_EXCLUDES)
    files, excluded, pending = {}, [], ["."]
    total = entries = 0
    deadline = time.monotonic() + 5
    while pending:
        path = pending.pop()
        with fs.parent("project", path) as (parent, name, device):
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                if os.fstat(fd).st_dev != device:
                    raise Refused()
                with os.scandir(fd) as children:
                    for entry in children:
                        entries += 1
                        if entries > MAX_ENTRIES or time.monotonic() > deadline:
                            raise Refused()
                        child = entry.name if path == "." else path + "/" + entry.name
                        if blocked(child, patterns) or reserved(entry.name):
                            continue
                        if len(child) > 4096 or child.count("/") > 32:
                            raise Refused()
                        st = entry.stat(follow_symlinks=False)
                        if st.st_dev != device or stat.S_ISLNK(st.st_mode):
                            excluded.append(child)
                        elif stat.S_ISDIR(st.st_mode):
                            pending.append(child)
                        elif stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
                            if len(files) >= MAX_FILES:
                                raise Refused()
                            item = read_file(fd, entry.name, device)
                            if "text" in item:
                                total += len(item["text"].encode("utf-8"))
                                if total > TOTAL_BYTES:
                                    item.pop("text")
                                    item["omitted"] = "total_size_limit"
                            files[child] = item
                        else:
                            excluded.append(child)
            finally:
                os.close(fd)
    fs.root("project")
    if patterns != (*fs.policy("project"), *REVIEW_EXCLUDES):
        raise Changed()
    return {"files": files, "patterns": patterns, "excluded": excluded}


def read_file(parent, name, device):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_dev != device:
            raise Refused()
        if before.st_size > FILE_BYTES:
            return {"signature": stamp(before), "omitted": "size_limit"}
        chunks = []
        remaining = FILE_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > FILE_BYTES or stamp(before) != stamp(os.fstat(fd)):
            raise Changed()
        item = {"signature": hashlib.sha256(data).hexdigest(), "mode": before.st_mode & 0o777}
        try:
            if b"\x00" in data:
                raise UnicodeError()
            item["text"] = data.decode("utf-8")
        except UnicodeError:
            item["omitted"] = "binary"
        return item
    finally:
        os.close(fd)


def excluded_path(path, excluded):
    return any(path == entry or path.startswith(entry + "/") for entry in excluded)


def compare(before, after):
    files = []
    patterns = (*before["patterns"], *after["patterns"])
    excluded = before["excluded"] + after["excluded"]
    for path in sorted(before["files"].keys() | after["files"].keys()):
        if blocked(path, patterns) or excluded_path(path, excluded):
            continue
        old, new = before["files"].get(path), after["files"].get(path)
        if old == new:
            continue
        # Text retention budgets may differ between snapshots; same bytes/mode
        # are still unchanged, even when one side omitted their text.
        if (
            old
            and new
            and (old["signature"], old.get("mode")) == (new["signature"], new.get("mode"))
        ):
            continue
        item = dict(
            path=path,
            kind="created" if old is None else "deleted" if new is None else "modified",
            additions=0,
            deletions=0,
        )
        omitted = next((side["omitted"] for side in (old, new) if side and "omitted" in side), None)
        if omitted:
            item["omitted"] = omitted
        else:
            a = old["text"].splitlines(keepends=True) if old else []
            b = new["text"].splitlines(keepends=True) if new else []
            patch = list(difflib.unified_diff(a, b, fromfile="a/" + path, tofile="b/" + path))
            item["additions"] = sum(line.startswith("+") for line in patch[2:])
            item["deletions"] = sum(line.startswith("-") for line in patch[2:])
            # Explicit markers keep final-newline-only edits visible.
            item["diff"] = "".join(
                line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                for line in patch
            )
            if old and new and old.get("mode") != new.get("mode"):
                item["mode_change"] = f"{old['mode']:03o} → {new['mode']:03o}"
        files.append(item)
    return files


def artifact(task, files):
    return dict(
        state="available",
        handle=task,
        scope="changes_during_task",
        files=files,
        files_changed=len(files),
        additions=sum(f["additions"] for f in files),
        deletions=sum(f["deletions"] for f in files),
        omitted_files=sum("omitted" in f for f in files),
    )


class Changes:
    def __init__(self, db, fs, clock):
        self.db, self.fs, self.clock = db, fs, clock
        db.execute("""CREATE TABLE IF NOT EXISTS task_changes(
            task TEXT PRIMARY KEY, baseline TEXT, artifact TEXT, expires REAL)""")
        self.sweep()

    def sweep(self):
        self.db.execute(
            """UPDATE task_changes SET baseline=NULL,artifact=NULL
            WHERE expires<=? OR task IN (
            SELECT t.id FROM tasks t JOIN modes m ON m.id=t.mode
            WHERE m.active=0 OR t.state IN ('cancelled','interrupted','failed'))""",
            (self.clock(),),
        )

        self.db.execute(
            """UPDATE task_changes SET artifact=NULL WHERE task IN (
            SELECT task FROM task_changes WHERE artifact IS NOT NULL
            ORDER BY rowid DESC LIMIT -1 OFFSET ?)""",
            (MAX_ARTIFACTS,),
        )

    def start(self, task):
        if self.db.execute("SELECT 1 FROM task_changes WHERE task=?", (task,)).fetchone():
            return
        baseline = self.capture()
        self.db.execute(
            "INSERT INTO task_changes VALUES (?,?,NULL,?)",
            (task, json.dumps(baseline) if baseline else None, self.clock() + RETENTION),
        )

    def capture(self):
        try:
            with self.fs.execution_guard():
                return snapshot(self.fs)
        except (OSError, ValueError, UnicodeError, Refused, Changed):
            return None

    def finish(self, task):
        row = self.db.execute("SELECT baseline FROM task_changes WHERE task=?", (task,)).fetchone()
        if not row or not row[0]:
            return
        after = self.capture()
        review = artifact(task, compare(json.loads(row[0]), after)) if after else None
        # Transport bounds also apply after JSON escaping and patch expansion.
        encoded = json.dumps(review) if review else None
        if encoded and len(encoded.encode()) > 3 * 1024 * 1024:
            encoded = None
        self.db.execute(
            "UPDATE task_changes SET baseline=NULL,artifact=?,expires=? WHERE task=?",
            (encoded, self.clock() + RETENTION, task),
        )

    def get(self, task):
        row = self.db.execute("SELECT artifact FROM task_changes WHERE task=?", (task,)).fetchone()
        if not row or not row[0]:
            return {"state": "unavailable"}
        try:
            patterns = self.fs.policy("project")
        except (OSError, ValueError, Refused):
            return {"state": "unavailable"}
        review = json.loads(row[0])
        return artifact(task, [f for f in review["files"] if not blocked(f["path"], patterns)])

    def summary(self, task):
        return {key: value for key, value in self.get(task).items() if key != "files"}
