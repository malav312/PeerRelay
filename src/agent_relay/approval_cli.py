"""Offline requester CLI: no gateway calls and no model calls."""

import argparse
import json
import sqlite3
import sys

from .approvals import escaped
from .client import load
from .filesystem import Refused
from .local_approvals import LocalDecisions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--json", action="store_true", help="Machine-readable, escaped output")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "status", "approve", "reject"):
        sub = commands.add_parser(name)
        sub.add_argument("proposal_id")
        if name in ("approve", "reject"):
            sub.add_argument("--digest", required=True, help="Exact digest printed by inspect")
    args = parser.parse_args(argv)
    service = None
    try:
        service = LocalDecisions(load(args.config))
        if args.command in ("inspect", "status"):
            result = service.inspect(args.proposal_id)
        else:
            if not sys.stdin.isatty() or not sys.stderr.isatty():
                raise ValueError(
                    "Decisions require an interactive terminal; this is not proof of a human"
                )
            review = service.inspect(args.proposal_id)
            print("Review target:", escaped(review.get("path", "")), file=sys.stderr)
            for warning in review.get("risk_warnings", []):
                print("WARNING:", warning, file=sys.stderr)
            print("Type " + args.command + " " + args.digest + " to confirm:", file=sys.stderr)
            if sys.stdin.readline().strip() != args.command + " " + args.digest:
                raise ValueError("Confirmation did not match; no decision recorded")
            result = service.decide(args.proposal_id, args.digest, args.command)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=True))
        else:
            print("Status:", result["status"])
            print("Proposal:", escaped(result["id"]))
            print("Approval digest:", result["digest"])
            if "path" in result:
                print("Registered root:", escaped(result["registered_root"]))
                print("Root identity (path/device/inode):", json.dumps(result["root_identity"]))
                print("Original grants:", json.dumps(result["grants"], sort_keys=True))
                print("Baseline hash (canonical JSON SHA-256):", result["base_sha256"])
                for warning in result.get("risk_warnings", []):
                    print("WARNING:", warning)
                print("Target:", escaped(result["root"] + "/" + result["path"]))
                print("Expected version:", result["expected_version"])
                print("Target still matches:", result["current_matches"])
                print("Diff (control characters and literal backslashes escaped):")
                for line in result["diff"].splitlines(keepends=True):
                    print(escaped(line.rstrip("\n"))[1:-1])
                print("Exact proposed string:", result["proposed_escaped"])
            print("Detail:", json.dumps(result["detail"], ensure_ascii=True))
        if result["status"] in ("stale", "uncertain"):
            return 2
        return 0
    except sqlite3.Error:
        print(
            json.dumps(
                {
                    "status": "uncertain",
                    "message": "Cannot establish durable status; inspect when journal is available",
                }
            )
        )
        return 2
    except (OSError, ValueError, KeyError, Refused) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=True))
        return 1
    finally:
        if service:
            service.__exit__(None, None, None)


if __name__ == "__main__":
    raise SystemExit(main())
