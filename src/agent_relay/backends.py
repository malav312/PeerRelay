"""Execution boundaries. Host mode is unrestricted; Docker never falls back to it."""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def base_environment():
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "TERM": "xterm-256color",
        "GIT_TERMINAL_PROMPT": "0",
    }


class HostBackend:
    name = "host"

    def __init__(self, state):
        self.home = tempfile.TemporaryDirectory(prefix="peerrelay-command-home-")
        Path(self.home.name, "tmp").mkdir(mode=0o700)

    def launch(self, ident, spec):
        env = base_environment()
        env.update(HOME=self.home.name, TMPDIR=str(Path(self.home.name, "tmp")))
        return ["/bin/sh", "-c", spec["command"]], spec["cwd"], env

    def cleanup(self, ident, confirmed=True):
        return True

    def close(self):
        self.home.cleanup()


class DockerBackend:
    name = "docker"

    def __init__(self, root, config):
        self.binary = shutil.which("docker")
        if not self.binary:
            raise ValueError("Docker is required for isolated execution; no host fallback")
        self.root = Path(root).resolve(strict=True)
        if "," in str(self.root) or "\n" in str(self.root):
            raise ValueError("Project path cannot be represented as a Docker bind mount")
        self.image = config.get("image", "python:3.13-slim")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]{0,255}", self.image):
            raise ValueError("Invalid operator-selected container image")
        self.prefix = [self.binary, "--host", "unix:///var/run/docker.sock"]

    def creation(self, ident, spec):
        relative = Path(spec["cwd"]).resolve(strict=True).relative_to(self.root)
        uid, gid = os.getuid(), os.getgid()
        if uid == 0:
            uid = gid = 65534
        argv = [
            *self.prefix,
            "create",
            "--pull",
            "never",
            "--name",
            ident,
            "--label",
            "peerrelay.command=" + ident,
            "--init",
            "--interactive",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
            "--memory",
            "1g",
            "--cpus",
            "2",
            "--ulimit",
            "nofile=1024:1024",
            "--user",
            f"{uid}:{gid}",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            "--tmpfs",
            "/home/relay:rw,nosuid,nodev,size=64m,mode=1777",
            "--env",
            "HOME=/home/relay/private",
            "--env",
            "GIT_TERMINAL_PROMPT=0",
            "--mount",
            f"type=bind,src={self.root},dst=/workspace",
            "--workdir",
            str(Path("/workspace") / relative),
        ]
        if spec["terminal"]:
            argv.append("--tty")
        # Podman's Docker API rejects uid/gid tmpfs options. Create a private HOME
        # as the command UID in a fresh sticky mount before any peer code runs.
        argv.extend(
            [
                self.image,
                "/bin/sh",
                "-c",
                'mkdir -m 700 "$HOME" && exec "$@"',
                "peerrelay-init",
                "/bin/sh",
                "-c",
                spec["command"],
            ]
        )
        return argv, str(self.root), base_environment()

    def prepare(self, ident, spec):
        argv, cwd, env = self.creation(ident, spec)
        result = subprocess.run(
            argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=10
        )
        if result.returncode != 0:
            raise ValueError("Container creation not confirmed; workload was not started")

    def launch(self, ident, spec):
        return (
            [*self.prefix, "start", "--attach", "--interactive", ident],
            str(self.root),
            base_environment(),
        )

    def cleanup(self, ident, confirmed=True):
        # Removing the named container kills its entire PID namespace, including setsid children.
        try:
            result = subprocess.run(
                [*self.prefix, "rm", "--force", ident],
                capture_output=True,
                timeout=3,
                env=base_environment(),
            )
            if result.returncode == 0:
                return True
            # Only an explicit missing-container result is safe to treat as already removed.
            return confirmed and (
                b"No such container:" in result.stderr or b"No such object:" in result.stderr
            )
        except (OSError, subprocess.TimeoutExpired):
            return False

    def close(self):
        pass


def backend(root, state, config=None):
    config = config or {"kind": "host"}  # Internal callers; production validates configuration.
    if config["kind"] == "host":
        return HostBackend(state)
    if config["kind"] == "docker":
        return DockerBackend(root, config)
    raise ValueError("Unknown execution backend")
