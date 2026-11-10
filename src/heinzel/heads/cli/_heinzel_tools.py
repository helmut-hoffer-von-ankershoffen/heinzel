"""CLI spelling for the same admitted individual-Heinzel call path."""

import argparse
import json
import sys
from typing import TYPE_CHECKING

from pydantic import ValidationError

from heinzel.kernel import HeinzelError
from heinzel.kernel._heinzel_invocation import MAX_MESSAGE_LENGTH, HeinzelInput

if TYPE_CHECKING:
    from heinzel.heads._heinzel_tools import HeinzelTools

_REFUSED = "The reviewed Heinzel request is invalid or unavailable"


def main(arguments: list[str], tools: "HeinzelTools | None") -> int:
    """Parse a fixed command before discovery; a tool name never establishes authority."""
    parser = argparse.ArgumentParser(prog="hz system mcp tools")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List reviewed Heinzels")
    invoke = commands.add_parser("invoke", help="Invoke a reviewed Heinzel")
    invoke.add_argument("tool", help="Reviewed MCP tool name or typed target key from tools list")
    invoke.add_argument("--request-id", required=True)
    messages = invoke.add_mutually_exclusive_group()
    messages.add_argument("--message")
    messages.add_argument("--message-stdin", action="store_true", help="Read the personal message from standard input")
    args = parser.parse_args(arguments)
    if tools is None:
        sys.stderr.write(_REFUSED + "\n")
        return 1
    try:
        if args.command == "list":
            result = (
                json.dumps(
                    [
                        {"name": target.tool_name, "kind": target.kind, "target": target.identity, "key": target.key}
                        for target in tools.targets()
                    ]
                )
                + "\n"
            )
        else:
            message = sys.stdin.read(MAX_MESSAGE_LENGTH + 1) if args.message_stdin else args.message
            request = HeinzelInput(request_id=args.request_id, message=message)
            # Resolve a typed identity only inside this lease's reviewed, current targets. The tool call
            # checks authority and target freshness again; a name supplied by the caller grants nothing.
            selected = args.tool
            if selected.startswith(("personal:", "contract:")):
                selected = next((target.tool_name for target in tools.targets() if target.key == selected), "")
            result = tools.call(selected, request).decode()
    except (HeinzelError, OSError, UnicodeError, ValidationError):
        sys.stderr.write(_REFUSED + "\n")
        return 1
    sys.stdout.write(result)
    return 0
