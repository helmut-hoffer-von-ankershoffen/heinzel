"""Sonar's overall-code ratings, judged inside CI: `python3 -I scripts/sonar_ratings.py`.

The `Sonar ratings` job of .github/workflows/ci.yml runs this and `CI OK` requires it on pull requests. It reads the
project's overall reliability, security and maintainability ratings from SonarCloud's public API and fails unless all
three are A. The `SonarCloud Code Analysis` check judges the pull request's new code; this keeps the bar on the whole
project, so a legacy regression, a rule update or a reopened issue stops the next merge. No answer is never a pass:
an unreachable or unreadable API fails as TOOL ERROR after a few tries.

Environment: GITHUB_REPOSITORY (the Sonar project is the repository with an underscore), GITHUB_OUTPUT and
GITHUB_STEP_SUMMARY. Standard library only.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

NAME = "Sonar ratings"
API = "https://sonarcloud.io/api/measures/component"
RATINGS = {"reliability_rating": "reliability", "security_rating": "security", "sqale_rating": "maintainability"}
LETTERS = {"1.0": "A", "2.0": "B", "3.0": "C", "4.0": "D", "5.0": "E"}
TRIES = 4
PAUSE = 15  # seconds between tries
MAX_BODY = 64 * 1024


class RatingError(ValueError):
    """The ratings were read and one is below A: a real verdict."""


class ToolError(RuntimeError):
    """SonarCloud gave no usable answer: no verdict was reached."""


def fetch(url: str) -> Any:  # noqa: ANN401 - whatever the API returns, checked by the caller
    """The JSON at url.

    Raises:
        ToolError: The API did not answer usably.
    """
    try:
        with urllib.request.urlopen(url, timeout=30) as reply:  # noqa: S310 - the fixed https API above
            body = reply.read(MAX_BODY + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        message = f"SonarCloud did not answer: {error}"
        raise ToolError(message) from error
    if len(body) > MAX_BODY:
        message = "SonarCloud's answer is oversized"
        raise ToolError(message)
    try:
        return json.loads(body)
    except ValueError as error:
        message = "SonarCloud did not return JSON"
        raise ToolError(message) from error


def ratings(project: str, read: Callable[[str], Any] = fetch) -> dict[str, str]:
    """The project's overall rating values, metric -> value (missing metrics are absent).

    Raises:
        ToolError: The answer has no measures for this project.
    """
    query = urllib.parse.urlencode({"component": project, "metricKeys": ",".join(RATINGS)})
    answer = read(f"{API}?{query}")
    component = answer.get("component") if isinstance(answer, dict) else None
    if not isinstance(component, dict) or component.get("key") != project:
        message = f"SonarCloud has no project {project}"
        raise ToolError(message)
    measures = component.get("measures")
    if not isinstance(measures, list):
        message = f"SonarCloud reports no measures for {project}"
        raise ToolError(message)
    return {m["metric"]: str(m.get("value")) for m in measures if isinstance(m, dict) and m.get("metric") in RATINGS}


def judge(project: str, values: Mapping[str, str], notes: list[str]) -> str:
    """The passing verdict, or why a rating is not A.

    Raises:
        RatingError: A rating is below A or not reported.
    """
    letters = {label: LETTERS.get(values.get(metric, ""), "not reported") for metric, label in RATINGS.items()}
    notes.append(f"Scope: overall code of the SonarCloud project {project} (main's latest analysis).")
    notes.append("Measured: " + ", ".join(f"{label} {letter}" for label, letter in letters.items()) + "; required A.")
    low = [f"{label} is {letter}" for label, letter in letters.items() if letter != "A"]
    if low:
        message = ", ".join(low) + ", needs A (see the project's issues on SonarCloud)"
        raise RatingError(message)
    return f"{NAME} passed: reliability, security and maintainability are A"


def publish(env: Mapping[str, str], headline: str, notes: list[str], *, failed: bool) -> None:
    """Print the verdict, annotate a failure, and hand it to CI OK and the job summary."""
    print("\n".join([headline, *notes]))
    if failed:
        print(f"::error title={NAME}::{headline}")
    if path := env.get("GITHUB_OUTPUT"):
        with Path(path).open("a", encoding="utf-8") as output:
            output.write(f"verdict={headline[:1000].replace(chr(10), ' ')}\n")
    if path := env.get("GITHUB_STEP_SUMMARY"):
        with Path(path).open("a", encoding="utf-8") as summary:
            summary.write(f"### {headline}\n\n" + "\n".join(f"- {note}" for note in notes) + "\n")


def read_ratings(project: str, read: Callable[[str], Any], sleep: Callable[[float], None]) -> dict[str, str]:
    """The project's ratings, tried TRIES times before giving up.

    Raises:
        ToolError: No project is named, or the last try had no usable answer.
    """
    if not project:
        message = "GITHUB_REPOSITORY is not set"
        raise ToolError(message)
    for attempt in range(1, TRIES):
        try:
            return ratings(project, read)
        except ToolError as error:
            print(f"try {attempt}/{TRIES}: {error}; again in {PAUSE}s", flush=True)
            sleep(PAUSE)
    return ratings(project, read)


def main(
    env: Mapping[str, str] = os.environ,
    read: Callable[[str], Any] = fetch,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Judge, publish and return the exit code: 0 passed, 1 below A, 2 tool error."""
    notes: list[str] = []
    project = env.get("GITHUB_REPOSITORY", "").replace("/", "_")
    try:
        verdict = judge(project, read_ratings(project, read, sleep), notes)
    except RatingError as error:
        publish(env, f"{NAME} FAILED (not met): {error}", notes, failed=True)
        return 1
    except (ToolError, KeyError, TypeError) as error:
        headline = f"{NAME} TOOL ERROR (no answer, the ratings were not judged): {type(error).__name__}: {error}"
        publish(env, headline, notes, failed=True)
        return 2
    publish(env, verdict, notes, failed=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
