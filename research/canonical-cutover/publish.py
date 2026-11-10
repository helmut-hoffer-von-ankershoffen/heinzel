# ruff: noqa: INP001
"""One-time cutover writer. It uses native PR CI and leaves CI OK to that run."""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = "helmut-hoffer-von-ankershoffen/heinzel"
REF = "refs/heads/bootstrap/one-shot-canonical-focused-publish"
CI_WORKFLOW = 394704163
LEGACY_WORKFLOW = 403912077
LEGACY_RUN = 42805237969
# Independently authenticated in the original protected run's Report step log.
LEGACY_CHECKS = {"merge-gate": 129792615223, "coverage-floor": 129792620528}
ACTIONS_APP = 15368
REPOSITORY_ID = 1431672693
RULESET_ID = 25762292
REF_FIELDS = 2
CHECK_PAGE_LIMIT = 100
SHA = re.compile(r"[0-9a-f]{40}")
NUMBER = re.compile(r"[1-9][0-9]*")
COVERAGE = "Python coverage: not applicable to the reviewed scope"


def _require(condition: object, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def _command(*args: str) -> bytes:
    return subprocess.run(args, capture_output=True, check=True, timeout=30).stdout  # noqa: S603 - fixed git/gh commands and validated identities


def _api(endpoint: str, *args: str) -> Any:  # noqa: ANN401 - validate typed fields at the API boundary
    return json.loads(_command("gh", "api", f"repos/{REPO}/{endpoint}", *args))


def _identity() -> tuple[str, str]:
    head, base, tree = (os.environ[key] for key in ("PR_HEAD_SHA", "EXPECTED_BASE", "EXPECTED_TREE"))
    number, run_id = (os.environ[key] for key in ("PR_NUMBER", "CI_RUN_ID"))
    _require(all(SHA.fullmatch(value) for value in (head, base, tree)), "invalid pinned Git identity")
    _require(all(NUMBER.fullmatch(value) for value in (number, run_id)), "invalid pinned run or PR")
    helper = os.environ.get("APPROVED_HELPER", "")
    _require(
        SHA.fullmatch(helper)
        and os.environ.get("GITHUB_REF") == REF
        and os.environ.get("GITHUB_EVENT_NAME") == "push"
        and os.environ.get("GITHUB_REPOSITORY") == REPO
        and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
        and helper == os.environ.get("GITHUB_SHA") == os.environ.get("GITHUB_WORKFLOW_SHA"),
        "unexpected protected helper identity",
    )
    pr = _api(f"pulls/{number}")
    main = _api("git/ref/heads/main")["object"]["sha"]
    _require(
        pr["number"] == int(number)
        and pr["state"] == "open"
        and pr["draft"] is False
        and pr["head"]["sha"] == head
        and pr["head"]["repo"]["full_name"] == REPO
        and pr["base"]["sha"] == main == base
        and pr["base"]["ref"] == "main"
        and pr["base"]["repo"]["full_name"] == REPO,
        "cutover PR or main changed",
    )
    ref = f"refs/pull/{number}/merge"
    fields = _command("git", "ls-remote", "--exit-code", f"https://github.com/{REPO}.git", ref).decode().split()
    _require(
        len(fields) == REF_FIELDS and SHA.fullmatch(fields[0]) and fields[1] == ref, "missing exact GitHub merge ref"
    )
    merge = _api(f"commits/{fields[0]}")
    _require(
        merge["sha"] == fields[0]
        and merge["commit"]["tree"]["sha"] == tree
        and [item["sha"] for item in merge["parents"]] == [base, head],
        "cutover merge parents or reviewed tree changed",
    )
    run = _api(f"actions/runs/{run_id}")
    _require(
        run["id"] == int(run_id)
        and run["workflow_id"] == CI_WORKFLOW
        and run["path"] == ".github/workflows/ci.yml"
        and run["head_sha"] == head
        and run["event"] == "pull_request"
        and run["run_attempt"] == 1
        and run["status"] == "completed"
        and run["conclusion"] == "success"
        and run["repository"]["full_name"] == REPO
        and run["repository"]["id"] == REPOSITORY_ID,
        "actual native CI attempt changed",
    )
    latest = _api(f"actions/workflows/{CI_WORKFLOW}/runs?head_sha={head}&event=pull_request&per_page=1")[
        "workflow_runs"
    ]
    _require(len(latest) == 1 and latest[0]["id"] == int(run_id), "native CI attempt was superseded")
    return head, hashlib.sha256(f"{number}:{run_id}:{head}:{base}:{fields[0]}:{tree}".encode()).hexdigest()


def _check_identity(check: dict[str, Any], head: str, name: str, url: str, external: str) -> None:
    own_url = f"https://github.com/{REPO}/runs/{check['id']}"
    _require(
        check["name"] == name
        and check["head_sha"] == head
        and check["details_url"] in {url, own_url}
        and check["external_id"] == external
        and check["app"]["id"] == ACTIONS_APP,
        "new Check Run does not bind the exact Actions app and verdict source",
    )


def _start(head: str, name: str, url: str, external: str) -> int:
    check = _api(
        "check-runs",
        "--method",
        "POST",
        "-f",
        f"name={name}",
        "-f",
        f"head_sha={head}",
        "-f",
        "status=in_progress",
        "-f",
        f"details_url={url}",
        "-f",
        f"external_id={external}",
    )
    _check_identity(check, head, name, url, external)
    identifier = check["id"]
    _require(type(identifier) is int and identifier > 0 and check["status"] == "in_progress", "invalid new Check Run")
    return int(identifier)


def _finish(check_id: int, head: str, name: str, description: str, provenance: tuple[str, str]) -> None:
    url, external = provenance
    check = _api(
        f"check-runs/{check_id}",
        "--method",
        "PATCH",
        "-f",
        "status=completed",
        "-f",
        "conclusion=success",
        "-f",
        f"details_url={url}",
        "-f",
        f"external_id={external}",
        "-f",
        "output[title]=Canonical focused cutover",
        "-f",
        f"output[summary]={description}\nPublisher: {url}\nEvidence identity: {external}",
    )
    _check_identity(check, head, name, url, external)
    _require(
        check["id"] == check_id and check["status"] == "completed" and check["conclusion"] == "success",
        "genuine protected Check Run result could not be bound",
    )
    latest = _api(f"commits/{head}/check-runs?check_name={name}&filter=latest&per_page={CHECK_PAGE_LIMIT}")
    _require(len(latest["check_runs"]) == latest["total_count"] <= CHECK_PAGE_LIMIT, "latest Check Runs truncated")
    actions = [row for row in latest["check_runs"] if row["app"]["id"] == ACTIONS_APP]
    _require(len(actions) == 1 and actions[0]["id"] == check_id, "new typed Check Run is not the active Actions result")
    _check_identity(actions[0], head, name, url, external)
    _require(actions[0]["conclusion"] == "success", "new typed Check Run did not read back as success")


def _legacy_completed(head: str) -> None:
    for name, identifier in LEGACY_CHECKS.items():
        checks = _api(f"commits/{head}/check-runs?check_name={name}&per_page={CHECK_PAGE_LIMIT}")
        _require(len(checks["check_runs"]) == checks["total_count"] <= CHECK_PAGE_LIMIT, "legacy checks truncated")
        selected = [
            row
            for row in checks["check_runs"]
            if row["name"] == name and row["app"]["id"] == ACTIONS_APP and row["head_sha"] == head
        ]
        _require(
            len(selected) == 1
            and selected[0]["id"] == identifier
            and selected[0]["status"] == "completed"
            and selected[0]["details_url"] == f"https://github.com/{REPO}/runs/{identifier}",
            "wait for exact reviewed legacy checks to finish",
        )
        direct = _api(f"check-runs/{identifier}")
        _require(
            direct["id"] == identifier
            and direct["name"] == name
            and direct["head_sha"] == head
            and direct["app"]["id"] == ACTIONS_APP
            and direct["status"] == "completed"
            and direct["conclusion"] == ("failure" if name == "merge-gate" else "success")
            and direct["details_url"] == f"https://github.com/{REPO}/runs/{identifier}",
            "reviewed legacy verdict identity changed",
        )
    run = _api(f"actions/runs/{LEGACY_RUN}")
    _require(
        run["id"] == LEGACY_RUN
        and run["workflow_id"] == LEGACY_WORKFLOW
        and run["path"] == ".github/workflows/merge-gate.yml"
        and run["event"] == "workflow_run"
        and run["repository"]["id"] == REPOSITORY_ID
        and run["head_sha"] == os.environ["EXPECTED_BASE"]
        and run["head_branch"] == "main"
        and run["run_attempt"] == 1
        and run["status"] == "completed"
        and run["conclusion"] == "failure",
        "wait for the protected legacy workflow to finish",
    )
    latest = _api(
        f"actions/workflows/{LEGACY_WORKFLOW}/runs?event=workflow_run&head_sha={os.environ['EXPECTED_BASE']}&per_page=1"
    )["workflow_runs"]
    _require(len(latest) == 1 and latest[0]["id"] == LEGACY_RUN, "legacy workflow was superseded")


def _publish(head: str, source: str) -> None:
    _require(source == os.environ.get("APPROVED_SOURCE"), "read-only judge source changed")
    _require(os.environ.get("COVERAGE_VERDICT") == COVERAGE, "unexpected cutover coverage applicability")
    rules = _api("rules/branches/main")
    status_rules = [rule for rule in rules if rule["type"] == "required_status_checks"]
    _require(len(status_rules) == 1 and status_rules[0]["ruleset_id"] == RULESET_ID, "effective ruleset changed")
    parameters = status_rules[0]["parameters"]
    _require(parameters["strict_required_status_checks_policy"] is False, "base freshness mode changed")
    required = parameters["required_status_checks"]
    expected = {"CI OK": None, "merge-gate": ACTIONS_APP, "coverage-floor": ACTIONS_APP}
    _require(
        len(required) == len(expected) and {row["context"]: row.get("integration_id") for row in required} == expected,
        "required contexts or App bindings changed",
    )
    _legacy_completed(head)
    run = os.environ["GITHUB_RUN_ID"]
    _require(NUMBER.fullmatch(run), "invalid publisher run ID")
    url = f"https://github.com/{REPO}/actions/runs/{run}"
    verdicts = {
        "merge-gate": "Genuine exact-head and current-main Sonar gates and A ratings passed",
        "coverage-floor": "Python coverage not applicable to reviewed tooling scope. Full candidate coverage deferred.",
    }
    external = f"canonical-cutover-v1:{source}:{os.environ['CI_RUN_ID']}"
    check_ids = {}
    for name in verdicts:
        _require(_identity() == (head, source), "source changed before opening the cutover verdicts")
        check_ids[name] = _start(head, name, url, external)
    for name, description in verdicts.items():
        _require(_identity() == (head, source), "source changed before completing the cutover verdicts")
        _finish(check_ids[name], head, name, description, (url, external))
    _require(_identity() == (head, source), "source changed after reporting verdicts")


def main() -> None:
    """Check the cutover identity or publish both protected verdicts."""
    _require(sys.argv[1:] in (["check"], ["publish"]), "expected check or publish")
    head, source = _identity()
    if sys.argv[1] == "check":
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"source={source}\n")
    else:
        _publish(head, source)


if __name__ == "__main__":
    main()
