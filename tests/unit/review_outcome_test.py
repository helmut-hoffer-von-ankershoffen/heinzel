import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("review_outcome", REPO / "scripts" / "review_outcome.py")
assert spec
assert spec.loader
review_outcome = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review_outcome)

CREDIT = "Credit balance is too low"  # the text of every failed review since 2026-11-01 00:55 UTC


def execution_file(tmp_path: Path, text: str, *, cost: float = 0, is_error: bool = True) -> Path:
    path = tmp_path / "claude-execution-output.json"
    result = {"type": "result", "subtype": "success", "is_error": is_error, "total_cost_usd": cost, "result": text}
    path.write_text(json.dumps([{"type": "system"}, result]), encoding="utf-8")
    return path


def judge(tmp_path, monkeypatch, outcome: str, path: Path) -> dict[str, str]:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("RUNNER_TEMP", str(path.parent))
    assert review_outcome.main(["", outcome]) == 0
    return dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())


# the verdict


@pytest.mark.parametrize(
    "text",
    [CREDIT, "You've hit your limit · resets 3pm", "Claude AI usage limit reached", "API Error: 529 Overloaded"],
)
def test_a_limit_before_any_work_is_skipped_with_its_reason(tmp_path, monkeypatch, capsys, text):
    assert judge(tmp_path, monkeypatch, "failure", execution_file(tmp_path, text)) == {
        "verdict": "skipped",
        "reason": text,
    }
    assert capsys.readouterr().out.startswith(f"::notice::Claude review skipped: {text}")


@pytest.mark.parametrize("text", ["Invalid API key · Fix external API key", "Reached maximum number of turns"])
def test_any_other_error_fails_with_its_text(tmp_path, monkeypatch, text):
    assert judge(tmp_path, monkeypatch, "failure", execution_file(tmp_path, text)) == {
        "verdict": "failed",
        "reason": text,
    }


def test_a_limit_after_real_work_fails(tmp_path, monkeypatch):
    path = execution_file(tmp_path, "API Error: 529 Overloaded", cost=0.4)
    assert judge(tmp_path, monkeypatch, "failure", path)["verdict"] == "failed"


def test_a_missing_or_broken_execution_file_fails(tmp_path, monkeypatch):
    assert judge(tmp_path, monkeypatch, "failure", tmp_path / "absent" / "claude-execution-output.json") == {
        "verdict": "failed",
        "reason": "no result message",
    }
    broken = tmp_path / "broken" / "claude-execution-output.json"
    broken.parent.mkdir()
    broken.write_text("{", encoding="utf-8")
    assert judge(tmp_path, monkeypatch, "failure", broken)["verdict"] == "failed"


def test_only_a_successful_step_is_a_review(tmp_path, monkeypatch):
    absent = tmp_path / "absent" / "claude-execution-output.json"
    assert judge(tmp_path, monkeypatch, "success", absent)["verdict"] == "reviewed"
    assert judge(tmp_path, monkeypatch, "skipped", absent)["verdict"] == "none"


def test_the_error_text_cannot_start_a_workflow_command_or_a_new_output(tmp_path, monkeypatch, capsys):
    path = execution_file(tmp_path, "Credit balance\n::error::x\nverdict=reviewed")
    outputs = judge(tmp_path, monkeypatch, "failure", path)
    assert outputs["verdict"] == "skipped"
    assert "::" not in outputs["reason"]
    assert capsys.readouterr().out.count("::") == 2  # only the leading ::notice::


def test_the_summary_says_what_happened(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    judge(tmp_path, monkeypatch, "failure", execution_file(tmp_path, CREDIT))
    assert f"Claude review skipped: {CREDIT}" in summary.read_text(encoding="utf-8")


def test_a_wrong_call_is_a_usage_error():
    assert review_outcome.main(["", "failure", "extra"]) == 2


# the workflow


def workflow() -> dict:
    return yaml.safe_load((REPO / ".github" / "workflows" / "claude-review.yml").read_text(encoding="utf-8"))


def test_every_review_attempt_is_judged_by_its_execution_file():
    """The action steps may not fail the job themselves, so the verdict step judges whichever ran."""
    steps = workflow()["jobs"]["attempt"]["steps"]
    actions = [step for step in steps if "claude-code-action" in step.get("uses", "")]
    assert {step["id"] for step in actions} == {"light", "full"}
    assert all(step["continue-on-error"] is True for step in actions)
    judged = next(step for step in steps if step.get("id") == "verdict")
    assert "if" not in judged
    assert judged["env"]["OUTCOME"] == (
        "${{ steps.full.outcome != 'skipped' && steps.full.outcome || steps.light.outcome }}"
    )
    assert judged["run"] == ('uv run --script scripts/review_outcome.py "$OUTCOME"')


def test_a_skipped_review_is_a_neutral_check_with_its_reason():
    attempt = workflow()["jobs"]["attempt"]
    report = attempt["steps"][-1]
    assert report["if"] == "steps.verdict.outputs.verdict == 'skipped'"
    assert "conclusion=neutral" in report["run"]
    assert '"output[title]=skipped: ' in report["run"]
    assert report["env"]["REASON"] == "${{ steps.verdict.outputs.reason }}"  # through env, never into the script
    assert attempt["permissions"]["checks"] == "write"


def test_the_review_check_is_skipped_only_when_nothing_could_run():
    condition = workflow()["jobs"]["review"]["if"]
    assert "!cancelled()" in condition
    assert "needs.attempt.outputs.verdict != 'skipped'" in condition
    assert "needs.attempt.outputs.verdict != 'none'" in condition


@pytest.mark.parametrize(
    ("verdict", "attempt", "code", "shown"),
    [
        ("reviewed", "success", 0, "Claude reviewed this pull request"),
        ("failed", "success", 1, "::error::Claude review failed, nothing was reviewed: Invalid API key"),
        ("", "failure", 1, "::error::The review attempt ended without a verdict (job failure)"),
    ],
)
def test_the_review_check_passes_only_on_a_review(verdict, attempt, code, shown):
    script = workflow()["jobs"]["review"]["steps"][0]["run"]
    reason = "Claude reviewed this pull request" if verdict == "reviewed" else "Invalid API key"
    env = {"VERDICT": verdict, "REASON": reason, "ATTEMPT": attempt, "PATH": "/usr/bin:/bin"}
    done = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, check=False, timeout=10)
    assert done.returncode == code
    assert done.stdout.startswith(shown)
