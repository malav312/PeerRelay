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
    serve = sub.add_parser("serve", help="Run A's project relay")
    serve.add_argument("--config", required=True)
    serve.add_argument("--bind", help="Default is the configured host; one server process only")
    mcp = sub.add_parser("mcp", help="Run the local role-specific stdio MCP server")
    mcp.add_argument("--config", required=True)
    mcp.add_argument("--background", choices=("off", "claude-channel"), default="off")
    args = parser.parse_args()
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
