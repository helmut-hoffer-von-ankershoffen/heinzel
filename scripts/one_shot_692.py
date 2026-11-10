# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""One-time normal merge, bound to the reviewed pull request and source.

External coordination must hold the merge queue and freeze competing base writes. GitHub's merge API binds the head,
not the base: even the final base read below cannot make that check atomic with the merge request.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

REPO = "helmut-hoffer-von-ankershoffen/heinzel"
HEAD = "2b2c5dc3e9c7156e0a2a3e4497b109bf99e52add"
BASE = "432006f27ce2df73a80f2f69a4e162378df2c862"
GATES = {"merge-gate", "coverage-floor"}
GITHUB_ACTIONS_APP = 15368
PR_NUMBER = 692
CI_RUN = 42767677534
CI_JOB = 129674241526
RECOVERY_RUN = 42772900169
RECOVERY_SHA = "28c7541d3f5886a56ff6fa4ffe03901f491b725b"


def api(path: str, *args: str) -> Any:  # noqa: ANN401
    """Read paginated GitHub data using fixed application-supplied paths."""
    command = ["gh", "api", "--paginate", "--slurp", f"repos/{REPO}/{path}", *args]
    return json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)  # noqa: S603


def one(path: str) -> dict[str, Any]:
    """Read a single response.

    Raises:
        ValueError: The API response is not a single object.
    """
    pages = api(path)
    if len(pages) != 1 or not isinstance(pages[0], dict):
        msg = f"unexpected response: {path}"
        raise ValueError(msg)
    return pages[0]


def validate() -> None:
    """Check source, PR state, base and actual associated required check runs.

    Raises:
        ValueError: Any binding or evidence is invalid.
    """
    if (
        os.environ.get("GITHUB_REPOSITORY") != REPO
        or os.environ.get("GITHUB_REF") != "refs/heads/bootstrap/one-shot-692"
    ):
        msg = "unexpected repository or workflow ref"
        raise ValueError(msg)
    if not os.environ.get("REVIEWED_HELPER_SHA") or os.environ.get("GITHUB_SHA") != os.environ["REVIEWED_HELPER_SHA"]:
        msg = "helper SHA does not match the separately configured review"
        raise ValueError(msg)
    pull = one("pulls/692")
    if not (
        pull.get("state") == "open"
        and pull.get("draft") is False
        and pull.get("head", {}).get("sha") == HEAD
        and pull.get("base", {}).get("sha") == BASE
        and pull.get("base", {}).get("ref") == "main"
        and pull.get("mergeable") is True
        and pull.get("mergeable_state") == "clean"
    ):
        msg = "pull request is not the expected open, non-draft, clean head and base"
        raise ValueError(msg)
    if one("commits/main").get("sha") != BASE:
        msg = "main moved"
        raise ValueError(msg)
    for run_id, sha, event, path, attempt in (
        (CI_RUN, HEAD, "pull_request", ".github/workflows/ci.yml", 2),
        (RECOVERY_RUN, RECOVERY_SHA, "push", ".github/workflows/checks-status-recovery.yml", 1),
    ):
        run = one(f"actions/runs/{run_id}")
        if not (
            run.get("id") == run_id
            and run.get("head_sha") == sha
            and run.get("event") == event
            and run.get("path") == path
            and run.get("run_attempt") == attempt
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
        ):
            msg = f"incorrect or unfinished evidence run: {run_id}"
            raise ValueError(msg)
    pages = api(f"commits/{HEAD}/check-runs?per_page=100")
    checks = [check for page in pages for check in page.get("check_runs", [])]
    matches = [check for check in checks if check.get("name") == "CI OK"]
    if not matches or any(
        check.get("status") != "completed"
        or check.get("conclusion") != "success"
        or (check.get("app") or {}).get("id") != GITHUB_ACTIONS_APP
        or not any(pr.get("number") == PR_NUMBER for pr in check.get("pull_requests", []))
        or check.get("id") != CI_JOB
        or check.get("details_url") != f"https://github.com/{REPO}/actions/runs/{CI_RUN}/job/{CI_JOB}"
        for check in matches
    ):
        msg = "missing, unassociated, untrusted or non-successful CI OK check"
        raise ValueError(msg)
    combined = one(f"commits/{HEAD}/status?per_page=100")
    statuses = combined.get("statuses", [])
    for name in GATES:
        matches = [status for status in statuses if status.get("context") == name]
        if (
            len(matches) != 1
            or matches[0].get("state") != "success"
            or matches[0].get("target_url") != (f"https://github.com/{REPO}/actions/runs/{RECOVERY_RUN}")
        ):
            msg = f"missing or incorrect recovery status: {name}"
            raise ValueError(msg)


def main() -> None:
    """Validate and optionally request a head-bound merge.

    Raises:
        ValueError: A precondition or merge response is invalid.
    """
    if sys.argv[1:] not in (["check"], ["merge"]):
        msg = "expected check or merge"
        raise ValueError(msg)
    validate()
    if sys.argv[1] == "merge":
        # This last read narrows, but cannot eliminate, the base movement race.
        if one("commits/main").get("sha") != BASE:
            msg = "main moved before merge"
            raise ValueError(msg)
        result = one_merge()
        if result.get("merged") is not True or not result.get("sha"):
            msg = "GitHub did not confirm a merge"
            raise ValueError(msg)
        print(f"GitHub confirmed merge: {result['sha']}")
        merged = one(f"commits/{result['sha']}")
        if [parent.get("sha") for parent in merged.get("parents", [])] != [BASE]:
            msg = "merge occurred but its parent was not the expected base; stop and investigate"
            raise ValueError(msg)
        if one("commits/main").get("sha") != result["sha"]:
            msg = "merge occurred but main moved afterward; stop and investigate"
            raise ValueError(msg)
        print(f"Observed main and squash parent: {result['sha']} {BASE}")
    else:
        print("Exact PR and genuine required check runs validated")


def one_merge() -> dict[str, Any]:
    """Request the normal squash merge using a fixed command and fixed head."""
    result = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "gh",
            "api",
            "--method",
            "PUT",
            f"repos/{REPO}/pulls/692/merge",
            "-f",
            f"sha={HEAD}",
            "-f",
            "merge_method=squash",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
