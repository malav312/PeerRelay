import hmac
import json
from contextlib import asynccontextmanager
from threading import Event, Thread

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .client import private_file
from .filesystem import Changed, Refused
from .service import Denied, Relay


class Boundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["scheme"] != "https":
            return await JSONResponse({"error": "https_required"}, 426)(scope, receive, send)
        body = bytearray()
        while True:
            item = await receive()
            if item["type"] == "http.disconnect":
                return
            body.extend(item.get("body", b""))
            if len(body) > 4 * 1024 * 1024:
                return await JSONResponse({"error": "too_large"}, 413)(scope, receive, send)
            if not item.get("more_body"):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        try:
            await self.app(scope, bounded_receive, send)
        except Exception:
            await JSONResponse({"error": "operation_failed"}, 500)(scope, receive, send)


def create_app(config):
    tokens = {
        role: private_file(path).read_text().strip() for role, path in config["tokens"].items()
    }
    if set(tokens) != {"requester", "responder"} or len(set(tokens.values())) != 2:
        raise ValueError("Separate paired credentials required")

    @asynccontextmanager
    async def lifespan(app):
        relay = Relay(config["root"], config["state_dir"])
        app.state.relay = relay
        stop = Event()

        def maintain():
            while not stop.wait(1):
                with relay.lock:
                    relay.sweep()
                    relay.db.commit()

        thread = Thread(target=maintain, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join()
            relay.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(Boundary)

    @app.get("/healthz")
    def health():
        return {"status": "ready", "service": "PeerRelay"}

    @app.post("/rpc")
    async def rpc(request: Request):
        supplied = request.headers.get("authorization", "")
        role = next(
            (r for r, t in tokens.items() if hmac.compare_digest(supplied, "Bearer " + t)), None
        )
        if role is None:
            return JSONResponse({"error": "unauthorized"}, 401)
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {"method", "arguments"}:
                raise Denied("Invalid envelope")
            return app.state.relay.call(role, body["method"], body["arguments"])
        except (Denied, Refused, Changed, OSError, ValueError, TypeError, json.JSONDecodeError):
            return JSONResponse({"error": "request_refused"}, 403)

    return app
