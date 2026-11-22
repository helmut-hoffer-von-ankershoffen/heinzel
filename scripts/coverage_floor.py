# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///
"""The coverage floor, judged inside the pull request's own CI run: `python3 -I scripts/coverage_floor.py`.

The `Coverage floor` job of .github/workflows/ci.yml runs this after the Coverage job, and `CI OK` requires it. It
reads the total that this very run measured, at this run's merge commit, so there is no stale tested base and no
separate check to wait for. The verdict names the measured and the required percent, the scope and the least covered
files. A judge that broke says TOOL ERROR, so nobody hunts for a coverage gap that isn't there.

Environment (set by the workflow): COVERAGE_RESULT (the Coverage job's result), COVERAGE_TOTAL (the downloaded
coverage-total.txt; absent or empty when the job measured nothing), UNCOVERED (the Coverage job's least covered files),
FOCUSED_MODE (empty: this workflow runs full CI), GITHUB_SHA, GITHUB_OUTPUT and GITHUB_STEP_SUMMARY.
Standard library only.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

WORKFLOW = ".github/workflows/ci.yml"
NAME = "Coverage floor"
MAX_TOTAL = 64
MAX_PERCENT = 100
MAX_UNCOVERED = 12
TOTAL = re.compile(r"\d{1,3}(?:\.\d+)?")


class FloorError(ValueError):
    """The evidence was read and does not meet the floor: a real coverage verdict."""


class ToolError(RuntimeError):
    """The judge could not read its inputs: no coverage verdict was reached."""


def red_below(workflow: str) -> float:
    """The badge's red threshold (MINIMUM_ORANGE), the floor. Read from the workflow, so the two can't drift.

    Raises:
        ToolError: The workflow doesn't set it.
    """
    found = re.findall(r"^\s*MINIMUM_ORANGE:\s*[\"']?(\d+(?:\.\d+)?)[\"']?\s*$", workflow, re.MULTILINE)
    if not found:
        message = f"no MINIMUM_ORANGE in {WORKFLOW}: can't tell where the floor is"
        raise ToolError(message)
    return max(float(value) for value in found)


def fail_under(workflow: str) -> float | None:
    """The Coverage job's own `--fail-under`, the report threshold, if it sets one."""
    found = re.findall(r"coverage report [^\n]*--fail-under=(\d+(?:\.\d+)?)", workflow)
    return max(float(value) for value in found) if found else None


def measured(path: Path) -> float:
    """The whole-project total the Coverage job recorded, parsed strictly.

    Raises:
        ToolError: The file is missing, oversized or not a percentage.
    """
    try:
        data = path.read_bytes()
    except OSError as error:
        message = f"can't read the coverage-total artifact: {error}"
        raise ToolError(message) from error
    text = data[:MAX_TOTAL].decode(errors="replace").strip()
    if len(data) > MAX_TOTAL or not TOTAL.fullmatch(text) or float(text) > MAX_PERCENT:
        message = f"the coverage-total artifact is not a percentage: {text[:40]!r}"
        raise ToolError(message)
    return float(text)


def uncovered(listing: str) -> list[str]:
    """The Coverage job's least covered files, one indented line each, bounded."""
    rows = [line.rstrip()[:300] for line in listing.splitlines() if line.strip()]
    return [f"  {row}" for row in rows[:MAX_UNCOVERED]]


def judge(env: Mapping[str, str], workflow: str, notes: list[str]) -> str:
    """The passing verdict for this run, or why the floor is not met.

    Raises:
        FloorError: Coverage was measured and is below a threshold, or was not measured where it must be.
        ToolError: The judge's inputs are missing or unreadable.
    """
    if env.get("FOCUSED_MODE"):
        message = "this run selected focused CI; the in-run floor reads only the Coverage job's whole-project total"
        raise ToolError(message)
    result = env.get("COVERAGE_RESULT", "")
    commit = env.get("GITHUB_SHA", "")[:12] or "this run's commit"
    if result == "skipped":
        notes.append(
            "Scope: no Core Python changed (site-only or Apple/prose-only change), so the Coverage job did not run; "
            "CI OK names the jobs this change requires."
        )
        return f"{NAME}: not applicable, no Core Python coverage workload ran for this change"
    floor = red_below(workflow)
    threshold = fail_under(workflow)
    required = f"{floor:g}% floor" + (f" and {threshold:g}% report threshold (--fail-under)" if threshold else "")
    total_path = env.get("COVERAGE_TOTAL", "")
    # An empty file is a report that printed nothing: no data was combined because the tests failed.
    if not total_path or not Path(total_path).is_file() or not Path(total_path).stat().st_size:
        if result == "success":
            message = "the Coverage job passed but left no coverage-total artifact"
            raise ToolError(message)
        message = (
            f"the Coverage job ended with {result or 'no result'} before it measured a total "
            f"(its tests failed or it was stopped); required {required}. See the Coverage job's log."
        )
        raise FloorError(message)
    total = measured(Path(total_path))
    notes.append(f"Whole-project coverage: measured {total:g}%, required {required}.")
    notes.append(f"Scope: the whole project, at the merge commit {commit} that this run tested.")
    rows = uncovered(env.get("UNCOVERED", ""))
    if rows:
        notes.extend(["Least covered files (statements, missed, branches, partial, cover, missing lines):", *rows])
    if total < floor:
        message = f"whole-project coverage {total:g}% is below the {floor:g}% floor"
        raise FloorError(message)
    if threshold is not None and total < threshold:
        message = f"whole-project coverage {total:g}% is below the {threshold:g}% report threshold (--fail-under)"
        raise FloorError(message)
    if result != "success":
        message = f"the Coverage job ended with {result} after measuring {total:g}%; see the Coverage job's log"
        raise FloorError(message)
    return f"{NAME} passed: whole-project coverage {total:g}% (required {required})"


def publish(env: Mapping[str, str], headline: str, notes: list[str], *, failed: bool) -> None:
    """Print the verdict, annotate a failure, and hand the headline to CI OK and the details to the job summary."""
    text = "\n".join([headline, *notes])
    print(text)
    if failed:
        print(f"::error title={NAME}::{headline}")
    if path := env.get("GITHUB_OUTPUT"):
        with Path(path).open("a", encoding="utf-8") as output:
            output.write(f"verdict={headline[:1000].replace(chr(10), ' ')}\n")
    if path := env.get("GITHUB_STEP_SUMMARY"):
        with Path(path).open("a", encoding="utf-8") as summary:
            summary.write(f"### {headline}\n\n```\n" + "\n".join(notes).replace("```", "'''") + "\n```\n")


def main(env: Mapping[str, str] = os.environ, root: Path = Path()) -> int:
    """Judge, publish and return the exit code: 0 passed, 1 not met, 2 tool error."""
    notes: list[str] = []
    try:
        verdict = judge(env, (root / WORKFLOW).read_text(encoding="utf-8"), notes)
    except FloorError as error:
        publish(env, f"{NAME} FAILED (not met): {error}", notes, failed=True)
        return 1
    except (ToolError, OSError, ValueError) as error:
        headline = f"{NAME} TOOL ERROR (the judge broke, coverage was not judged): {type(error).__name__}: {error}"
        publish(env, headline, notes, failed=True)
        return 2
    publish(env, verdict, notes, failed=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
