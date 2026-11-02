# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Decide what a failed Claude review means for its check, from the action's execution file.

A review that could not start because of a limit outside the pull request (credit, usage or rate limit, an overloaded
API) says nothing about the change: warn and exit 0. Anything else stays red: a bad key, a turn limit, a crash, a run
that cost something before failing, or no readable result at all. The action hides the error text from its log, so this
prints it.

Usage: uv run --script scripts/review_outcome.py EXECUTION_FILE
"""

import json
import os
import re
import sys
from pathlib import Path

EXTERNAL_LIMIT = re.compile(
    r"credit balance|usage limit|spending limit|rate.?limit|hit your .*limit|overloaded|API Error: (429|5\d\d)",
    re.IGNORECASE,
)
MAX_SHOWN = 300


def result_message(execution_file: Path) -> dict | None:
    """The last result message of the run, or None if the file is missing, unreadable or has none."""
    try:
        messages = json.loads(execution_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(messages, list):
        return None
    results = [m for m in messages if isinstance(m, dict) and m.get("type") == "result"]
    return results[-1] if results else None


def could_not_run(result: dict | None) -> bool:
    """Whether the run failed on an external limit before doing any work."""
    if result is None or not result.get("is_error") or result.get("total_cost_usd") != 0:
        return False
    return bool(EXTERNAL_LIMIT.search(str(result.get("result", ""))))


def shown(result: dict | None) -> str:
    """The error text on one line, short, unable to start a workflow command."""
    if result is None:
        return "no result message"
    text = str(result.get("result") or result.get("errors") or result.get("subtype") or "no error text")
    return " ".join(text.split()).replace("::", ": :")[:MAX_SHOWN]


def main(argv: list[str]) -> int:
    """Print the verdict, add it to the step summary, and return the exit code of the check."""
    result = result_message(Path(argv[1]))
    skipped = could_not_run(result)
    message = (
        f"Claude review skipped, nothing was reviewed: {shown(result)}"
        if skipped
        else f"Claude review failed: {shown(result)}"
    )
    print(f"::{'warning' if skipped else 'error'}::{message}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as out:
            out.write(f"{message}\n")
    return 0 if skipped else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
