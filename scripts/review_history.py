"""A spend-limit exception cannot override a prior same-head Gate rejection."""

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable

WORKFLOW = ".github/workflows/claude-review.yml"
REJECT_STEP = "Record explicit Gate review rejection"
LEGACY_JUDGE = "Judge the review and comment"


def _rows(document: object, key: str) -> tuple[int, list[dict]]:
    if isinstance(document, dict):
        total, records = document.get("total_count"), document.get(key)
        if (
            type(total) is int
            and total >= 0
            and isinstance(records, list)
            and all(isinstance(row, dict) for row in records)
        ):
            return total, records
    msg = "GitHub returned an unreadable Gate review inventory"
    raise ValueError(msg)


def _previous_attempts(
    get: Callable[[str], object], repo: str, head: str, run_id: int, attempt: int
) -> list[tuple[int, int]]:
    start = (
        f"repos/{repo}/actions/workflows/claude-review.yml/runs?event=pull_request_target&head_sha={head}&per_page=100"
    )
    known: dict[int, int] = {}
    count = None
    for page in range(1, 11):  # GitHub caps filtered workflow-run searches at 1,000 results.
        total, runs = _rows(get(f"{start}&page={page}"), "workflow_runs")
        if count is None:
            count = total
        if total != count or len(runs) != min(100, max(0, total - len(known))):
            msg = "the same-head Gate workflow inventory changed or is incomplete"
            raise ValueError(msg)
        for run in runs:
            identity, version = run.get("id"), run.get("run_attempt")
            if (
                (run.get("path"), run.get("event"), run.get("head_sha")) != (WORKFLOW, "pull_request_target", head)
                or type(identity) is not int
                or identity < 1
                or identity in known
                or type(version) is not int
                or version < 1
            ):
                msg = "GitHub returned inconsistent exact-head Gate workflow identity"
                raise ValueError(msg)
            known[identity] = version
        if len(known) == total:
            break
    if len(known) != count or known.get(run_id) != attempt:
        msg = "the current Gate run and attempt are not in a complete same-head inventory"
        raise ValueError(msg)
    return [
        (identity, version) for identity, latest in known.items() for version in range(1, latest + (identity != run_id))
    ]


def _verify_attempt(get: Callable[[str], object], repo: str, head: str, run_id: int, attempt: int) -> None:
    count, jobs = _rows(get(f"repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"), "jobs")
    gate = [job for job in jobs if job.get("name") == "Gate review"]
    if count != len(jobs) or len(gate) != 1:
        msg = "a prior same-head Gate attempt has no complete job evidence"
        raise ValueError(msg)
    job = gate[0]
    if (job.get("run_id"), job.get("run_attempt"), job.get("head_sha"), job.get("status")) != (
        run_id,
        attempt,
        head,
        "completed",
    ):
        msg = "a prior same-head Gate attempt is incomplete or has mismatched identity"
        raise ValueError(msg)
    steps = job.get("steps")
    if not isinstance(steps, list) or not all(isinstance(step, dict) for step in steps):
        msg = "a prior same-head Gate attempt has unreadable steps"
        raise ValueError(msg)
    rejected = [(step.get("status"), step.get("conclusion")) for step in steps if step.get("name") == REJECT_STEP]
    if rejected == [("completed", "success")]:
        msg = "a prior Gate reviewer rejected this exact head; the spending-limit exception is unavailable"
        raise ValueError(msg)
    if rejected == [("completed", "skipped")] and job.get("conclusion") in ("success", "failure"):
        return
    # The original workflow only succeeded when the reviewer approved. A failed legacy judge may have rejected the head.
    legacy = [(step.get("status"), step.get("conclusion")) for step in steps if step.get("name") == LEGACY_JUDGE]
    if not rejected and job.get("conclusion") == "success" and legacy == [("completed", "success")]:
        return
    msg = "a prior same-head Gate result cannot rule out an explicit rejection"
    raise ValueError(msg)


def check(get: Callable[[str], object], repo: str, head: str, run_id: int, attempt: int) -> None:
    """Verify complete same-head history for the current protected workflow attempt.

    Raises:
        ValueError: A rejection was found or GitHub could not prove the review history.

    """
    if not re.fullmatch(r"[a-f0-9]{40}", head) or run_id < 1 or attempt < 1:
        msg = "the current Gate head, run or attempt is invalid"
        raise ValueError(msg)
    for identity, version in _previous_attempts(get, repo, head, run_id, attempt):
        _verify_attempt(get, repo, head, identity, version)


def _github(path: str) -> object:
    result = subprocess.run(["gh", "api", path], text=True, capture_output=True, check=False, timeout=30)  # noqa: S603, S607
    if result.returncode:
        msg = "GitHub could not supply prior Gate review attempts"
        raise ValueError(msg)
    return json.loads(result.stdout)


def main() -> int:
    """Run the history check and surface only its fixed diagnostic messages."""
    try:
        check(
            _github,
            os.environ["GITHUB_REPOSITORY"],
            os.environ["PR_HEAD"],
            int(os.environ["GITHUB_RUN_ID"]),
            int(os.environ["GITHUB_RUN_ATTEMPT"]),
        )
    except (json.JSONDecodeError, OSError, subprocess.SubprocessError, KeyError):
        print("::error::GitHub Gate review history could not be verified", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
