import importlib.util
import json
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


def execution_file(tmp_path: Path, text: str, *, cost: float = 0, is_error: bool = True) -> Path:
    path = tmp_path / "execution.json"
    result = {"type": "result", "subtype": "success", "is_error": is_error, "total_cost_usd": cost, "result": text}
    path.write_text(json.dumps([{"type": "system"}, result]), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "text",
    [
        "Credit balance is too low",
        "You've hit your limit · resets 3pm",
        "Claude AI usage limit reached",
        "API Error: 429 rate_limit_error",
        "API Error: 529 Overloaded",
    ],
)
def test_an_external_limit_before_any_work_skips_with_a_warning(tmp_path, capsys, text):
    assert review_outcome.main(["", str(execution_file(tmp_path, text))]) == 0
    assert capsys.readouterr().out.startswith(f"::warning::Claude review skipped, nothing was reviewed: {text}")


@pytest.mark.parametrize("text", ["Invalid API key · Fix external API key", "Reached maximum number of turns", ""])
def test_any_other_error_stays_red_and_shows_its_text(tmp_path, capsys, text):
    assert review_outcome.main(["", str(execution_file(tmp_path, text))]) == 1
    assert capsys.readouterr().out.startswith("::error::Claude review failed: ")


def test_a_limit_after_real_work_stays_red(tmp_path):
    assert review_outcome.main(["", str(execution_file(tmp_path, "API Error: 529 Overloaded", cost=0.4))]) == 1


def test_a_missing_or_broken_execution_file_stays_red(tmp_path, capsys):
    assert review_outcome.main(["", str(tmp_path / "absent.json")]) == 1
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert review_outcome.main(["", str(broken)]) == 1
    assert "no result message" in capsys.readouterr().out


def test_the_error_text_cannot_start_a_workflow_command(tmp_path, capsys):
    review_outcome.main(["", str(execution_file(tmp_path, "boom\n::set-output name=x::y"))])
    assert capsys.readouterr().out.count("::") == 2  # only the leading ::error::


def test_the_summary_says_what_happened(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    review_outcome.main(["", str(execution_file(tmp_path, "Credit balance is too low"))])
    assert "skipped, nothing was reviewed: Credit balance is too low" in summary.read_text(encoding="utf-8")


def test_the_workflow_judges_every_failed_review_by_its_execution_file():
    """The action steps may not fail the job themselves, so the last step must always judge a failure of either."""
    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "claude-review.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["review"]["steps"]
    actions = [step for step in steps if "claude-code-action" in step.get("uses", "")]
    assert {step["id"] for step in actions} == {"light", "full"}
    assert all(step["continue-on-error"] is True for step in actions)
    last = steps[-1]
    assert "steps.light.outcome == 'failure'" in last["if"]
    assert "steps.full.outcome == 'failure'" in last["if"]
    assert (
        last["run"].strip() == 'uv run --script scripts/review_outcome.py "$RUNNER_TEMP/claude-execution-output.json"'
    )
