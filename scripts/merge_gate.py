# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""The merge gate: `merge_gate.py sonar` and `merge_gate.py coverage`, one required check each.

Rule: nothing merges unless SonarCloud's quality gate passes and overall reliability, security and maintainability are
rated A, for both the pull request's analysis and the project; and nothing merges at a coverage where the badge is red.

Runs from .github/workflows/merge-gate.yml on `pull_request_target`, so a pull request can't change the gate that judges
it: this file comes from the base branch and no pull request code is run. What it reads from the pull request's CI is
a number, parsed strictly. Standard library only. Waits with a deadline, then fails closed: no answer is never a pass.

Environment: GITHUB_REPOSITORY, GITHUB_TOKEN, PR_NUMBER and PR_HEAD_SHA (absent: the project only), COVERAGE_CHECK (the
name of the CI job that measures coverage), CI_WORKFLOW (default .github/workflows/ci.yml).
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

SONAR = "https://sonarcloud.io/api"
GITHUB = "https://api.github.com"
RATINGS = {"reliability_rating": "reliability", "security_rating": "security", "sqale_rating": "maintainability"}
LETTERS = {"1.0": "A", "2.0": "B", "3.0": "C", "4.0": "D", "5.0": "E"}
SONAR_WAIT = 1800  # seconds; analysis normally takes minutes
COVERAGE_WAIT = 2700  # seconds; the CI job has to get a runner first
POLL = 30
ARTIFACT = "coverage-total"
MAX_ARTIFACT = 64 * 1024
TOTAL = re.compile(r"\d{1,3}(?:\.\d+)?")
REDIRECTS = (301, 302, 303, 307, 308)
MAX_PERCENT = 100
RUN_URL = re.compile(r"/actions/runs/(\d+)")


class UnavailableError(Exception):
    """A service did not answer usably. Retried until the deadline, then the gate fails closed."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Hands a redirect back to `fetch`, which decides what the next request carries."""

    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        """Follow nothing."""


def fetch(url: str, headers: dict[str, str] | None = None) -> bytes:
    """The body at url. A redirect is followed without the headers, because artifact storage refuses our token.

    Raises:
        UnavailableError: The service did not answer usably.
    """
    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(url, headers=headers or {})  # noqa: S310 - fixed https API bases above
    try:
        with opener.open(request, timeout=30) as reply:
            return bytes(reply.read())
    except urllib.error.HTTPError as error:
        location = error.headers.get("Location") if error.code in REDIRECTS else None
        error.close()
        if location and location.startswith("https://"):
            return fetch(location)
        message = f"{url} answered {error.code}"
        raise UnavailableError(message) from error
    except OSError as error:  # URLError and TimeoutError are OSErrors
        message = f"{url} did not answer: {error}"
        raise UnavailableError(message) from error


def fetch_json(url: str, headers: dict[str, str] | None = None) -> Any:  # noqa: ANN401 - whatever the API returns
    """The JSON at url.

    Raises:
        UnavailableError: The service did not answer usably.
    """
    try:
        return json.loads(fetch(url, headers))
    except ValueError as error:
        message = f"{url} did not return JSON"
        raise UnavailableError(message) from error


@dataclass
class Gate:
    """One run: what it judges, and how it reads the world (replaced in tests)."""

    repo: str
    pr: str = ""
    head: str = ""
    token: str = ""
    read: Callable[..., Any] = fetch_json
    read_bytes: Callable[..., bytes] = fetch
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic

    @property
    def project(self) -> str:
        """The Sonar project key: the repository with an underscore."""
        return self.repo.replace("/", "_")

    def sonar(self, path: str, **params: str) -> Any:  # noqa: ANN401 - whatever the API returns
        """A public SonarCloud API answer."""
        return self.read(f"{SONAR}/{path}?{urllib.parse.urlencode(params)}")

    def github(self, path: str) -> Any:  # noqa: ANN401 - whatever the API returns
        """A GitHub API answer for this repository."""
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"}
        return self.read(f"{GITHUB}/repos/{self.repo}/{path}", headers)

    def wait(self, what: str, seconds: float, step: Callable[[], Any]) -> Any:  # noqa: ANN401 - what step returns
        """step() until it returns something other than None.

        Raises:
            UnavailableError: The deadline passed.
        """
        deadline = self.clock() + seconds
        last = ""
        while True:
            try:
                answer = step()
                if answer is not None:
                    return answer
                last = "not there yet"
            except UnavailableError as error:
                last = str(error)
            if self.clock() >= deadline:
                message = f"gave up waiting {int(seconds / 60)} min for {what}: {last}"
                raise UnavailableError(message)
            print(f"waiting for {what}: {last}", flush=True)
            self.sleep(POLL)

    # Sonar

    def pull_analysed(self) -> bool | None:
        """True once Sonar has analysed this exact head."""
        pulls = self.sonar("project_pull_requests/list", project=self.project).get("pullRequests", [])
        match = next((p for p in pulls if p.get("key") == self.pr), None)
        return True if match and (match.get("commit") or {}).get("sha") == self.head else None

    def main_analysed(self) -> bool | None:
        """True once Sonar has analysed main's latest commit."""
        head = self.github("commits/main").get("sha")
        branches = self.sonar("project_branches/list", project=self.project).get("branches", [])
        main = next((b for b in branches if b.get("isMain")), None)
        return True if head and main and (main.get("commit") or {}).get("sha") == head else None

    def judge(self, scope: str, **where: str) -> list[str]:
        """What is wrong with one analysis, in words. Empty: quality gate OK and all three ratings A."""
        status = self.sonar("qualitygates/project_status", projectKey=self.project, **where).get("projectStatus", {})
        measures = (
            self.sonar("measures/component", component=self.project, metricKeys=",".join(RATINGS), **where)
            .get("component", {})
            .get("measures", [])
        )
        values = {m.get("metric"): m.get("value") for m in measures}
        problems = []
        if status.get("status") != "OK":
            bad = [
                f"{c.get('metricKey')}={c.get('actualValue')} (limit {c.get('errorThreshold')})"
                for c in status.get("conditions", [])
                if c.get("status") == "ERROR"
            ]
            problems.append(
                f"{scope}: quality gate is {status.get('status') or 'unknown'}" + (f": {', '.join(bad)}" if bad else "")
            )
        for metric, label in RATINGS.items():
            value = values.get(metric)
            if value != "1.0":
                problems.append(f"{scope}: {label} is {LETTERS.get(value or '', 'not reported')}, needs A")
        return problems

    def run_sonar(self) -> list[str]:
        """Everything wrong with the pull request's analysis and the project's."""
        problems = []
        if self.pr:
            self.wait(f"Sonar's analysis of #{self.pr} at {self.head[:8]}", SONAR_WAIT, self.pull_analysed)
            problems += self.wait(
                "Sonar's verdict", 300, lambda: self.judge(f"pull request #{self.pr}", pullRequest=self.pr)
            )
        self.wait("Sonar's analysis of main's latest commit", SONAR_WAIT, self.main_analysed)
        problems += self.wait("Sonar's verdict", 300, lambda: self.judge("project"))
        return problems

    # Coverage

    def coverage_job(self, name: str) -> dict[str, Any] | None:
        """The latest completed run of the job at this head; None while it hasn't finished."""
        runs = self.github(f"commits/{self.head}/check-runs?per_page=100&check_name={urllib.parse.quote(name)}")
        found = sorted(runs.get("check_runs", []), key=lambda r: r.get("started_at") or "", reverse=True)
        return found[0] if found and found[0].get("status") == "completed" else None

    def measured(self, job: dict[str, Any]) -> float:
        """The total the pull request's own CI measured.

        Raises:
            UnavailableError: The total can't be read.
        """
        match = RUN_URL.search(job.get("details_url") or "")
        if not match:
            message = f"can't find the CI run of {job.get('name')}"
            raise UnavailableError(message)
        artifacts = self.github(f"actions/runs/{match[1]}/artifacts?name={ARTIFACT}").get("artifacts", [])
        if not artifacts:
            message = f"the CI run has no {ARTIFACT} artifact"
            raise UnavailableError(message)
        headers = {"Authorization": f"Bearer {self.token}"}
        data = self.read_bytes(artifacts[0]["archive_download_url"], headers)
        if len(data) > MAX_ARTIFACT:
            message = f"the {ARTIFACT} artifact is too large"
            raise UnavailableError(message)
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                text = archive.read(f"{ARTIFACT}.txt").decode().strip()
        except (zipfile.BadZipFile, KeyError, UnicodeDecodeError) as error:
            message = f"can't read the {ARTIFACT} artifact: {error}"
            raise UnavailableError(message) from error
        if not TOTAL.fullmatch(text) or float(text) > MAX_PERCENT:
            message = f"the {ARTIFACT} artifact is not a percentage: {text[:40]!r}"
            raise UnavailableError(message)
        return float(text)

    def badge_total(self) -> float:
        """Main's published total, for a pull request whose CI skipped the measurement because no code changed.

        Raises:
            UnavailableError: The badge has no number.
        """
        url = f"https://raw.githubusercontent.com/{self.repo}/python-coverage-comment-action-data/endpoint.json"
        match = re.fullmatch(r"(\d{1,3})(?:\.\d+)?%", str(self.read(url).get("message")))
        if not match:
            message = "main's coverage badge has no percentage"
            raise UnavailableError(message)
        return float(match[1])

    def run_coverage(self, check: str, floor: float) -> list[str]:
        """What is wrong with the coverage at this head."""
        job = self.wait(f"the {check} job at {self.head[:8]}", COVERAGE_WAIT, lambda: self.coverage_job(check))
        conclusion = job.get("conclusion")
        if conclusion == "skipped":
            total, source = (
                self.wait("main's coverage badge", 300, self.badge_total),
                "main's badge; CI measured nothing here",
            )
        elif conclusion == "success":
            total, source = self.wait("the measured total", 300, lambda: self.measured(job)), "measured at this head"
        else:
            return [f"the {check} job ended with {conclusion}, so there is no coverage to judge: {job.get('html_url')}"]
        print(f"coverage {total}% ({source}), red below {floor}%", flush=True)
        return [] if total >= floor else [f"coverage {total}% is below {floor}%, where the badge turns red ({source})"]


def red_below(workflow: str) -> float:
    """The badge's own threshold: its MINIMUM_ORANGE, under which it is red. Read, so the two can't drift.

    Raises:
        UnavailableError: The workflow doesn't set it.
    """
    found = re.findall(r"^\s*MINIMUM_ORANGE:\s*[\"']?(\d+(?:\.\d+)?)[\"']?\s*$", workflow, re.MULTILINE)
    if not found:
        message = "no MINIMUM_ORANGE in the CI workflow: can't tell where the badge turns red"
        raise UnavailableError(message)
    return max(float(value) for value in found)


def finish(problems: list[str], passed: str) -> int:
    """Print the verdict; the exit code."""
    for problem in problems:
        print(f"::error::{problem}")
    if not problems:
        print(passed)
    return 1 if problems else 0


def main(argv: list[str]) -> int:
    """Run one gate; the exit code."""
    mode = argv[0] if argv else ""
    if mode not in ("sonar", "coverage"):
        print("usage: merge_gate.py sonar|coverage", file=sys.stderr)
        return 2
    env = os.environ
    gate = Gate(
        env["GITHUB_REPOSITORY"], env.get("PR_NUMBER", ""), env.get("PR_HEAD_SHA", ""), env.get("GITHUB_TOKEN", "")
    )
    try:
        if mode == "sonar":
            return finish(gate.run_sonar(), "Sonar: quality gate passes and all ratings are A.")
        if not gate.head:
            print("::error::the coverage gate judges a pull request; none given")
            return 1
        workflow = Path(env.get("CI_WORKFLOW", ".github/workflows/ci.yml")).read_text()
        return finish(gate.run_coverage(env["COVERAGE_CHECK"], red_below(workflow)), "Coverage is above red.")
    except UnavailableError as error:
        print(f"::error::Failing closed, no verdict: {error}. Rerun this job when the service is back.")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
