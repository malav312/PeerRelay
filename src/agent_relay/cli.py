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
    init = sub.add_parser("init", help="Create a fresh pair outside the project")
    sub.add_parser("proposal", help="Human-only local inspect/status/approve/reject")
    init.add_argument("--grant", choices=("ro", "rw", "exec"), default="ro")
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
    args = parser.parse_args()
    if args.command == "init":
        print(
            json.dumps(
                initialize(args.project, args.pair_dir, args.host, args.port, grant=args.grant),
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
            build_server(client).run(transport="stdio")
        finally:
            client.close()


if __name__ == "__main__":
    main()
