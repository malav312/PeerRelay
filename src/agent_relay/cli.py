import argparse
import json
import os
import sys

from .client import Client, load, private_file
from .setup import initialize


def main():
    os.umask(0o077)
    if len(sys.argv) > 1 and sys.argv[1] == "proposal":
        from .approval_cli import main as proposal_main

        raise SystemExit(proposal_main(sys.argv[2:]))
    parser = argparse.ArgumentParser(description="PeerRelay: paired requester and response modes")
    sub = parser.add_subparsers(dest="command", required=True)
    bootstrap = sub.add_parser("bootstrap", help="Stable stdio MCP; no role bundle required")
    bootstrap.add_argument(
        "--state-dir", required=True, help="Private setup state outside projects"
    )
    bootstrap_setup = sub.add_parser(
        "bootstrap-setup", help="Package stable MCP and both entry skills"
    )
    bootstrap_setup.add_argument("--client", choices=("claude", "codex"), required=True)
    bootstrap_setup.add_argument("--state-dir", required=True)
    bootstrap_setup.add_argument("--directory", required=True)
    client_setup = sub.add_parser("client-setup", help="Prepare a fresh native role chat")
    client_setup.add_argument("--client", choices=("claude", "codex"), required=True)
    client_setup.add_argument("--config", required=True)
    client_setup.add_argument("--directory", required=True, help="New private chat directory")
    client_setup.add_argument("--background", choices=("off", "claude-channel"), default="off")
    client_start = sub.add_parser("client-start", help="Launch a prepared restricted Claude chat")
    client_start.add_argument("--directory", required=True)
    codex = sub.add_parser("codex-responder", help="Launch a dedicated native Codex responder")
    codex.add_argument("--config", required=True, help="Existing responder client.json")
    codex.add_argument("--directory", required=True, help="New private session directory")
    requester = sub.add_parser("codex-requester", help="Launch an automatic native Codex requester")
    requester.add_argument("--config", required=True, help="Existing requester client.json")
    requester.add_argument("--directory", required=True, help="New private session directory")
    requester.add_argument(
        "--execution", action="store_true", help="Explicitly enable the pair's command permission"
    )
    init = sub.add_parser("init", help="Create a fresh pair outside the project")
    sub.add_parser("proposal", help="Human-only local inspect/status/approve/reject")
    init.add_argument("--grant", choices=("ro", "rw", "exec"), default="ro")
    init.add_argument("--execution-backend", choices=("docker", "host"), default="docker")
    init.add_argument(
        "--allow-host-credentials",
        action="store_true",
        help="Acknowledge host exec can access relay/provider credentials and persist",
    )
    init.add_argument("--container-image", default="python:3.13-slim")
    init.add_argument("--project", required=True)
    init.add_argument("--pair-dir", required=True)
    init.add_argument(
        "--host", default="127.0.0.1", help="Reachable A hostname/IP for the TLS certificate"
    )
    init.add_argument("--port", type=int, default=9443)
    pair = sub.add_parser("pair", help="Show a spoken pairing code for a responder on this network")
    pair.add_argument("--project", required=True)
    pair.add_argument("--pair-dir", required=True, help="New or existing private pair folder")
    pair.add_argument("--grant", choices=("ro", "rw", "exec"), help="Default ro for a new pair")
    pair.add_argument(
        "--execution-backend",
        choices=("docker", "host"),
        help="Default docker for a new pair; existing backend is preserved",
    )
    pair.add_argument(
        "--allow-host-credentials",
        action="store_true",
        default=None,
        help="Acknowledge host exec exposes relay/provider credentials",
    )
    pair.add_argument("--container-image", help="Default python:3.13-slim for a new pair")
    pair.add_argument("--host", help="LAN address to use (default: detected)")
    pair.add_argument("--port", type=int, default=9443, help="Relay port; pairing uses port+1")
    pair.add_argument("--name", help="Name shown to the other person")
    pair.add_argument("--no-advertise", action="store_true", help="Skip mDNS; join by --address")
    join = sub.add_parser("join", help="Join a pairing by typing the code you were told")
    join.add_argument("--dir", required=True, help="New private folder for this pairing")
    join.add_argument("--code", help="Pairing code (prompted if omitted)")
    join.add_argument("--address", help="HOST:PORT if discovery is blocked on this network")
    join.add_argument("--name", help="Name shown to the other person")
    serve = sub.add_parser("serve", help="Run A's project relay")
    serve.add_argument("--config", required=True)
    serve.add_argument("--bind", help="Default is the configured host; one server process only")
    mcp = sub.add_parser("mcp", help="Run the local role-specific stdio MCP server")
    mcp.add_argument("--config", required=True)
    mcp.add_argument("--background", choices=("off", "claude-channel"), default="off")
    args = parser.parse_args()
    if args.command == "bootstrap":
        from .bootstrap import Setups, build_server

        with Setups(args.state_dir) as setups:
            build_server(setups).run(transport="stdio")
        return
    if args.command == "bootstrap-setup":
        from .client_setup import prepare_bootstrap

        try:
            print(
                json.dumps(prepare_bootstrap(args.client, args.state_dir, args.directory), indent=2)
            )
        except (ValueError, OSError) as error:
            parser.error(str(error))
        return
    if args.command in ("codex-responder", "codex-requester"):
        import asyncio

        from .codex_requester import run_requester
        from .codex_responder import run_responder

        if not sys.stdin.isatty() or not sys.stdout.isatty():
            parser.error("Dedicated Codex session requires a human terminal")
        try:
            if args.command == "codex-requester":
                asyncio.run(run_requester(args.config, args.directory, execution=args.execution))
            else:
                asyncio.run(run_responder(args.config, args.directory))
        except KeyboardInterrupt:
            raise SystemExit(130) from None
        except (ValueError, OSError, RuntimeError) as error:
            parser.error(str(error))
        return
    if args.command in ("client-setup", "client-start"):
        from .client_setup import prepare, start

        try:
            if args.command == "client-setup":
                print(json.dumps(prepare(
                    args.client, args.config, args.directory, background=args.background
                ), indent=2))
            else:
                start(args.directory)
        except (ValueError, OSError, KeyError) as error:
            parser.error(str(error))
        return
    if args.command == "init":
        print(
            json.dumps(
                initialize(
                    args.project,
                    args.pair_dir,
                    args.host,
                    args.port,
                    grant=args.grant,
                    execution_backend=args.execution_backend,
                    allow_host_credentials=args.allow_host_credentials,
                    container_image=args.container_image,
                ),
                indent=2,
            )
        )
        return
    if args.command in ("pair", "join"):
        from .pairing import PairingFailed
        from .pairing_cli import pair_host, pair_join, parse_address

        try:
            if args.command == "pair":
                pair_host(
                    args.project,
                    args.pair_dir,
                    grant=args.grant,
                    host=args.host,
                    port=args.port,
                    name=args.name,
                    advertise=not args.no_advertise,
                    execution_backend=args.execution_backend,
                    allow_host_credentials=args.allow_host_credentials,
                    container_image=args.container_image,
                )
            else:
                pair_join(
                    args.dir,
                    code=args.code,
                    address=parse_address(args.address) if args.address else None,
                    name=args.name,
                )
        except (ValueError, PairingFailed) as error:
            print(f"  ✗ {error}", file=sys.stderr)
            raise SystemExit(1) from None
        except KeyboardInterrupt:
            print("\n  Pairing cancelled; nothing was saved.", file=sys.stderr)
            raise SystemExit(130) from None
        return
    config = load(args.config)
    if args.command == "serve":
        import uvicorn

        from .api import create_app

        private_file(config["key_file"])
        uvicorn.run(
            create_app(config),
            host=args.bind or config["host"],
            port=config["port"],
            ssl_keyfile=config["key_file"],
            ssl_certfile=config["ca_file"],
            access_log=False,
            log_level="critical",
            proxy_headers=False,
            timeout_keep_alive=5,
            limit_concurrency=32,
        )
    else:
        from .mcp_server import build_server

        client = Client(config)
        try:
            build_server(client, background=args.background).run(transport="stdio")
        finally:
            client.close()


if __name__ == "__main__":
    main()
