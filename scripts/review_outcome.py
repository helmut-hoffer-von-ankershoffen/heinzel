# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Decide what one Claude review attempt means, from the review step's outcome and the action's execution file.

The verdict is `reviewed` (the step succeeded), `skipped` (it failed on a limit outside the pull request before doing
any work: credit balance, usage or spending limit, rate limit, overload), `failed` (any other failure: a bad key, a turn
limit, a crash, a run that cost something, no readable result) or `none` (no review step ran). It goes to the step
outputs `verdict` and `reason`; the workflow reports `skipped` as a neutral check and fails the review check on
`failed`, so a review that did not run never passes. The action hides the error text from its log; this prints it.

The execution file is the one the action writes, `$RUNNER_TEMP/claude-execution-output.json`.

Usage: python3 scripts/review_outcome.py STEP_OUTCOME
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
ARGS = 2
EXECUTION_FILE = "claude-execution-output.json"


def one_line(text: object) -> str:
    """Text on one short line that cannot start a workflow command or a new step output."""
    return " ".join(str(text).split()).replace("::", ": :")[:MAX_SHOWN]


def result_message(execution_file: Path) -> dict[str, object] | None:
    """The last result message of the run, or None if the file is missing, unreadable or has none."""
    try:
        messages = json.loads(execution_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(messages, list):
        return None
    results = [m for m in messages if isinstance(m, dict) and m.get("type") == "result"]
    return results[-1] if results else None


def could_not_start(result: dict[str, object] | None) -> bool:
    """Whether the run failed on an external limit before doing any work."""
    if result is None or not result.get("is_error") or result.get("total_cost_usd") != 0:
        return False
    return bool(EXTERNAL_LIMIT.search(str(result.get("result", ""))))


def shown(result: dict[str, object] | None) -> str:
    """The error text of a result message on one line."""
    if result is None:
        return "no result message"
    return one_line(result.get("result") or result.get("errors") or result.get("subtype") or "no error text")


def verdict(outcome: str, execution_file: Path) -> tuple[str, str]:
    """(verdict, reason) of one attempt, from the review step's outcome."""
    if outcome == "success":
        return "reviewed", "Claude reviewed this pull request"
    if outcome != "failure":
        return "none", "no review step ran for this pull request"
    result = result_message(execution_file)
    return ("skipped" if could_not_start(result) else "failed"), shown(result)


def main(argv: list[str]) -> int:
    """Write the verdict to the step outputs and summary; exit 0 unless called wrongly."""
    if len(argv) != ARGS:
        print(__doc__, file=sys.stderr)
        return 2
    kind, reason = verdict(argv[1], Path(os.environ.get("RUNNER_TEMP", ".")) / EXECUTION_FILE)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as out:
            out.write(f"verdict={kind}\nreason={reason}\n")
    line = f"Claude review {kind}: {reason}"
    print(f"::notice::{line}" if kind == "skipped" else line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as out:
            out.write(f"{line}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
