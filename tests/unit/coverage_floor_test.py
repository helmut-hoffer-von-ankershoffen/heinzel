"""The coverage floor is a job of the pull request's own CI run, and its verdict says why it failed."""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = "  coverage:\n    steps:\n      - env:\n          MINIMUM_ORANGE: 80\n"
WORKFLOW += "        run: uv run --no-build coverage report --format=total --precision=2 --fail-under=90 > t.txt\n"
LISTING = (
    "src/heinzel/a.py      120     30     40      5    71%   3-9, 14\n"
    "src/heinzel/b.py       10      2      0      0    80%   7-8\n"
)


@pytest.fixture
def floor(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    from scripts import coverage_floor

    return coverage_floor


def _run(floor, tmp_path, total: str | None, result: str = "success", **extra: str) -> tuple[int, str, str, str]:
    (tmp_path / ".github/workflows").mkdir(parents=True, exist_ok=True)
    (tmp_path / floor.WORKFLOW).write_text(WORKFLOW, encoding="utf-8")
    env = {
        "COVERAGE_RESULT": result,
        "GITHUB_SHA": "a" * 40,
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "UNCOVERED": LISTING,
        "COVERAGE_TOTAL": str(tmp_path / "coverage-total.txt"),
        **extra,
    }
    if total is not None:
        (tmp_path / "coverage-total.txt").write_text(total, encoding="utf-8")
    code = floor.main(env, tmp_path)
    output = (tmp_path / "output").read_text(encoding="utf-8")
    summary = (tmp_path / "summary").read_text(encoding="utf-8")
    return code, output, summary, env["GITHUB_OUTPUT"]


def test_a_total_above_both_thresholds_passes_and_names_measured_required_and_scope(floor, tmp_path):
    code, output, summary, _ = _run(floor, tmp_path, "93.41\n")
    assert code == 0
    assert (
        output == "verdict=Coverage floor passed: whole-project coverage 93.41% "
        "(required 80% floor and 90% report threshold (--fail-under))\n"
    )
    assert "measured 93.41%, required 80% floor and 90% report threshold" in summary
    assert "Scope: the whole project, at the merge commit aaaaaaaaaaaa that this run tested." in summary
    assert "src/heinzel/a.py" in summary
    assert "3-9, 14" in summary


@pytest.mark.parametrize(
    "case",
    [
        ("79.9", "failure", "whole-project coverage 79.9% is below the 80% floor"),
        ("88.5", "failure", "whole-project coverage 88.5% is below the 90% report threshold (--fail-under)"),
        ("95", "failure", "the Coverage job ended with failure after measuring 95%"),
        (None, "failure", "the Coverage job ended with failure before it measured a total"),
        (None, "cancelled", "the Coverage job ended with cancelled before it measured a total"),
        ("", "failure", "the Coverage job ended with failure before it measured a total"),
    ],
)
def test_a_shortfall_fails_as_not_met_with_its_reason(floor, tmp_path, capsys, case):
    total, result, reason = case
    code, output, summary, _ = _run(floor, tmp_path, total, result)
    assert code == 1
    assert output.startswith(f"verdict=Coverage floor FAILED (not met): {reason}")
    assert "TOOL ERROR" not in output
    assert f"::error title=Coverage floor::Coverage floor FAILED (not met): {reason}" in capsys.readouterr().out
    assert summary.startswith("### Coverage floor FAILED")


@pytest.mark.parametrize(
    "case",
    [
        (None, "success", {}, "the Coverage job passed but left no coverage-total artifact"),
        ("", "success", {}, "the Coverage job passed but left no coverage-total artifact"),
        ("ninety", "success", {}, "not a percentage"),
        ("101", "success", {}, "not a percentage"),
        ("9" * 80, "success", {}, "not a percentage"),
        ("95", "success", {"FOCUSED_MODE": "pr-scoped"}, "this run selected focused CI"),
    ],
)
def test_a_judge_that_cannot_read_its_inputs_says_tool_error(floor, tmp_path, case):
    total, result, extra, reason = case
    code, output, _, _ = _run(floor, tmp_path, total, result, **extra)
    assert code == 2
    assert output.startswith("verdict=Coverage floor TOOL ERROR (the judge broke, coverage was not judged): ")
    assert reason in output
    assert "not met" not in output


def test_a_workflow_without_the_floor_is_a_tool_error_not_a_pass(floor, tmp_path):
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / floor.WORKFLOW).write_text("jobs: {}\n", encoding="utf-8")
    (tmp_path / "total").write_text("99", encoding="utf-8")
    env = {"COVERAGE_RESULT": "success", "COVERAGE_TOTAL": str(tmp_path / "total")}
    assert floor.main(env, tmp_path) == 2


def test_a_change_without_core_python_is_not_applicable_and_passes(floor, tmp_path):
    code, output, summary, _ = _run(floor, tmp_path, None, "skipped")
    assert code == 0
    assert output.startswith("verdict=Coverage floor: not applicable")
    assert "CI OK names the jobs this change requires" in summary


def test_the_floor_is_read_from_the_real_workflow(floor):
    workflow = (ROOT / floor.WORKFLOW).read_text(encoding="utf-8")
    assert floor.red_below(workflow) == 80
    assert floor.fail_under(workflow) == 90


def test_the_least_covered_files_are_bounded(floor):
    rows = floor.uncovered("\n".join(f"f{i}.py {'x' * 400}" for i in range(30)))
    assert len(rows) == floor.MAX_UNCOVERED
    assert all(len(row) <= 302 for row in rows)


def test_the_script_runs_isolated_with_the_standard_library_only(tmp_path):
    (tmp_path / "total").write_text("85", encoding="utf-8")
    env = {"COVERAGE_RESULT": "success", "COVERAGE_TOTAL": str(tmp_path / "total"), "PATH": "/usr/bin:/bin"}
    done = subprocess.run(
        [sys.executable, "-I", "scripts/coverage_floor.py"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert done.returncode == 1
    assert (
        "Coverage floor FAILED (not met): whole-project coverage 85% is below the 90% report threshold" in done.stdout
    )


def _jobs() -> dict:
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]


def test_the_floor_is_a_ci_job_that_ci_ok_requires_and_no_merge_gate_workflow_is_left():
    jobs = _jobs()
    job = jobs["coverage-floor"]
    assert job["name"] == "Coverage floor"
    assert job["needs"] == ["changes", "coverage"]
    assert "github.event_name == 'pull_request'" in job["if"]
    assert job["permissions"] == {"contents": "read"}
    assert [step["run"] for step in job["steps"] if "run" in step] == ["python3 -I scripts/coverage_floor.py"]
    assert "coverage-floor" in jobs["ci-ok"]["needs"]
    assert "sonar-ratings" in jobs["ci-ok"]["needs"]
    assert jobs["sonar-ratings"]["if"].startswith("github.event_name == 'pull_request'")
    assert [step["run"] for step in jobs["sonar-ratings"]["steps"] if "run" in step] == [
        "python3 -I scripts/sonar_ratings.py"
    ]
    full = next(
        step["run"] for step in jobs["ci-ok"]["steps"] if step["name"] == "Require the complete direct CI workload"
    )
    assert "required+=(coverage-floor)" in full.split("SURFACE_ONLY:-false", 1)[1]  # after the surface-only reset
    assert "required+=(sonar-ratings)" in full.split("SURFACE_ONLY:-false", 1)[1]
    assert not (ROOT / ".github/workflows/merge-gate.yml").exists()
    assert jobs["coverage"]["outputs"]["uncovered"] == "${{ steps.uncovered.outputs.files }}"


def test_ci_ok_names_each_failed_job_and_the_floor_verdict():
    step = _jobs()["ci-ok"]["steps"][0]
    assert step["env"]["NEEDS"] == "${{ toJSON(needs) }}"
    assert ".value.outputs.verdict" in step["run"]
    assert "::error::Required job" in step["run"]


def test_gate_review_covers_what_could_weaken_the_floor():
    """The judges run from the PR's own bytes, so Gate review must see every input that sets their bar."""
    import re

    review = (ROOT / ".github/workflows/claude-review.yml").read_text(encoding="utf-8")
    pattern = re.search(r"gate_paths='([^']+)'", review)
    assert pattern
    gate = re.compile(pattern[1])
    for path in (
        "Makefile",
        ".github/workflows/ci.yml",
        "scripts/coverage_floor.py",
        "scripts/sonar_ratings.py",
        "tests/unit/coverage_floor_test.py",
        "tests/unit/sonar_ratings_test.py",
    ):
        assert gate.search(path), path
    assert "for section in importlinter coverage pytest; do" in review
