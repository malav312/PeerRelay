import fcntl
import hashlib
import os
import re
import secrets
import stat
import threading
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from agent_relay.journal import Journal
from agent_relay.policy import blocked, ignore_patterns
from agent_relay.schemas import CONTENT_LIMIT, Tool, payload_hash


class Refused(Exception):
    pass


class Changed(Exception):
    pass


def identity(st):
    return [st.st_dev, st.st_ino]


def reserved(name):
    # Reserve both current and legacy private operation files, regardless of branding.
    return name.casefold().startswith(".relay-write-") or bool(
        re.match(r"\.[a-z][a-z0-9-]*-(?:create|apply)-", name, re.IGNORECASE)
    )


def version(st, data):
    return payload_hash(
        [
            *identity(st),
            st.st_size,
            st.st_mtime_ns,
            st.st_ctime_ns,
            hashlib.sha256(data).hexdigest(),
        ]
    )


class Filesystem:
    def __init__(self, roots, state_dir, protected_paths=(), allow_unavailable_roots=False):
        self.roots = {}
        self.lock = threading.Lock()
        paths = [Path(r["path"]).absolute() for r in roots.values()]
        state = Path(state_dir).absolute()
        for secret in protected_paths:
            secret = Path(secret).resolve()
            if any(p == secret or p in secret.parents for p in paths):
                raise ValueError("Private configuration/credentials overlap a root")
        for p in paths:
            if (
                (p.resolve() != p and not allow_unavailable_roots)
                or p == state
                or p in state.parents
                or state in p.parents
            ):
                raise ValueError("Roots and private state must be separate and not symlinks")
            if any(p != q and (p in q.parents or q in p.parents) for q in paths):
                raise ValueError("Overlapping roots")
        if len(set(paths)) != len(paths):
            raise ValueError("Duplicate roots")
        self.journal = Journal(state)
        try:
            for alias, config in roots.items():
                if config["mode"] not in ("ro", "rw"):
                    raise ValueError("Invalid root mode")
                p = Path(config["path"]).absolute()
                fd = None
                try:
                    if p.resolve() != p:
                        raise ValueError("Registered roots must not use symlinks")
                    fd = os.open(p, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    st = os.fstat(fd)
                    self.journal.root(alias, [str(p), *identity(st)])
                    self.roots[alias] = (p, fd, identity(st), config["mode"])
                except (OSError, ValueError):
                    if fd is not None:
                        os.close(fd)
                    if not allow_unavailable_roots:
                        raise
        except BaseException:
            self.close()
            raise

    def close(self):
        for _, fd, _, _ in self.roots.values():
            os.close(fd)
        self.roots.clear()

    @contextmanager
    def execution_guard(self):
        """One stable lock shared by tool execution and local decisions/processes."""
        with self.lock:
            fd = os.open(
                self.journal.directory / "execution.lock",
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                0o600,
            )
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != os.getuid():
                    raise Refused()
                os.fchmod(fd, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)

    def proposal_binding(self, operation_id, payload, grants):
        path, _, ident, _ = self.roots[payload["root"]]
        return {
            "id": operation_id,
            "root_identity": [str(path), *ident],
            "payload": payload,
            "grants": grants,
        }

    def root(self, alias):
        if alias not in self.roots:
            raise Refused()
        path, fd, expected, mode = self.roots[alias]
        st = os.stat(path, follow_symlinks=False)
        if not stat.S_ISDIR(st.st_mode) or identity(st) != expected:
            raise Refused()
        return fd, expected[0], mode

    def policy(self, alias):
        root, _, _ = self.root(alias)
        try:
            return ignore_patterns(root)
        except (OSError, ValueError, UnicodeError):
            raise Refused() from None

    def check_path(self, alias, path):
        if blocked(path, self.policy(alias)):
            raise Refused()

    @contextmanager
    def parent(self, alias, path):
        root, device, _ = self.root(alias)
        self.check_path(alias, path)
        if path.startswith("/") or "\x00" in path:
            raise Refused()
        parts = path.split("/")
        if any(p in ("", "..") or reserved(p) for p in parts):
            raise Refused()
        if "." in parts and parts != ["."]:
            raise Refused()
        fd = os.dup(root)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
                if os.fstat(fd).st_dev != device:
                    raise Refused()
            yield fd, parts[-1], device
        finally:
            os.close(fd)

    def read(self, tool):
        with self.parent(tool.root, tool.path) as (parent, name, device):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                before = os.fstat(fd)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_nlink != 1
                    or before.st_dev != device
                ):
                    raise Refused()
                if before.st_size > CONTENT_LIMIT:
                    raise Refused()
                chunks = []
                remaining = CONTENT_LIMIT + 1
                while remaining:
                    chunk = os.read(fd, min(remaining, 65536))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                data = b"".join(chunks)
                after = os.fstat(fd)
                if len(data) > CONTENT_LIMIT or version(before, data) != version(after, data):
                    raise Changed()
                if b"\x00" in data:
                    raise Refused()
                return {
                    "status": "succeeded",
                    "content": data.decode("utf-8"),
                    "version": version(after, data),
                }
            finally:
                os.close(fd)

    def listing(self, tool):
        with self.parent(tool.root, tool.path) as (parent, name, device):
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                if os.fstat(fd).st_dev != device:
                    raise Refused()
                entries = []
                patterns = self.policy(tool.root)
                scanned = 0
                with os.scandir(fd) as iterator:
                    for entry in iterator:
                        scanned += 1
                        if scanned > 10000:
                            break
                        st = entry.stat(follow_symlinks=False)
                        child = entry.name if tool.path == "." else tool.path + "/" + entry.name
                        if blocked(child, patterns) or reserved(entry.name) or st.st_dev != device:
                            continue
                        if stat.S_ISDIR(st.st_mode) or (
                            stat.S_ISREG(st.st_mode) and st.st_nlink == 1
                        ):
                            entries.append(
                                {"name": entry.name, "directory": stat.S_ISDIR(st.st_mode)}
                            )
                entries.sort(key=lambda e: e["name"])
                end = tool.cursor + 200
                return {
                    "status": "succeeded",
                    "entries": entries[tool.cursor : end],
                    "cursor": end if end < len(entries) else None,
                    "truncated": scanned > 10000 or end < len(entries),
                }
            finally:
                os.close(fd)

    def search(self, tool):
        pending, matches, count, dirs, truncated = [tool.path], [], 0, 0, False
        while pending and count < 100 and len(matches) < 100 and dirs < 100:
            path = pending.pop()
            dirs += 1
            page = self.listing(Tool(kind="list", root=tool.root, path=path))
            truncated |= page["truncated"]
            for entry in page["entries"]:
                child = entry["name"] if path == "." else path + "/" + entry["name"]
                if entry["directory"]:
                    if child.count("/") < 32:
                        pending.append(child)
                    else:
                        truncated = True
                    continue
                if count >= 100 or len(matches) >= 100:
                    truncated = True
                    break
                count += 1
                try:
                    read = self.read(Tool(kind="read", root=tool.root, path=child))
                except (OSError, Refused, Changed, UnicodeError):
                    continue
                for number, line in enumerate(read["content"].splitlines(), 1):
                    if tool.query in line:
                        matches.append(
                            {
                                "path": child,
                                "line": number,
                                "text": line[:2000],
                                "version": read["version"],
                            }
                        )
                        if len(matches) == 100:
                            truncated = True
                            break
        return {"status": "succeeded", "matches": matches, "truncated": truncated or bool(pending)}

    def create(self, tool):
        with self.parent(tool.root, tool.path) as (parent, name, _):
            temporary = ".peerrelay-create-" + secrets.token_hex(16)
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(tool.content.encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(
                        temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False
                    )
                except FileExistsError:
                    raise Changed() from None
                os.unlink(temporary, dir_fd=parent)
                os.fsync(parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
        return {"status": "succeeded", "created": True}

    def execute(self, operation_id, payload, grants):
        try:
            tool = Tool.model_validate(payload)
            _, _, mode = self.root(tool.root)
            self.check_path(tool.root, tool.path)
            right = grants.get(tool.root)
            if right not in ("ro", "rw"):
                raise Refused()
            if tool.kind in ("create_file", "propose_replace") and (mode != "rw" or right != "rw"):
                raise Refused()
        except (ValidationError, Refused, OSError):
            return {"status": "denied"}
        # Thread + OS lock protect the journal/effect boundary across processes too.
        with self.execution_guard():
            previous = self.journal.begin(operation_id, payload, grants)
            if previous is not None:
                return previous
            try:
                if tool.kind == "propose_replace":
                    current = self.read(tool)
                    if current["version"] != tool.expected_version:
                        raise Changed()
                    result = {
                        "status": "proposed",
                        "base_version": current["version"],
                        "content": tool.content,
                    }
                    self.journal.proposal(
                        operation_id,
                        self.proposal_binding(operation_id, payload, grants),
                        current["content"],
                        result,
                    )
                else:
                    handler = {
                        "read": self.read,
                        "list": self.listing,
                        "search": self.search,
                        "create_file": self.create,
                    }[tool.kind]
                    result = handler(tool)
            except Changed:
                result = {"status": "conflict"}
            except (Refused, UnicodeError, FileNotFoundError, NotADirectoryError, PermissionError):
                result = {"status": "denied"}
            except OSError:
                # IO failure after create may be post-effect; never claim safe retry.
                result = {"status": "unknown" if tool.kind == "create_file" else "denied"}
            self.journal.finish(operation_id, result)
            return result

    def purge(self, operation_ids):
        with self.execution_guard():
            self.journal.purge(operation_ids)
