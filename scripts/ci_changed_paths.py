# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///
"""Enumerate every changed endpoint for CI; invalid comparisons fail closed at the caller."""

import re
import subprocess
import sys


def main() -> int:
    """Use the PR merge base or the actual push before tree, with no rename collapsing."""
    event, base = sys.argv[1:]
    if not re.fullmatch(r"[0-9a-f]{40}", base):
        return 1
    base_commit = f"{int(base, 16):040x}"
    revisions = [f"{base_commit}...HEAD"] if event == "pull_request" else [base_commit, "HEAD"]
    result = subprocess.run(  # noqa: S603 - revisions follow end-of-options
        ["git", "diff", "--name-only", "--no-renames", "-z", "--end-of-options", *revisions, "--"],  # noqa: S607
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode:
        return 1
    paths = result.stdout.decode("utf-8").split("\0")
    # The downstream selectors consume lines: ambiguous names must retain all app checks.
    if any("\n" in path or "\r" in path for path in paths):
        return 1
    sys.stdout.write("\n".join(path for path in paths if path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
