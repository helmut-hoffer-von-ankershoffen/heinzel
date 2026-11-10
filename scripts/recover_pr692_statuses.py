# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Rejudge the two protected gates for the pinned PR #692 recovery."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import re
import sys
import urllib.request
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

REPO = "helmut-hoffer-von-ankershoffen/heinzel"
API = f"https://api.github.com/repos/{REPO}"
WEB = f"https://github.com/{REPO}"
REF = "refs/heads/repair/checks-status-recovery"
WORKFLOW = ".github/workflows/checks-status-recovery.yml"
CI = ".github/workflows/ci.yml"
PR = 692
RULESET = 25762292
BASE = "432006f27ce2df73a80f2f69a4e162378df2c862"
HEAD = "2b2c5dc3e9c7156e0a2a3e4497b109bf99e52add"
HEAD_REF = "repair/3qj-post-593"
MERGE = "7ec73f9c51f063bbc5b04772102927efa9932f46"
TREE = "7e4469c10fbf8b12821cb66784ad01cfaac61409"
RUN = 42767677534
ATTEMPT = 2
SUITE = 115826527542
CI_OK_JOB = 129674241526
COVERAGE_JOB = 129668190311
ARTIFACT = 13739904749
ARTIFACT_SIZE = 158
ARTIFACT_DIGEST = "b95e80e74204e4259b7c05a7c2f0821c38cea3c4a40426c39593c5be51da736e"
ARTIFACT_URL = f"{API}/actions/artifacts/{ARTIFACT}/zip"
PUBLIC_QUEUE_URL = f"{API}/commits/{HEAD}/check-runs?per_page=100&filter=latest"
EXPECTED_SOURCES = {"CI OK": None, "merge-gate": 15368, "coverage-floor": 15368}
PINS = {
    "scripts/merge_gate.py": "81c590244558107fdc385439c44e9b2ebe20601ff930e9ee810dcd883fa4fcab",
    CI: "2ab124821108a4c6795627d33e70e871b1ee93e5f6f5ff8b57e331c98e1779ef",
}
LOGGER = logging.getLogger(__name__)

type Get = Callable[[str], Any]
type Post = Callable[[dict[str, str]], None]


class RecoveryError(ValueError):
    """The exact incident identity or recovery authority could not be verified."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RecoveryError(message)


def _pull_identity(pull: dict[str, Any]) -> bool:
    return (
        pull["number"] == PR
        and pull["base"]["ref"] == "main"
        and pull["base"]["sha"] == BASE
        and pull["head"]["ref"] == HEAD_REF
        and pull["head"]["sha"] == HEAD
    )


def _check_target(get: Get) -> None:
    pull = get(f"pulls/{PR}")
    _require(
        _pull_identity(pull)
        and pull["state"] == "open"
        and pull["draft"] is False
        and pull["mergeable"] is True
        and pull["mergeable_state"] in ("blocked", "clean")
        and pull["merge_commit_sha"] == MERGE
        and pull["head"]["repo"]["full_name"] == pull["base"]["repo"]["full_name"] == REPO,
        "PR #692 or its test-merge identity moved",
    )
    for ref, sha in (("main", BASE), (HEAD_REF, HEAD)):
        current = get(f"git/ref/heads/{ref}")
        _require(
            current["ref"] == f"refs/heads/{ref}"
            and current["object"]["type"] == "commit"
            and current["object"]["sha"] == sha,
            "A branch ref moved",
        )
    head = get(f"git/commits/{HEAD}")
    _require(head["sha"] == HEAD and head["tree"]["sha"] == TREE, "The PR head tree changed")
    merge = get(f"git/commits/{MERGE}")
    _require(
        merge["sha"] == MERGE
        and [parent["sha"] for parent in merge["parents"]] == [BASE, HEAD]
        and merge["tree"]["sha"] == TREE,
        "The test-merge parents or tree changed",
    )


def _check_rules(get: Get) -> None:
    rules = [rule for rule in get("rules/branches/main") if rule["type"] == "required_status_checks"]
    _require(
        len(rules) == 1 and rules[0]["ruleset_id"] == RULESET and rules[0]["ruleset_source"] == REPO,
        "The effective required-check ruleset changed",
    )
    settings = rules[0]["parameters"]
    contexts = settings["required_status_checks"]
    _require(
        len(contexts) == len(EXPECTED_SOURCES)
        and {row["context"]: row.get("integration_id") for row in contexts} == EXPECTED_SOURCES
        and settings["strict_required_status_checks_policy"] is False,
        "The required contexts or publisher bindings changed",
    )


def _check_run(run: dict[str, Any]) -> None:
    _require(
        run["id"] == RUN
        and run["run_attempt"] == ATTEMPT
        and run["check_suite_id"] == SUITE
        and run["event"] == "pull_request"
        and run["path"] == CI
        and run["head_sha"] == HEAD
        and run["head_branch"] == HEAD_REF
        and run["display_title"] == f"CI {MERGE}"
        and run["status"] == "completed"
        and run["conclusion"] == "success"
        and run["repository"]["full_name"] == run["head_repository"]["full_name"] == REPO
        and len(run["pull_requests"]) == 1
        and _pull_identity(run["pull_requests"][0]),
        "The exact successful PR CI attempt or suite changed",
    )


def _exact_job(get: Get, job_id: int, attempt: int, name: str) -> dict[str, Any]:
    jobs = get(f"actions/runs/{RUN}/attempts/{attempt}/jobs?per_page=100")
    matches = [job for job in jobs["jobs"] if job["name"] == name]
    _require(jobs["total_count"] == len(jobs["jobs"]) and len(matches) == 1, f"Missing or ambiguous {name} evidence")
    job = matches[0]
    _require(
        job["id"] == job_id
        and job["run_id"] == RUN
        and job["run_attempt"] == attempt
        and job["head_sha"] == HEAD
        and job["status"] == "completed"
        and job["conclusion"] == "success",
        f"The pinned {name} job did not pass on the expected attempt",
    )
    return job


def _check_artifact(get: Get) -> None:
    artifact = get(f"actions/artifacts/{ARTIFACT}")
    source = artifact["workflow_run"]
    _require(
        artifact["id"] == ARTIFACT
        and artifact["name"] == "coverage-total"
        and artifact["expired"] is False
        and artifact["size_in_bytes"] == ARTIFACT_SIZE
        and artifact["digest"] == f"sha256:{ARTIFACT_DIGEST}"
        and source["id"] == RUN
        and source["head_sha"] == HEAD
        and source["head_branch"] == HEAD_REF,
        "The attempt-one Coverage artifact expired or changed",
    )


def _verify_current(get: Get) -> None:
    _check_target(get)
    _check_rules(get)
    _check_run(get(f"actions/runs/{RUN}"))


def _judge(command: Callable[[], list[str]]) -> str:
    try:
        problems = command()
    except (OSError, ValueError, KeyError, TypeError) as error:
        LOGGER.error(  # noqa: TRY400 - keep service exception details out of the workflow log
            "ci.pr692_recovery.gate_error", extra={"outcome": type(error).__name__}
        )
        return "error"
    for problem in problems:
        LOGGER.error("ci.pr692_recovery.gate_failure", extra={"outcome": problem})
    return "failure" if problems else "success"


def _status(context: str, state: str, run_url: str) -> dict[str, str]:
    return {
        "context": context,
        "state": state,
        "description": f"Protected gate {state} for PR #692",
        "target_url": run_url,
    }


def _invalidate(post: Post, contexts: list[str], run_url: str) -> None:
    # Statuses are append-only. Retract this run's possible successes on the same immutable head.
    for context in contexts:
        try:
            post(_status(context, "error", run_url))
        except (OSError, ValueError, KeyError, TypeError):
            LOGGER.error("ci.pr692_recovery.invalidation_failed", extra={"outcome": context})  # noqa: TRY400


def recover(
    get: Get,
    post: Post,
    sonar: Callable[[], list[str]],
    coverage: Callable[[dict[str, Any]], list[str]],
    run_url: str,
) -> bool:
    """Publish only the genuine gate outcomes for the exact original PR head.

    Raises:
        RecoveryError: A publication prerequisite changed.
    """
    LOGGER.info("ci.pr692_recovery.start")
    outcome = "error"
    started: list[str] = []
    try:
        _verify_current(get)
        _check_run(get(f"actions/runs/{RUN}/attempts/{ATTEMPT}"))
        _exact_job(get, CI_OK_JOB, ATTEMPT, "CI OK")
        coverage_job = _exact_job(get, COVERAGE_JOB, 1, "Coverage")
        _check_artifact(get)
        commands = {"merge-gate": sonar, "coverage-floor": lambda: coverage(coverage_job)}

        for context in commands:
            _verify_current(get)
            started.append(context)
            post(_status(context, "pending", run_url))
        verdicts = {context: _judge(command) for context, command in commands.items()}
        for context, state in verdicts.items():
            _verify_current(get)
            post(_status(context, state, run_url))
        _verify_current(get)
        outcome = "ok" if all(state == "success" for state in verdicts.values()) else "failed_gates"
    except Exception:
        _invalidate(post, started, run_url)
        raise
    else:
        return outcome == "ok"
    finally:
        LOGGER.info("ci.pr692_recovery.finish", extra={"outcome": outcome})


def _load_gate(root: Path) -> ModuleType:
    for name, digest in PINS.items():
        _require(hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, "Pinned main source differs")
    spec = importlib.util.spec_from_file_location("protected_merge_gate", root / "scripts/merge_gate.py")
    if spec is None or spec.loader is None:
        message = "Could not load the pinned main judge"
        raise RecoveryError(message)
    gate = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = gate
    spec.loader.exec_module(gate)
    return gate


def _assert_host() -> None:
    commit = os.environ.get("RECOVERY_SHA", "")
    _require(
        bool(re.fullmatch(r"[0-9a-f]{40}", commit))
        and commit == os.environ.get("GITHUB_SHA") == os.environ.get("GITHUB_WORKFLOW_SHA")
        and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("GITHUB_REPOSITORY") == REPO
        and os.environ.get("GITHUB_EVENT_NAME") == "push"
        and os.environ.get("GITHUB_REF") == REF
        and os.environ.get("GITHUB_WORKFLOW_REF") == f"{REPO}/{WORKFLOW}@{REF}"
        and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
        and os.environ.get("GITHUB_ACTOR") == REPO.partition("/")[0],
        "The workflow is outside the approved one-shot GitHub Actions push",
    )


def _no_wait(_seconds: float) -> None:
    message = "Protected gate evidence is unavailable for this recovery run"
    raise RecoveryError(message)


def _coverage(gate: ModuleType, token: str, floor: float, job: dict[str, Any]) -> list[str]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    archive = gate.fetch(ARTIFACT_URL, headers)
    _require(
        len(archive) == ARTIFACT_SIZE and hashlib.sha256(archive).hexdigest() == ARTIFACT_DIGEST,
        "Coverage archive size or digest differs",
    )
    responses = {
        f"{API}/commits/{HEAD}/check-runs?per_page=100&check_name=Coverage": {
            "check_runs": [{**job, "details_url": f"{WEB}/actions/runs/{RUN}/job/{COVERAGE_JOB}"}],
        },
        f"{API}/actions/runs/{RUN}/artifacts?name=coverage-total": {
            "artifacts": [{"archive_download_url": ARTIFACT_URL}],
        },
    }

    def read(url: str, _headers: dict[str, str]) -> Any:  # noqa: ANN401 - protected judge consumes GitHub JSON
        if url == PUBLIC_QUEUE_URL:
            return gate.fetch_json(url)
        return responses[url]

    judge = gate.Gate(
        REPO,
        "692",
        HEAD,
        token,
        read=read,
        read_bytes=lambda url, _: {ARTIFACT_URL: archive}[url],
        sleep=_no_wait,
    )
    return judge.run_coverage("Coverage", floor)


def _post_status(gate: ModuleType, token: str, status: dict[str, str]) -> None:
    request = urllib.request.Request(  # noqa: S310 - fixed HTTPS GitHub endpoint for the exact PR head
        f"{API}/statuses/{HEAD}",
        data=json.dumps(status).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.build_opener(gate.NoRedirect()).open(request, timeout=15) as response:
        result = json.load(response)
        _require(
            response.status == HTTPStatus.CREATED
            and result["context"] == status["context"]
            and result["state"] == status["state"]
            and result["creator"]["login"] == "github-actions[bot]",
            "GitHub did not confirm the expected Actions status",
        )


def main(root: Path) -> int:
    """Run the reviewed recovery workflow against its sole authorized target."""
    _assert_host()
    gate = _load_gate(root.resolve())
    token = os.environ["GITHUB_TOKEN"]
    run_id = os.environ["GITHUB_RUN_ID"]
    _require(bool(re.fullmatch(r"[1-9][0-9]*", run_id)), "Invalid recovery run ID")

    def read(url: str, headers: dict[str, str] | None = None) -> Any:  # noqa: ANN401 - judge consumes service JSON
        return gate.fetch_json(url, None if url == PUBLIC_QUEUE_URL else headers)

    judge = gate.Gate(REPO, "692", HEAD, token, read=read, sleep=_no_wait)
    floor = gate.red_below((root / CI).read_text())

    def run_sonar() -> list[str]:
        problems = judge.run_sonar()
        _require(judge.pull_analysed() and judge.main_analysed(), "Sonar analysis changed during judgment")
        return problems

    def fail_closed(command: Callable[[], list[str]]) -> list[str]:
        try:
            return command()
        except gate.UnavailableError as error:
            message = "Protected gate evidence could not be read"
            raise RecoveryError(message) from error

    passed = recover(
        judge.github,
        lambda status: _post_status(gate, token, status),
        lambda: fail_closed(run_sonar),
        lambda job: fail_closed(lambda: _coverage(gate, token, floor, job)),
        f"{WEB}/actions/runs/{run_id}",
    )
    return 0 if passed else 1


if __name__ == "__main__":
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s %(outcome)s", defaults={"outcome": "start"}))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    sys.exit(main(Path(sys.argv[1])))
