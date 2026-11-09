"""CLI spelling for the same admitted individual-Heinzel call path."""

import argparse
import json
import sys
from typing import TYPE_CHECKING

from pydantic import ValidationError

from heinzel.kernel import HeinzelError
from heinzel.kernel._heinzel_invocation import HeinzelInput

if TYPE_CHECKING:
    from heinzel.heads._heinzel_tools import HeinzelTools

_REFUSED = "The reviewed Heinzel request is invalid or unavailable"


def main(arguments: list[str], tools: "HeinzelTools | None") -> int:
    """Parse a fixed command before discovery; a tool name never establishes authority."""
    parser = argparse.ArgumentParser(prog="hz system mcp tools")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List reviewed Heinzels")
    invoke = commands.add_parser("invoke", help="Invoke a reviewed Heinzel")
    invoke.add_argument("tool")
    invoke.add_argument("--request-id", required=True)
    invoke.add_argument("--message")
    args = parser.parse_args(arguments)
    if tools is None:
        sys.stderr.write(_REFUSED + "\n")
        return 1
    try:
        if args.command == "list":
            result = (
                json.dumps(
                    [
                        {"name": target.tool_name, "kind": target.kind, "target": target.identity}
                        for target in tools.targets()
                    ]
                )
                + "\n"
            )
        else:
            request = HeinzelInput(request_id=args.request_id, message=args.message)
            result = tools.call(args.tool, request).decode()
    except (HeinzelError, OSError, ValidationError):
        sys.stderr.write(_REFUSED + "\n")
        return 1
    sys.stdout.write(result)
    return 0
