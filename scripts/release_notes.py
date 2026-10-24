# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Print one release's section of CHANGELOG.md, for the GitHub release notes.

The notes come from the changelog as merged in the release pull request, so edits made during review are published.

Usage: uv run --script scripts/release_notes.py v1.2.3 [CHANGELOG.md]
"""

import re
import sys
from pathlib import Path


def section(changelog: str, version: str) -> str:
    """The body of `## [version]` up to the next release heading.

    Raises:
        ValueError: If the changelog has no section for `version`.
    """
    number = re.escape(version.removeprefix("v"))
    match = re.search(rf"^## \[{number}\].*?$(.*?)(?=^## \[|\Z)", changelog, flags=re.MULTILINE | re.DOTALL)
    if match is None:
        msg = f"CHANGELOG.md has no section for {version}"
        raise ValueError(msg)
    return match.group(1).strip() + "\n"


def main() -> int:
    """Print the section, or explain why it is missing."""
    version, path = sys.argv[1], Path(sys.argv[2] if len(sys.argv) > 2 else "CHANGELOG.md")  # noqa: PLR2004
    try:
        print(section(path.read_text(encoding="utf-8"), version), end="")
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
