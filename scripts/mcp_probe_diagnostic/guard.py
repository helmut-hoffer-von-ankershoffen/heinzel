"""Fail closed before running the one-off, dispatch-only MCP diagnostic."""

import os
import re
import subprocess
import sys
from collections.abc import Mapping


def authorized(env: Mapping[str, str], head: str) -> bool:
    """Only the designated branch and an explicitly pinned checkout may run."""
    expected = env.get("EXPECTED_SHA", "")
    return (
        env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and env.get("GITHUB_REF") == "refs/heads/ci/mcp-macos-diagnostic"
        and env.get("FOCUSED_REF") == "ci/mcp-macos-diagnostic"
        and env.get("REQUESTED_OS") == "macos-latest"
        and env.get("REQUESTED_KIND") == "pytest"
        and env.get("REQUESTED_REPEAT") == "1"
        and env.get("REQUESTED_STRESS") == "false"
        and re.fullmatch(r"[0-9a-f]{40}", expected) is not None
        and expected == env.get("GITHUB_SHA") == head
    )


def main() -> int:
    """Check the checked-out commit without displaying request data."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607 - fixed command
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode or not authorized(os.environ, result.stdout.strip()):
        print("MCP diagnostic authorization refused")
        return 1
    print("MCP diagnostic authorization accepted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
