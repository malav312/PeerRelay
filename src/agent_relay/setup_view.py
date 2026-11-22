"""Capability-protected loopback setup view; no external assets or request logs."""

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = Path(__file__).with_name("setup_view.html").read_text()


def start_view(setups):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def authorize(self, mutation=False):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != origin.removeprefix("http://"):
                self.send_error(403)
                return None
            supplied = self.headers.get("Origin")
            if (mutation and supplied != origin) or (supplied is not None and supplied != origin):
                self.send_error(403)
                return None
            token = self.path.split("/")[1]
            with setups.lock:
                if setups.closed:
                    self.send_error(410, "Setup is closed")
                    return None
                session = next(
                    (
                        s
                        for s in setups.sessions.values()
                        if not s.closed and secrets.compare_digest(s.view_token, token)
                    ),
                    None,
                )
            if session is None or self.path not in (f"/{token}", f"/{token}/state"):
                self.send_error(404)
                return None
            return session

        def reply(self, value, status=200, html=False, nonce=None):
            body = value.encode() if html else json.dumps(value).encode()
            self.send_response(status)
            self.send_header(
                "Content-Type", "text/html; charset=utf-8" if html else "application/json"
            )
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; connect-src 'self'; frame-ancestors 'none'; "
                f"script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                "form-action 'self'; base-uri 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            session = self.authorize()
            if session is None:
                return
            if self.path.endswith("/state"):
                with session.lock:
                    if session.closed or setups.closed:
                        self.send_error(410, "Setup is closed")
                        return
                    pair_dir = session.directory / "pair"
                    config = pair_dir / (
                        "requester/client.json"
                        if session.record["role"] == "requester"
                        else "client.json"
                    )
                    server = pair_dir / "server.json"
                    from .client import load

                    saved = config.exists() and (
                        session.record["role"] == "responder"
                        or bool(load(server).get("token_sha256"))
                    )
                    self.reply(
                        {
                            **session.receipt(),
                            "view": session.view,
                            "project_default": str(setups.project) if setups.project else "",
                            "pinned": "settings" in session.record,
                            "saved": bool(saved),
                            "bundle": str(config) if saved else None,
                            "can_start": (
                                session.record["role"] == "requester"
                                and server.exists()
                                and session.record.get("config") == str(config)
                            ),
                        }
                    )
            else:
                nonce = secrets.token_urlsafe(24)
                self.reply(PAGE.replace("NONCE", nonce), html=True, nonce=nonce)

        def do_POST(self):
            session = self.authorize(mutation=True)
            if session is None:
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if (
                    not 0 < length <= 16384
                    or self.headers.get("Content-Type") != "application/json"
                ):
                    raise ValueError("Expected a small JSON form")
                body = json.loads(self.rfile.read(length))
                settings = body.get("settings", {})
                action = body["action"]
                if action == "pair":
                    session.start_pairing(settings)
                elif action == "reuse":
                    session.reuse(settings["config"])
                elif action == "confirm":
                    session.confirm(settings["accepted"])
                elif action == "cancel":
                    session.cancel()
                elif action == "start_relay":
                    session.start_relay()
                elif action == "check_relay":
                    session.check_relay()
                else:
                    raise ValueError("Unknown setup action")
                self.reply({"ok": True})
            except (ValueError, OSError, KeyError, TypeError) as error:
                self.reply({"error": str(error)}, status=400)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
