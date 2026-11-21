import asyncio
import hmac
import json
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event, Thread

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .audit import AuthAudit
from .client import private_file
from .filesystem import Changed, Refused
from .service import Denied, Relay


class Boundary:
    def __init__(self, app, tokens):
        self.app = app
        self.tokens = tokens

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["scheme"] != "https":
            return await JSONResponse({"error": "https_required"}, 426)(scope, receive, send)
        headers = [v for k, v in scope.get("headers", []) if k.lower() == b"authorization"]
        supplied = headers[0] if len(headers) == 1 else b""
        role = next(
            (
                r
                for r, t in self.tokens.items()
                if hmac.compare_digest(supplied, ("Bearer " + t).encode())
            ),
            None,
        )
        if role is None:
            audit = getattr(getattr(scope.get("app"), "state", None), "audit", None)
            peer = (scope.get("client") or ("unknown", 0))[0]
            limited = audit.failed(peer) if audit else False
            return await JSONResponse({"error": "unauthorized"}, 429 if limited else 401)(
                scope, receive, send
            )
        scope["peerrelay_role"] = role
        body = bytearray()
        try:
            async with asyncio.timeout(5):
                while True:
                    item = await receive()
                    if item["type"] == "http.disconnect":
                        return
                    body.extend(item.get("body", b""))
                    if len(body) > 4 * 1024 * 1024:
                        return await JSONResponse({"error": "too_large"}, 413)(scope, receive, send)
                    if not item.get("more_body"):
                        break
        except TimeoutError:
            return await JSONResponse({"error": "body_timeout"}, 408)(scope, receive, send)
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
    root = Path(config["root"]).resolve(strict=True)
    protected = list(config["tokens"].values())
    protected.extend(config[key] for key in ("_path", "key_file", "ca_file") if key in config)
    for path in protected:
        path = Path(path).resolve(strict=True)
        if path == root or root in path.parents:
            raise ValueError("Private configuration/credentials overlap the project")
    tokens = {
        role: private_file(path).read_text().strip() for role, path in config["tokens"].items()
    }
    if set(tokens) != {"requester", "responder"} or len(set(tokens.values())) != 2:
        raise ValueError("Separate paired credentials required")

    execution_config = None
    if config.get("grant") == "exec":
        execution_config = config.get("execution_backend")
        if not isinstance(execution_config, dict) or execution_config.get("kind") not in (
            "docker",
            "host",
        ):
            raise ValueError(
                "Exec requires explicit backend configuration; legacy host access is disabled"
            )
        if (
            execution_config["kind"] == "host"
            and execution_config.get("allow_host_credentials") is not True
        ):
            raise ValueError(
                "Host exec requires acknowledgement of relay/provider credential access"
            )

    @asynccontextmanager
    async def lifespan(app):
        relay = Relay(
            config["root"],
            config["state_dir"],
            grant=config.get("grant", "rw"),
            execution_backend=execution_config,
        )
        app.state.relay = relay
        app.state.audit = AuthAudit(relay.state)
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
            app.state.audit.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(Boundary, tokens=tokens)

    @app.get("/healthz")
    def health():
        return {"status": "ready"}

    @app.post("/rpc")
    async def rpc(request: Request):
        role = request.scope["peerrelay_role"]
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {"method", "arguments"}:
                raise Denied("Invalid envelope")
            return app.state.relay.call(role, body["method"], body["arguments"])
        except (Denied, Refused, Changed, OSError, ValueError, TypeError, json.JSONDecodeError):
            return JSONResponse({"error": "request_refused"}, 403)

    return app
