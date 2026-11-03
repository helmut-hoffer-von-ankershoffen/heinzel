"""Validate a public main batch before delegating an intermediate CI run.

Only the queue publisher can attest a batch. Unknown evidence runs full CI.
The queue vendors this module to keep both readers on the same wire format.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

if TYPE_CHECKING:
    from collections.abc import Callable

REPOSITORY = "helmut-hoffer-von-ankershoffen/heinzel"
PUBLISHER_ID = 1826050
CONTEXT = "hz/main-batch"
PREFIX = "CI batch admission "
DELEGATED = "Main batch delegated to "
SHA = re.compile(r"[0-9a-f]{40}")
MIN_BATCH, MAX_BATCH, MEMBER_FIELDS, MAX_URL = 2, 5, 2, 1800


def proof_url(base: str, prs: list[list[int | str]], run_id: int) -> str:
    """Encode only public identities into a bounded, versioned proof URL."""
    payload = {"v": 1, "base": base, "prs": prs, "run": run_id}
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"https://github.com/{REPOSITORY}/actions/runs/{run_id}?batch={encoded}"


def _valid_members(prs: object) -> bool:
    if not isinstance(prs, list) or not MIN_BATCH <= len(prs) <= MAX_BATCH:
        return False
    if any(
        not isinstance(row, list)
        or len(row) != MEMBER_FIELDS
        or type(row[0]) is not int
        or row[0] <= 0
        or not isinstance(row[1], str)
        or not SHA.fullmatch(row[1])
        for row in prs
    ):
        return False
    return len({row[0] for row in prs}) == len(prs) and len({row[1] for row in prs}) == len(prs)


def _valid_payload(data: object, path: str) -> bool:
    return (
        isinstance(data, dict)
        and set(data) == {"v", "base", "prs", "run"}
        and type(data["v"]) is int
        and data["v"] == 1
        and type(data["run"]) is int
        and data["run"] > 0
        and path == f"/{REPOSITORY}/actions/runs/{data['run']}"
        and isinstance(data["base"], str)
        and bool(SHA.fullmatch(data["base"]))
        and _valid_members(data["prs"])
        and data["base"] not in {row[1] for row in data["prs"]}
    )


def decode(url: object) -> dict[str, Any] | None:
    """Return a canonical bounded public proof, or None for malformed input."""
    try:
        if not isinstance(url, str) or len(url) > MAX_URL:
            return None
        parts = urlsplit(url)
        query = parse_qs(parts.query, strict_parsing=True)
        if (
            parts.scheme != "https"
            or parts.netloc != "github.com"
            or parts.fragment
            or set(query) != {"batch"}
            or len(query["batch"]) != 1
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,1500}", query["batch"][0])
        ):
            return None
        encoded = query["batch"][0]
        data = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
        if _valid_payload(data, parts.path) and proof_url(data["base"], data["prs"], data["run"]) == url:
            return data
    except (ValueError, TypeError, KeyError):
        return None
    return None


def valid_run(run: dict[str, Any], proof: dict[str, Any]) -> bool:
    """Bind the exact first-attempt main push workflow without accepting reruns."""
    return (
        isinstance(run, dict)
        and type(run.get("id")) is int
        and run["id"] > 0
        and run["id"] == proof["run"]
        and type(run.get("run_attempt")) is int
        and run["run_attempt"] == 1
        and run.get("event") == "push"
        and run.get("head_branch") == "main"
        and run.get("head_sha") == proof["prs"][-1][1]
        and run.get("path") == ".github/workflows/ci.yml"
        and run.get("repository", {}).get("full_name") == REPOSITORY
        and run.get("status") in {"queued", "in_progress", "completed", "waiting", "pending", "requested"}
        and (
            (run.get("status") != "completed" and run.get("conclusion") is None)
            or (
                run.get("status") == "completed"
                and run.get("conclusion") in {"success", "failure", "timed_out", "startup_failure"}
            )
        )
    )


def _attested(status: dict[str, Any], url: str | None = None) -> bool:
    return (
        status.get("context") == CONTEXT
        and status.get("state") == "success"
        and type(status.get("creator", {}).get("id")) is int
        and status["creator"]["id"] == PUBLISHER_ID
        and (url is None or status.get("target_url") == url)
    )


def _valid_history(proof: dict[str, Any], get: Callable[[str], dict[str, Any]]) -> bool:
    final = proof["prs"][-1][1]
    compare = get(f"compare/{proof['base']}...{final}")
    commits = compare.get("commits", [])
    if (
        compare.get("status") != "ahead"
        or compare.get("total_commits") != len(proof["prs"])
        or [row.get("sha") for row in commits] != [row[1] for row in proof["prs"]]
    ):
        return False
    parent = proof["base"]
    for (number, commit), row in zip(proof["prs"], commits, strict=True):
        pr = get(f"pulls/{number}")
        if (
            [p.get("sha") for p in row.get("parents", [])] != [parent]
            or pr.get("merged") is not True
            or pr.get("merge_commit_sha") != commit
            or pr.get("base", {}).get("ref") != "main"
            or pr.get("base", {}).get("repo", {}).get("full_name") != REPOSITORY
        ):
            return False
        parent = commit
    return True


def _latest_status(sha: str, get: Callable[[str], Any]) -> dict[str, Any] | None:
    # The statuses endpoint includes the immutable numeric creator ID and is newest first.
    rows = get(f"commits/{sha}/statuses?per_page=100")
    if not isinstance(rows, list):
        return None
    return next((row for row in rows if str(row.get("context", "")).casefold() == CONTEXT), None)


def _valid_job_page(payload: object) -> bool:
    if not isinstance(payload, dict) or type(payload.get("total_count")) is not int:
        return False
    jobs = payload.get("jobs")
    if not isinstance(jobs, list) or payload["total_count"] != len(jobs):
        return False
    statuses = {"queued", "waiting", "in_progress", "pending", "requested", "completed"}
    conclusions = {
        "success",
        "failure",
        "neutral",
        "cancelled",
        "skipped",
        "timed_out",
        "action_required",
        "startup_failure",
        "stale",
    }
    return all(
        isinstance(row, dict)
        and isinstance(row.get("name"), str)
        and bool(row["name"].strip())
        and isinstance(row.get("status"), str)
        and row["status"] in statuses
        and (
            (
                row["status"] == "completed"
                and isinstance(row.get("conclusion"), str)
                and row["conclusion"] in conclusions
            )
            or (row["status"] != "completed" and row.get("conclusion") is None)
        )
        for row in jobs
    )


def proof_for(sha: str, get: Callable[[str], Any]) -> dict[str, Any] | None:
    """Validate the publisher, contiguous squash history and exact final run.

    The final run may accept its own proof before its marker exists. An intermediate
    delegates only after that marker proves the aggregate base was consumed.
    """
    status = _latest_status(sha, get)
    if not status or not _attested(status):
        return None
    proof = decode(status.get("target_url"))
    if not proof or sha not in [row[1] for row in proof["prs"]]:
        return None
    final = proof["prs"][-1][1]
    final_status = _latest_status(final, get)
    if (
        not final_status
        or not _attested(final_status, status["target_url"])
        or not valid_run(get(f"actions/runs/{proof['run']}"), proof)
        or not _valid_history(proof, get)
    ):
        return None
    if sha != final:
        jobs = get(f"actions/runs/{proof['run']}/attempts/1/jobs?per_page=100")
        if not _valid_job_page(jobs):
            return None
        if not any(
            row["name"] == f"Main batch full suite ({proof['base']})"
            and row["status"] == "completed"
            and row["conclusion"] == "success"
            for row in jobs["jobs"]
        ):
            return None
    return proof


def admit(
    sha: str,
    get: Callable[[str], Any],
    *,
    seconds: float = 180,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, str | bool | int]:
    """Poll boundedly; every error or missing proof preserves the normal full run."""
    deadline = clock() + seconds
    while True:
        try:
            proof = proof_for(sha, get)
            if proof:
                return {"full": sha == proof["prs"][-1][1], "base": proof["base"], "run": proof["run"]}
        except (ValueError, TypeError, KeyError, AttributeError, OSError, subprocess.SubprocessError):
            pass
        remaining = deadline - clock()
        if remaining <= 0:
            return {"full": True, "base": "", "run": ""}
        sleep(min(5, remaining))


def main() -> None:
    """Emit workflow outputs using a token with read permissions only."""
    result: dict[str, str | bool | int] = {"full": True, "base": "", "run": ""}
    if (
        os.environ.get("GITHUB_REPOSITORY") == REPOSITORY
        and os.environ.get("GITHUB_EVENT_NAME") == "push"
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
    ):
        deadline = time.monotonic() + 180

        def get(endpoint: str) -> dict[str, Any] | list[dict[str, Any]]:
            remaining = deadline - time.monotonic()
            # A subprocess timeout bounds even a hung remote request by the remaining admission budget.
            done = subprocess.run(  # noqa: S603 - fixed gh API invocation, no shell or arbitrary host
                ["gh", "api", f"repos/{REPOSITORY}/{endpoint}"],  # noqa: S607 - hosted runner supplies gh
                capture_output=True,
                text=True,
                check=True,
                timeout=max(0.001, min(10, remaining)),
            )
            return json.loads(done.stdout)

        result = admit(os.environ["GITHUB_SHA"], get, seconds=180)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as handle:
        for key, value in result.items():
            print(f"{key}={str(value).lower() if isinstance(value, bool) else value}", file=handle)


if __name__ == "__main__":
    main()
