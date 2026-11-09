"""The merge gate judges from public APIs, waits with a deadline and fails closed."""

import hashlib
import importlib.util
import io
import json
import os
import sys
import urllib.error
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

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
        self.queue_until = 0.0  # while now is below this, another check at this head waits for a runner

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
            waiting = [{"name": "Tests", "status": "queued"}] if self.now < self.queue_until else []
            return {"check_runs": ([self.job] if self.job else []) + waiting}
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
    gate = world.gate()
    with pytest.raises(merge_gate.UnavailableError, match="gave up waiting 30 min"):
        gate.run_sonar()
    assert world.now >= merge_gate.SONAR_WAIT


def test_main_not_yet_analysed_at_its_latest_commit_fails_closed():
    world = World()
    world.sonar_main_sha = "old"
    gate = world.gate()
    with pytest.raises(merge_gate.UnavailableError, match="main's latest commit"):
        gate.run_sonar()


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
    gate = world.gate()
    with pytest.raises(merge_gate.UnavailableError, match="the Coverage job"):
        gate.run_coverage("Coverage", 80.0)
    assert world.now >= merge_gate.COVERAGE_WAIT


def test_runner_queue_time_does_not_count_against_the_coverage_wait():
    world = World()
    ready = merge_gate.COVERAGE_WAIT * 2
    world.queue_until = ready
    world.job = None
    gate = world.gate()
    real = gate.read

    def read(url: str, headers: dict | None = None) -> Any:
        if world.now >= ready:
            world.job = {**World().job}
        return real(url, headers)

    gate.read = read
    assert gate.run_coverage("Coverage", 80.0) == []
    assert world.now >= ready


def test_a_queue_that_never_clears_still_fails_closed():
    world = World()
    world.queue_until = float("inf")
    world.job = None
    with pytest.raises(merge_gate.UnavailableError, match="gave up waiting"):
        world.gate().run_coverage("Coverage", 80.0)
    assert world.now >= merge_gate.QUEUE_CAP


def test_queue_time_does_not_count_against_sonars_wait():
    world = World()
    world.pull_sha = "old"
    world.queue_until = merge_gate.SONAR_WAIT * 2
    gate = world.gate()

    def analysis_lands(seconds: float) -> None:
        world.tick(seconds)
        if world.now >= merge_gate.SONAR_WAIT * 2:
            world.pull_sha = HEAD

    gate.sleep = analysis_lands
    assert gate.run_sonar() == []


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
    gate = world.gate()
    with pytest.raises(merge_gate.UnavailableError):
        gate.run_coverage("Coverage", 80.0)


def test_a_missing_artifact_fails_closed():
    world = World()
    world.artifacts = 0
    gate = world.gate()
    with pytest.raises(merge_gate.UnavailableError, match="no coverage-total artifact"):
        gate.run_coverage("Coverage", 80.0)


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
    assert set(yaml.safe_load(text)[True]) == {"workflow_run"}  # YAML reads the key `on` as True
    assert "ref:" not in text
    assert "secrets." not in text


def test_the_gates_start_after_ci_and_report_the_required_check_names():
    """No gate holds a runner while CI runs, and the ruleset's merge-gate and coverage-floor still get reported."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/merge-gate.yml").read_text())
    trigger = workflow[True]["workflow_run"]  # YAML reads the key `on` as True
    assert trigger == {"workflows": ["CI"], "types": ["completed"]}
    job = workflow["jobs"]["gates"]
    assert job["permissions"]["checks"] == "write"
    assert "contents" not in job["permissions"] or job["permissions"]["contents"] == "read"
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert "for check in merge-gate coverage-floor" in runs
    finish = job["steps"][-1]
    assert finish["if"].startswith("always()")
    assert "conclusion=success || conclusion=failure" in finish["run"]


def test_focused_coverage_holds_without_reading_the_main_badge(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert merge_gate.main(["coverage", "--focused"]) == 1


@pytest.fixture
def focused_caller(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import focused_ci_collect

    base = "b" * 40
    repository = {"id": 123, "full_name": REPO}
    event = {
        "action": "completed",
        "repository": repository,
        "workflow_run": {
            "id": 51,
            "run_attempt": 2,
            "repository": repository,
            "head_repository": repository,
            "head_sha": HEAD,
            "event": "pull_request",
            "status": "completed",
            "conclusion": "success",
            "pull_requests": [{"number": 7, "base": {"ref": "main", "sha": base}, "head": {"sha": HEAD}}],
        },
    }
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event))
    manifest = "[source]\nworkload_app_id = 11\n"
    digest = hashlib.sha256(manifest.encode()).hexdigest()
    for name, value in {
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_TOKEN": "fixture",
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_run",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": base,
        "GITHUB_WORKFLOW_REF": f"{REPO}/.github/workflows/merge-gate.yml@refs/heads/main",
        "GITHUB_EVENT_PATH": str(event_path),
        "FOCUSED_EVIDENCE_ADMIN_MANIFEST": manifest,
        "FOCUSED_EVIDENCE_ADMIN_SHA256": digest,
    }.items():
        monkeypatch.setenv(name, value)
    return focused_ci_collect, base, manifest, digest


def test_focused_caller_ignores_untrusted_policy_and_attempt_environment(monkeypatch, focused_caller):
    collector, base, manifest, digest = focused_caller
    for name, value in {
        "PR_NUMBER": "99",
        "PR_HEAD_SHA": "c" * 40,
        "FOCUSED_CI_RUN_ID": "88",
        "FOCUSED_CI_RUN_ATTEMPT": "9",
        "FOCUSED_CI_POLICY_PATH": "/untrusted/manifest",
        "FOCUSED_CI_POLICY_SHA256": "a" * 64,
        "FOCUSED_CI_WORKLOAD_APP_ID": "666",
    }.items():
        monkeypatch.setenv(name, value)

    def git(*args):
        if args == ("rev-parse", "HEAD"):
            return base.encode()
        assert args == (
            "fetch",
            "--no-tags",
            "--no-recurse-submodules",
            "origin",
            "refs/pull/7/head",
            "refs/pull/7/merge",
        )
        return b""

    def collect(_read, path, trigger, *, policy_sha256):
        assert trigger == collector.Trigger(7, HEAD, 51, 2)
        assert path.read_text() == manifest
        assert policy_sha256 == digest
        return SimpleNamespace(run=SimpleNamespace(base=base, pr=7, head=HEAD, run_id=51, attempt=2))

    monkeypatch.setattr(collector, "git_bytes", git)
    monkeypatch.setattr(collector, "collect", collect)
    assert merge_gate.main(["focused"]) == 0


@pytest.mark.parametrize(
    ("override", "message"),
    [
        (("GITHUB_REF", "refs/pull/7/merge"), "workflow_run on main"),
        (("GITHUB_EVENT_NAME", "workflow_dispatch"), "workflow_run on main"),
        (
            ("GITHUB_WORKFLOW_REF", f"{REPO}/.github/workflows/decoy.yml@refs/heads/main"),
            "protected merge-gate workflow",
        ),
        (("FOCUSED_EVIDENCE_ADMIN_SHA256", "c" * 64), "does not match its protected digest"),
    ],
)
def test_focused_caller_rejects_untrusted_ref_or_pin_before_git_or_api(
    monkeypatch, capsys, focused_caller, override, message
):
    collector, _, _, digest = focused_caller
    monkeypatch.setenv("FOCUSED_CI_POLICY_SHA256", digest)
    monkeypatch.setenv(*override)

    def unexpected_read(*_):
        pytest.fail("the invalid caller must fail before Git or API reads")

    monkeypatch.setattr(merge_gate.Gate, "github", unexpected_read)
    monkeypatch.setattr(collector, "git_bytes", unexpected_read)
    assert merge_gate.main(["focused"]) == 1
    assert message in capsys.readouterr().out


def test_focused_caller_rejects_a_pr_checkout_before_fetch(monkeypatch, capsys, focused_caller):
    collector, _, _, _ = focused_caller

    def git(*args):
        assert args == ("rev-parse", "HEAD")
        return HEAD.encode()

    monkeypatch.setattr(collector, "git_bytes", git)
    assert merge_gate.main(["focused"]) == 1
    assert "checkout is not the protected event SHA" in capsys.readouterr().out


def test_focused_caller_rejects_a_run_from_a_different_head_repository(monkeypatch, capsys, focused_caller):
    collector, _, _, _ = focused_caller
    event_file = Path(os.environ["GITHUB_EVENT_PATH"])
    event = json.loads(event_file.read_bytes())
    event["workflow_run"]["head_repository"]["id"] = 999
    event_file.write_text(json.dumps(event))

    def unexpected_git(*_):
        pytest.fail("an untrusted workload source must fail before fetching Git objects")

    monkeypatch.setattr(collector, "git_bytes", unexpected_git)
    assert merge_gate.main(["focused"]) == 1
    assert "does not belong to a pull request on this protected base" in capsys.readouterr().out


def test_focused_workflow_is_uncalled_and_read_only():
    path = ".github/workflows/focused-ci-evidence.yml"
    workflow = yaml.safe_load((ROOT / path).read_text())
    assert workflow[True] == {"workflow_call": None}
    assert workflow["permissions"] == {}
    job = workflow["jobs"]["collect"]
    assert "merge-gate.yml@refs/heads/main" in job["if"]
    assert job["environment"] == "focused-evidence"
    assert set(job["permissions"].values()) == {"read"}
    checkout, collect = job["steps"]
    assert checkout["with"] == {"ref": "${{ github.sha }}", "persist-credentials": False}
    for name in ("FOCUSED_EVIDENCE_ADMIN_MANIFEST", "FOCUSED_EVIDENCE_ADMIN_SHA256"):
        assert collect["env"][name] == "${{ secrets." + name + " }}"
    assert all(
        path not in other.read_text()
        for other in (ROOT / ".github/workflows").glob("*.yml")
        if other.name != Path(path).name
    )
