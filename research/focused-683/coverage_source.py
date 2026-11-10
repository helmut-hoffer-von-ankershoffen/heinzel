# ruff: noqa: INP001
"""Record all Python statement and branch opportunities without importing production code."""

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

from coverage import Coverage


def main() -> None:
    """Analyze every reviewed source path, including files with no execution data.

    Raises:
        SystemExit: If the source analyzer did not return the reviewed branch-aware scope.
    """
    out, *paths = sys.argv[1:]
    coverage = Coverage()
    coverage.get_data().add_arcs({path: [] for path in paths})
    stream = io.StringIO()
    with redirect_stdout(stream):
        coverage.json_report(morfs=paths, outfile="-")
    data = json.loads(stream.getvalue())
    if data["meta"]["branch_coverage"] is not True or set(data["files"]) != set(paths):
        message = "Source analysis did not cover the complete reviewed Python scope"
        raise SystemExit(message)
    inventory = {
        path: {"statements": report["missing_lines"], "branches": report["missing_branches"]}
        for path, report in data["files"].items()
    }
    Path(out).write_text(json.dumps(inventory) + "\n")


if __name__ == "__main__":
    main()
