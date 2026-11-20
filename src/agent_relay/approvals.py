"""Local human decisions. Deliberately not exposed as a native worker tool."""

import difflib
import errno
import hashlib
import json
import os
import secrets
import sqlite3
import stat

from agent_relay.filesystem import Changed, Refused, identity, version
from agent_relay.schemas import CONTENT_LIMIT, Tool, canonical, payload_hash


def escaped(value):
    # Protect the terminal from control sequences and make literal backslashes visible.
    return json.dumps(value, ensure_ascii=True)


class Approvals:
    def __init__(self, filesystem):
        self.fs = filesystem
        self.journal = filesystem.journal

    def _load(self, ident):
        with self.journal.connect() as db:
            row = db.execute(
                "SELECT binding,digest,base_content,state,detail FROM local_proposals WHERE id=?",
                (ident,),
            ).fetchone()
            op = db.execute("SELECT hash,result FROM operations WHERE id=?", (ident,)).fetchone()
        if not row:
            raise ValueError(
                "No captured proposal; legacy proposals require explicit import-target"
            )
        binding, digest, base, state, detail = row
        binding = json.loads(binding)
        detail = json.loads(detail)
        if detail.get("purged"):
            if (
                binding
                or base is not None
                or state not in ("rejected", "stale", "applied", "uncertain")
            ):
                raise ValueError("Invalid proposal tombstone")
            return dict(
                id=ident, binding=None, digest=digest, base=None, status=state, detail=detail
            )
        tool = Tool.model_validate(binding["payload"])
        if (
            tool.kind != "propose_replace"
            or binding["id"] != ident
            or digest != payload_hash(binding)
            or not op
            or binding.get("base_sha256") != payload_hash(base)
            or op[0] != payload_hash([binding["payload"], binding["grants"]])
            or not op[1]
            or json.loads(op[1]).get("status") != "proposed"
        ):
            raise ValueError("Proposal integrity check failed")
        return dict(
            id=ident, binding=binding, digest=digest, base=base, status=state, detail=detail
        )

    def _transition(self, row, state, detail):
        with self.journal.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE local_proposals SET state=?,detail=? WHERE id=?",
                (state, canonical(detail), row["id"]),
            )
            db.execute(
                "INSERT INTO local_proposal_events (proposal_id,state,detail) VALUES (?,?,?)",
                (row["id"], state, canonical(detail)),
            )
        row.update(status=state, detail=detail)

    def _recover(self, row):
        # Caller holds the same process-wide lock as the applier. No live applier
        # can be mistaken for a crash while this lock is held.
        if row["status"] in ("approved", "applying"):
            self._transition(
                row,
                "uncertain",
                {
                    **row["detail"],
                    "reason": "Interrupted application; no automatic replay",
                    "interrupted_state": row["status"],
                },
            )

    def _result(self, row):
        return {key: row[key] for key in ("id", "digest", "status", "detail")}

    def _validate_root(self, row):
        b = row["binding"]
        tool = Tool.model_validate(b["payload"])
        self.fs.root(tool.root)
        current = self.fs.proposal_binding(row["id"], b["payload"], b["grants"])
        expected = {k: v for k, v in b.items() if k != "base_sha256"}
        if (
            current != expected
            or self.fs.roots[tool.root][3] != "rw"
            or b["grants"].get(tool.root) != "rw"
        ):
            raise Refused()
        return tool

    def inspect(self, ident):
        with self.fs.execution_guard():
            row = self._load(ident)
            self._recover(row)
            if row["binding"] is None:
                return self._result(row)
            tool = Tool.model_validate(row["binding"]["payload"])
            try:
                self._validate_root(row)
                current = self.fs.read(Tool(kind="read", root=tool.root, path=tool.path))
                matches = current["version"] == tool.expected_version
            except (OSError, Refused, Changed, UnicodeError, KeyError):
                current = None
                matches = False
            with self.journal.connect() as db:
                events = [
                    dict(sequence=n, state=s, detail=json.loads(d), created_at=t)
                    for n, s, d, t in db.execute(
                        "SELECT sequence,state,detail,created_at FROM local_proposal_events "
                        "WHERE proposal_id=? ORDER BY sequence",
                        (ident,),
                    )
                ]
            base = row["base"]
            if base is None:
                diff = "Expected baseline unavailable; no valid before/after diff.\n"
            else:
                lines = difflib.unified_diff(
                    base.splitlines(keepends=True),
                    tool.content.splitlines(keepends=True),
                    fromfile=tool.path + " (expected)",
                    tofile=tool.path + " (proposed)",
                )
                diff = "".join(
                    line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                    for line in lines
                )
            return {
                **self._result(row),
                "root": tool.root,
                "path": tool.path,
                "registered_root": row["binding"]["root_identity"][0],
                "root_identity": row["binding"]["root_identity"],
                "grants": row["binding"]["grants"],
                "base_sha256": row["binding"]["base_sha256"],
                "expected_version": tool.expected_version,
                "current_matches": matches,
                "diff": diff,
                "base_content": base,
                "proposed_content": tool.content,
                "proposed_escaped": escaped(tool.content),
                "events": events,
                "current_version": current["version"] if current else None,
            }

    def import_target(self, ident, root, path, grants):
        """Adopt a legacy receipt only if reconstructed input matches its saved hash."""
        with self.fs.execution_guard():
            result = self.journal.lookup(ident)
            if not result or result.get("status") != "proposed" or "content" not in result:
                raise ValueError("Complete legacy proposal receipt unavailable")
            tool = Tool(
                kind="propose_replace",
                root=root,
                path=path,
                content=result["content"],
                expected_version=result["base_version"],
            )
            payload = tool.model_dump()
            binding = self.fs.proposal_binding(ident, payload, grants)
            self._validate_root({"id": ident, "binding": binding})
            current = self.fs.read(tool)
            base = current["content"] if current["version"] == tool.expected_version else None
            self.journal.proposal(ident, binding, base, result)
        return self.inspect(ident)

    def decide(self, ident, digest, decision):
        if decision not in ("approve", "reject"):
            raise ValueError("Decision must be approve or reject")
        with self.fs.execution_guard():
            row = self._load(ident)
            if not secrets.compare_digest(row["digest"], digest):
                raise ValueError("Approval digest does not match the inspected proposal")
            self._recover(row)
            if row["status"] != "pending":
                return self._result(row)
            if decision == "reject":
                self._transition(row, "rejected", {"reason": "Explicit local rejection"})
                return self._result(row)
            self._transition(
                row, "approved", {"reason": "Explicit local approval", "digest": digest}
            )
            try:
                tool = self._validate_root(row)
                self._apply(row, tool)
            except (Changed, FileNotFoundError):
                self._transition(
                    row, "stale", {"reason": "Target no longer matches expected version"}
                )
            except (Refused, UnicodeError, KeyError):
                self._transition(
                    row, "rejected", {"reason": "Target violates filesystem protections"}
                )
            except OSError as error:
                state = (
                    "rejected"
                    if row["status"] == "approved" and error.errno in (errno.ELOOP, errno.ENOTDIR)
                    else "uncertain"
                )
                self._transition(
                    row,
                    state,
                    {
                        **row["detail"],
                        "reason": "Filesystem failure; no automatic replay",
                        "errno": error.errno,
                    },
                )
            except sqlite3.Error:
                # The prior intent remains durable. A later status call can recover
                # it when SQLite is available; never claim the replacement failed.
                row.update(
                    status="uncertain",
                    detail={
                        **row["detail"],
                        "reason": "Journal write failed; inspect before further action",
                    },
                )
            return self._result(row)

    def _read_fd(self, fd, device):
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_dev != device
            or before.st_uid != os.getuid()
            or before.st_size > CONTENT_LIMIT
        ):
            raise Refused()
        os.lseek(fd, 0, os.SEEK_SET)
        data = bytearray()
        while len(data) <= CONTENT_LIMIT:
            chunk = os.read(fd, min(65536, CONTENT_LIMIT + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(fd)
        if len(data) > CONTENT_LIMIT or version(before, data) != version(after, data):
            raise Changed()
        return after, bytes(data)

    def _apply(self, row, tool):
        with self.fs.parent(tool.root, tool.path) as (parent, name, device):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            temp = None
            temporary_fd = None
            try:
                original, data = self._read_fd(fd, device)
                if version(original, data) != tool.expected_version:
                    raise Changed()
                temp = ".peerrelay-apply-" + secrets.token_hex(16)
                detail = {
                    "temporary_name": temp,
                    "permission_bits": stat.S_IMODE(original.st_mode),
                    "expected_version": tool.expected_version,
                    "proposed_sha256": hashlib.sha256(tool.content.encode()).hexdigest(),
                }
                self._transition(row, "applying", detail)
                temporary_fd = os.open(
                    temp, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=parent
                )
                with os.fdopen(os.dup(temporary_fd), "wb") as stream:
                    stream.write(tool.content.encode("utf-8"))
                    stream.flush()
                    os.fchmod(stream.fileno(), stat.S_IMODE(original.st_mode))
                    os.fsync(stream.fileno())
                # Re-open the parent through the registered root, then compare the
                # open file AND directory entry immediately before replacement.
                with self.fs.parent(tool.root, tool.path) as (now_parent, _, _):
                    if identity(os.fstat(now_parent)) != identity(os.fstat(parent)):
                        raise Changed()
                after, data = self._read_fd(fd, device)
                entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (
                    version(after, data) != tool.expected_version
                    or identity(entry) != identity(after)
                    or not stat.S_ISREG(entry.st_mode)
                    or entry.st_nlink != 1
                ):
                    raise Changed()
                staged, staged_data = self._read_fd(temporary_fd, device)
                staged_entry = os.stat(temp, dir_fd=parent, follow_symlinks=False)
                if (
                    staged_data != tool.content.encode("utf-8")
                    or stat.S_IMODE(staged.st_mode) != stat.S_IMODE(original.st_mode)
                    or identity(staged_entry) != identity(staged)
                    or not stat.S_ISREG(staged_entry.st_mode)
                    or staged_entry.st_nlink != 1
                ):
                    raise Refused()
                os.replace(temp, name, src_dir_fd=parent, dst_dir_fd=parent)
                temp = None
                os.fsync(parent)
                self._transition(row, "applied", detail)
            finally:
                os.close(fd)
                if temp is not None and temporary_fd is not None:
                    try:
                        entry = os.stat(temp, dir_fd=parent, follow_symlinks=False)
                        if identity(entry) == identity(os.fstat(temporary_fd)):
                            os.unlink(temp, dir_fd=parent)
                    except FileNotFoundError:
                        pass
                    finally:
                        os.close(temporary_fd)
                elif temporary_fd is not None:
                    os.close(temporary_fd)
