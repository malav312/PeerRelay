"""Pinned HTTPS transport. It never imports or invokes a model/provider SDK."""

import json
import os
import ssl
import stat
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx


class Unavailable(Exception):
    pass


def private_file(path):
    path = Path(path).absolute()
    st = path.lstat()
    if (
        path.resolve() != path
        or not stat.S_ISREG(st.st_mode)
        or st.st_uid != os.getuid()
        or st.st_mode & 0o077
    ):
        raise ValueError("Configuration and credentials must be owned, private regular files")
    return path


def load(path):
    path = private_file(path)
    config = json.loads(path.read_text())
    for key in ("ca_file", "token_file", "key_file", "state_dir"):
        if key in config:
            item = Path(config[key])
            config[key] = str(item if item.is_absolute() else path.parent / item)
    config["_path"] = str(path)
    return config


class Client:
    def __init__(self, config):
        parsed = urlsplit(config["url"])
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("A fixed HTTPS origin is required")
        self.role = config["role"]
        if self.role not in ("requester", "responder"):
            raise ValueError("Invalid role")
        self.token_file = private_file(config["token_file"])
        self.http = httpx.Client(
            base_url=config["url"],
            verify=ssl.create_default_context(cafile=config["ca_file"]),
            trust_env=False,
            follow_redirects=False,
            timeout=10,
        )
        self.stop = threading.Event()
        self.lease = None
        self.thread = None

    def call(self, method, arguments):
        token = private_file(self.token_file).read_text().strip()
        try:
            with self.http.stream(
                "POST",
                "/rpc",
                headers={"Authorization": "Bearer " + token},
                json={"method": method, "arguments": arguments},
            ) as response:
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 4 * 1024 * 1024:
                        raise Unavailable("Response exceeded limit")
                if response.status_code != 200:
                    raise Unavailable(
                        "Relay refused request or responder unavailable; no local fallback"
                    )
                result = json.loads(raw)
        except (httpx.HTTPError, ValueError) as error:
            raise Unavailable("Relay unavailable; no local fallback") from error
        if method == "response_mode":
            self.lease = result["lease_id"]
            if self.thread is None:
                self.thread = threading.Thread(target=self._heartbeat, daemon=True)
                self.thread.start()
        elif method == "disconnect":
            self.lease = None
        return result

    def _heartbeat(self):
        while not self.stop.wait(15):
            lease = self.lease
            if lease:
                try:
                    self.call("heartbeat", {"lease_id": lease})
                except (Unavailable, OSError):
                    self.lease = None

    def wait_for_task(self, lease_id, seconds=25):
        if not 0 <= seconds <= 50:
            raise ValueError("Wait must be between 0 and 50 seconds")
        deadline = time.monotonic() + seconds
        while True:
            result = self.call("claim", {"lease_id": lease_id})
            if result["state"] != "idle" or time.monotonic() >= deadline:
                return result
            if self.stop.wait(min(0.5, max(0, deadline - time.monotonic()))):
                raise Unavailable("Responder stopped")

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=11)
        if self.lease:
            try:
                self.call("disconnect", {"lease_id": self.lease})
            except (Unavailable, OSError):
                pass
        self.http.close()
