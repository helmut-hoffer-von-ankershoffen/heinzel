"""The merge gate judges from public APIs, waits with a deadline and fails closed."""

import importlib.util
import io
import json
import sys
import urllib.error
import zipfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("merge_gate", ROOT / "scripts" / "merge_gate.py")
assert spec
assert spec.loader
merge_gate = importlib.util.module_from_spec(spec)
sys.modules["merge_gate"] = merge_gate
spec.loader.exec_module(merge_gate)

pytestmark = pytest.mark.unit

REPO = "owner/thing"
HEAD = "a" * 40
GOOD = {"reliability_rating": "1.0", "security_rating": "1.0", "sqale_rating": "1.0"}


class World:
    """Answers the gate's URLs from what a test says is true, and keeps its own clock."""

    def __init__(self) -> None:
        self.gate_status = {"": "OK", "pr": "OK"}
        self.ratings = {"": dict(GOOD), "pr": dict(GOOD)}
        self.pull_sha = HEAD
        self.main_sha = "b" * 40
        self.sonar_main_sha = self.main_sha
        self.down = False
        self.job: dict[str, Any] | None = {
            "name": "Coverage",
            "status": "completed",
            "conclusion": "success",
            "details_url": f"https://github.com/{REPO}/actions/runs/77/job/1",
        }
        self.total = "85.50"
        self.badge = "92%"
        self.now = 0.0
        self.artifacts = 1

    def read(self, url: str, _headers: dict | None = None) -> Any:  # noqa: PLR0911 - one answer per endpoint
        if self.down:
            message = "service is down"
            raise merge_gate.UnavailableError(message)
        scope = "pr" if "pullRequest=" in url else ""
        if "project_status" in url:
            conditions = [
                {"status": "ERROR", "metricKey": "new_security_rating", "actualValue": "5", "errorThreshold": "1"}
            ]
            ok = self.gate_status[scope] == "OK"
            return {"projectStatus": {"status": self.gate_status[scope], "conditions": [] if ok else conditions}}
        if "measures/component" in url:
            return {"component": {"measures": [{"metric": k, "value": v} for k, v in self.ratings[scope].items()]}}
        if "project_pull_requests" in url:
            return {"pullRequests": [{"key": "7", "commit": {"sha": self.pull_sha}}]}
        if "project_branches" in url:
            return {"branches": [{"isMain": True, "commit": {"sha": self.sonar_main_sha}}]}
        if url.endswith("commits/main"):
            return {"sha": self.main_sha}
        if "check-runs" in url:
            return {"check_runs": [self.job] if self.job else []}
        if "/artifacts" in url:
            return {"artifacts": [{"archive_download_url": "https://api/zip"}] * self.artifacts}
        if "endpoint.json" in url:
            return {"message": self.badge}
        raise AssertionError(url)

    def zip(self, _url: str, _headers: dict | None = None) -> bytes:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("coverage-total.txt", self.total)
        return out.getvalue()

    def gate(self, pr: str = "7") -> Any:
        return merge_gate.Gate(
            REPO,
            pr,
            HEAD if pr else "",
            "token",
            read=self.read,
            read_bytes=self.zip,
            sleep=self.tick,
            clock=lambda: self.now,
        )

    def tick(self, seconds: float) -> None:
        self.now += seconds


def test_passes_when_pull_request_and_project_are_clean():
    assert World().gate().run_sonar() == []


def test_project_only_mode_does_not_need_a_pull_request():
    world = World()
    world.pull_sha = "stale"
    assert world.gate(pr="").run_sonar() == []


@pytest.mark.parametrize(("scope", "words"), [("pr", "pull request #7"), ("", "project")])
@pytest.mark.parametrize(
    ("metric", "label"),
    [("reliability_rating", "reliability"), ("security_rating", "security"), ("sqale_rating", "maintainability")],
)
def test_a_rating_below_a_fails_for_either_analysis(scope, words, metric, label):
    world = World()
    world.ratings[scope][metric] = "2.0"
    assert world.gate().run_sonar() == [f"{words}: {label} is B, needs A"]


def test_a_failed_quality_gate_names_the_failing_condition():
    world = World()
    world.gate_status[""] = "ERROR"
    problems = world.gate().run_sonar()
    assert problems == ["project: quality gate is ERROR: new_security_rating=5 (limit 1)"]


def test_a_rating_sonar_did_not_report_fails():
    world = World()
    del world.ratings["pr"]["security_rating"]
    assert world.gate().run_sonar() == ["pull request #7: security is not reported, needs A"]


def test_it_waits_until_sonar_has_analysed_this_exact_head():
    world = World()
    world.pull_sha = "old"
    original = world.tick

    def analysis_lands(seconds: float) -> None:
        original(seconds)
        if world.now >= 120:
            world.pull_sha = HEAD

    gate = world.gate()
    gate.sleep = analysis_lands
    assert gate.run_sonar() == []
    assert world.now >= 120


def test_a_stale_analysis_fails_closed_at_the_deadline():
    world = World()
    world.pull_sha = "old"
    with pytest.raises(merge_gate.UnavailableError, match="gave up waiting 30 min"):
        world.gate().run_sonar()
    assert world.now >= merge_gate.SONAR_WAIT


def test_main_not_yet_analysed_at_its_latest_commit_fails_closed():
    world = World()
    world.sonar_main_sha = "old"
    with pytest.raises(merge_gate.UnavailableError, match="main's latest commit"):
        world.gate().run_sonar()


def test_an_outage_fails_closed_with_a_clear_message(monkeypatch, capsys):
    world = World()
    world.down = True
    gate = world.gate()
    monkeypatch.setattr(merge_gate, "Gate", lambda *_, **__: gate)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    assert merge_gate.main(["sonar"]) == 1
    assert "Failing closed, no verdict" in capsys.readouterr().out


def test_coverage_at_or_above_the_floor_passes_below_fails():
    world = World()
    world.total = "80.00"
    assert world.gate().run_coverage("Coverage", 80.0) == []
    world.total = "79.99"
    assert "79.99% is below 80.0%" in world.gate().run_coverage("Coverage", 80.0)[0]


def test_coverage_waits_for_the_job_then_fails_closed():
    world = World()
    world.job = None
    with pytest.raises(merge_gate.UnavailableError, match="the Coverage job"):
        world.gate().run_coverage("Coverage", 80.0)
    assert world.now >= merge_gate.COVERAGE_WAIT


def test_a_failed_coverage_job_fails_the_gate():
    world = World()
    world.job = {**world.job, "conclusion": "failure", "html_url": "https://x"}
    assert "ended with failure" in world.gate().run_coverage("Coverage", 80.0)[0]


def test_a_skipped_measurement_is_judged_by_mains_badge():
    world = World()
    world.job = {**world.job, "conclusion": "skipped"}
    assert world.gate().run_coverage("Coverage", 80.0) == []
    world.badge = "63%"
    assert "63.0% is below" in world.gate().run_coverage("Coverage", 80.0)[0]


@pytest.mark.parametrize("text", ["101", "fine", "85; rm -rf /", "", "-1"])
def test_an_artifact_that_is_not_a_percentage_is_refused(text):
    world = World()
    world.total = text
    with pytest.raises(merge_gate.UnavailableError):
        world.gate().run_coverage("Coverage", 80.0)


def test_a_missing_artifact_fails_closed():
    world = World()
    world.artifacts = 0
    with pytest.raises(merge_gate.UnavailableError, match="no coverage-total artifact"):
        world.gate().run_coverage("Coverage", 80.0)


def test_the_floor_is_the_badges_own_threshold():
    assert merge_gate.red_below("x:\n  MINIMUM_GREEN: 90\n  MINIMUM_ORANGE: 80\n") == 80.0
    with pytest.raises(merge_gate.UnavailableError, match="MINIMUM_ORANGE"):
        merge_gate.red_below("MINIMUM_GREEN: 90\n")


def test_the_floor_in_this_repository_is_found():
    assert merge_gate.red_below((ROOT / ".github/workflows/ci.yml").read_text()) >= 80


def test_a_redirect_drops_the_token(monkeypatch):
    seen: list[tuple[str, str | None]] = []

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_: object):
            return False

    class Opener:
        def open(self, request, timeout):
            seen.append((request.full_url, request.get_header("Authorization")))
            if len(seen) == 1:
                raise urllib.error.HTTPError(
                    request.full_url, 302, "", {"Location": "https://storage/zip"}, io.BytesIO()
                )
            return Reply(json.dumps({"ok": 1}).encode())

    monkeypatch.setattr(merge_gate.urllib.request, "build_opener", lambda *_: Opener())
    assert merge_gate.fetch("https://api/zip", {"Authorization": "Bearer t"}) == b'{"ok": 1}'
    assert seen == [("https://api/zip", "Bearer t"), ("https://storage/zip", None)]


def test_the_workflow_never_checks_out_the_pull_request():
    text = (ROOT / ".github/workflows/merge-gate.yml").read_text()
    assert "pull_request_target" in text
    assert "ref:" not in text
    assert "secrets." not in text
