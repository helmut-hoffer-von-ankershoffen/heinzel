# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///
"""Write scripts/release_policy.json: the release facts other tools read from this exact commit, all derived here.

The release tooling outside this repository reads the manifest with `git show COMMIT:scripts/release_policy.json`
instead of keeping its own copies of job names, lanes and contexts. The commit and its review verdict bind the
manifest: nothing in it pins file bytes. The Lint job runs `--check`, so a commit whose manifest is stale fails CI.
Usage: python scripts/release_policy.py [--check]   (--check: fail when the committed manifest is stale)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

# The workflow launcher and a direct run supply only this exact checkout's modules.
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_paths import not_ci
from scripts.focused_plan import LANES
from scripts.release_candidate import (
    QUARANTINE_IDLE,
    QUARANTINE_PLAN,
    QUARANTINE_REGISTRY,
    QUARANTINE_RUNS,
    RECORDER,
    REPOSITORY,
    WORKFLOW,
    expected_jobs,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "scripts/release_policy.json"
POLICY = 1
EVENTS = ("pull_request", "workflow_dispatch")
AGGREGATE_JOB = "ci-ok"
FOCUSED_JOB = "focused-tests"
LANE_EXPRESSION = "${{ matrix.lane }}"
# The status-check context of this workflow that the `main` ruleset requires: its aggregate job. The coverage floor is
# a job of the workflow that the aggregate needs (scripts/coverage_floor.py), so no separate gate context exists.
LANE_NAME = re.compile(r"[a-z0-9-]+")
TEST_JOB = re.compile(r"Test \(.+\)")


class PolicyError(ValueError):
    """The workflow, recorder or lanes cannot be stated as the reviewed release policy."""


def require(condition: object, message: str) -> None:
    """Refuse to write a manifest whose facts contradict each other.

    Raises:
        PolicyError: the condition does not hold.
    """
    if not condition:
        raise PolicyError(message)


def job_name(workflow: str, job: str) -> str:
    """Read the display name of one job of the workflow."""
    found = re.search(rf"^  {re.escape(job)}:\n(?:    .*\n)*?    name: (.+)$", workflow, re.MULTILINE)
    require(found, f"{WORKFLOW} has no named {job} job")
    assert found  # noqa: S101 - narrowed by require
    return found.group(1).strip()


def aggregate(workflow: str) -> str:
    """The display name of the workflow's aggregate job, the one required status check of the workflow."""
    return job_name(workflow, AGGREGATE_JOB)


def focused_job(workflow: str) -> str:
    """The focused-lane job name with `{lane}` where the workflow expands the lane."""
    name = job_name(workflow, FOCUSED_JOB)
    require(name.count(LANE_EXPRESSION) == 1 and "{" not in name.replace(LANE_EXPRESSION, ""), f"{name} names no lane")
    return name.replace(LANE_EXPRESSION, "{lane}")


def tracked(root: Path) -> list[str]:
    """Every path Git tracks in this checkout."""
    listing = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 - git from PATH, as every script here
        cwd=root,
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout.decode()
    return [path for path in listing.split("\0") if path]


def policy(root: Path = ROOT, listing: list[str] | None = None) -> dict[str, object]:
    """Derive the manifest from the recorder, the workflow, the focused lanes and the tracked test files."""
    paths = tracked(root) if listing is None else listing
    rule = not_ci(root, paths)
    workflow = (root / WORKFLOW).read_text(encoding="utf-8")
    jobs = {event: expected_jobs(event) for event in EVENTS}
    split = {
        event: {
            conclusion: sorted(name for name, expected in inventory.items() if expected == conclusion)
            for conclusion in ("skipped", "success")
        }
        for event, inventory in jobs.items()
    }
    name = aggregate(workflow)
    for event in EVENTS:
        require(jobs[event].get(name) == "success", f"{name} is not a required success job on {event}")
    tests = sorted({job for event in EVENTS for job in split[event]["success"] if TEST_JOB.fullmatch(job)})
    for lane in LANES:
        require(LANE_NAME.fullmatch(lane), f"focused lane {lane!r} is not a plain lane name")
    return {
        "aggregate": name,
        "expected_jobs": split,
        "focused_job": focused_job(workflow),
        "not_ci": rule,
        "lanes": {
            lane: {"kind": kind, "python": python, "runner": runner} for lane, (runner, python, kind) in LANES.items()
        },
        "policy": POLICY,
        "quarantine": {
            "idle": QUARANTINE_IDLE,
            "plan": QUARANTINE_PLAN,
            "registry": QUARANTINE_REGISTRY,
            "runs": sorted(QUARANTINE_RUNS),
        },
        "recorder": RECORDER,
        "repository": REPOSITORY,
        "required_contexts": [name],
        "test_jobs": tests,
        "workflow": WORKFLOW,
    }


def render(root: Path = ROOT, listing: list[str] | None = None) -> str:
    """The exact manifest bytes: sorted keys, two-space indent, one trailing newline."""
    return json.dumps(policy(root, listing), indent=2, sort_keys=True) + "\n"


def main(args: list[str]) -> int:
    """Write the manifest, or with --check report whether the committed one is current."""
    target = ROOT / MANIFEST
    text = render()
    if args == ["--check"]:
        if not target.is_file() or target.read_text(encoding="utf-8") != text:
            print(f"{MANIFEST} is stale: run python scripts/release_policy.py", file=sys.stderr)
            return 1
        return 0
    if args:
        print(__doc__, file=sys.stderr)
        return 2
    target.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
