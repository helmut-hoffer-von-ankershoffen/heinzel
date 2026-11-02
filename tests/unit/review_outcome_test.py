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

CREDIT = json.dumps(
    {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "Your credit balance is too low to access the Anthropic API.",
        },
    }
)
MODELS = json.dumps({"data": [{"id": "claude-sonnet-x"}, {"id": "claude-haiku-x"}]})


def execution_file(tmp_path: Path, text: str, *, cost: float = 0, is_error: bool = True) -> Path:
    path = tmp_path / "execution.json"
    result = {"type": "result", "subtype": "success", "is_error": is_error, "total_cost_usd": cost, "result": text}
    path.write_text(json.dumps([{"type": "system"}, result]), encoding="utf-8")
    return path


def fake_api(monkeypatch, answers: dict[str, tuple[int, str]]) -> list[tuple[str, str, dict | None]]:
    calls: list[tuple[str, str, dict | None]] = []

    def request(method, path, key, payload=None):
        calls.append((method, path, payload))
        return answers[method]

    monkeypatch.setattr(review_outcome, "request", request)
    return calls


# probe: before the review


def test_a_working_key_lets_the_review_run_after_one_token_on_the_cheapest_model(monkeypatch):
    calls = fake_api(monkeypatch, {"GET": (200, MODELS), "POST": (200, "{}")})
    assert review_outcome.probe({"ANTHROPIC_API_KEY": "k"})[0] is True
    assert calls[1][2]["model"] == "claude-haiku-x"
    assert calls[1][2]["max_tokens"] == 1


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (400, CREDIT),
        (429, json.dumps({"error": {"message": "Number of request tokens has exceeded your rate limit"}})),
        (529, json.dumps({"error": {"message": "Overloaded"}})),
        (400, json.dumps({"error": {"message": "You have reached your specified API usage limits."}})),
    ],
)
def test_an_account_limit_skips_the_review_with_its_reason(monkeypatch, status, body):
    fake_api(monkeypatch, {"GET": (200, MODELS), "POST": (status, body)})
    ready, reason = review_outcome.probe({"ANTHROPIC_API_KEY": "k"})
    assert ready is False
    assert f"(HTTP {status})" in reason


def test_a_bad_key_lets_the_review_run_so_it_fails_red_with_its_own_error(monkeypatch):
    fake_api(monkeypatch, {"GET": (401, json.dumps({"error": {"message": "invalid x-api-key"}}))})
    ready, reason = review_outcome.probe({"ANTHROPIC_API_KEY": "k"})
    assert ready is True
    assert "HTTP 401: invalid x-api-key" in reason


def test_an_unreachable_api_lets_the_review_run(monkeypatch):
    fake_api(monkeypatch, {"GET": (0, "timed out")})
    assert review_outcome.probe({"ANTHROPIC_API_KEY": "k"})[0] is True


def test_no_credentials_skip_and_a_subscription_token_is_not_probed(monkeypatch):
    calls = fake_api(monkeypatch, {})
    assert review_outcome.probe({}) == (False, "no ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN secret is set")
    assert review_outcome.probe({"CLAUDE_CODE_OAUTH_TOKEN": "t"})[0] is True
    assert calls == []


def test_probe_writes_the_step_outputs_and_a_notice(monkeypatch, tmp_path, capsys):
    fake_api(monkeypatch, {"GET": (200, MODELS), "POST": (400, CREDIT)})
    output, summary = tmp_path / "output", tmp_path / "summary"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert review_outcome.main(["", "probe"]) == 0
    lines = output.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "ready=false"
    assert lines[1] == "reason=Your credit balance is too low to access the Anthropic API. (HTTP 400)"
    assert capsys.readouterr().out.startswith("::notice::Claude review skipped: Your credit balance")
    assert "skipped" in summary.read_text(encoding="utf-8")


def test_an_api_error_text_cannot_start_a_workflow_command_or_a_new_output():
    ready, reason = review_outcome.verdict(429, json.dumps({"error": {"message": "x\n::error::y\nready=true"}}))
    assert ready is False
    assert "\n" not in reason
    assert "::" not in reason


# judge: after a failed review step


@pytest.mark.parametrize(
    "text",
    ["Credit balance is too low", "Claude AI usage limit reached", "API Error: 529 Overloaded"],
)
def test_a_review_that_could_not_start_fails_and_says_why(tmp_path, capsys, text):
    assert review_outcome.main(["", "judge", str(execution_file(tmp_path, text))]) == 1
    assert capsys.readouterr().out.startswith(
        f"::error::Claude review skipped, it could not start, nothing was reviewed: {text}"
    )


@pytest.mark.parametrize("text", ["Invalid API key · Fix external API key", "Reached maximum number of turns", ""])
def test_any_other_failed_review_fails_and_shows_its_text(tmp_path, capsys, text):
    assert review_outcome.main(["", "judge", str(execution_file(tmp_path, text))]) == 1
    assert capsys.readouterr().out.startswith("::error::Claude review failed, nothing was reviewed: ")


def test_a_limit_after_real_work_is_a_failure(tmp_path, capsys):
    path = execution_file(tmp_path, "API Error: 529 Overloaded", cost=0.4)
    assert review_outcome.main(["", "judge", str(path)]) == 1
    assert "::error::Claude review failed" in capsys.readouterr().out


def test_a_missing_or_broken_execution_file_fails(tmp_path, capsys):
    assert review_outcome.main(["", "judge", str(tmp_path / "absent.json")]) == 1
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert review_outcome.main(["", "judge", str(broken)]) == 1
    assert "no result message" in capsys.readouterr().out


def test_the_error_text_cannot_start_a_workflow_command(tmp_path, capsys):
    review_outcome.main(["", "judge", str(execution_file(tmp_path, "boom\n::set-output name=x::y"))])
    assert capsys.readouterr().out.count("::") == 2  # only the leading ::error::


def test_an_unknown_command_is_a_usage_error():
    assert review_outcome.main(["", "pass"]) == 2


# the workflow


def workflow() -> dict:
    return yaml.safe_load((REPO / ".github" / "workflows" / "claude-review.yml").read_text(encoding="utf-8"))


def test_the_review_runs_only_when_the_preflight_says_it_can():
    jobs = workflow()["jobs"]
    assert jobs["review"]["if"] == "needs.preflight.outputs.ready == 'true'"
    assert jobs["preflight"]["outputs"]["ready"] == "${{ steps.probe.outputs.ready }}"
    probe = next(step for step in jobs["preflight"]["steps"] if step.get("id") == "probe")
    assert probe["run"] == "uv run --script scripts/review_outcome.py probe"
    assert probe["env"]["ANTHROPIC_API_KEY"] == "${{ secrets.ANTHROPIC_API_KEY }}"


def test_a_skipped_review_is_reported_as_a_neutral_check_never_a_pass():
    jobs = workflow()["jobs"]
    report = jobs["preflight"]["steps"][-1]
    assert report["if"] == "steps.probe.outputs.ready == 'false'"
    assert "conclusion=neutral" in report["run"]
    assert '"output[title]=skipped: ' in report["run"]
    assert report["env"]["REASON"] == "${{ steps.probe.outputs.reason }}"  # through env, never into the script
    assert jobs["preflight"]["permissions"] == {"contents": "read", "checks": "write"}


def test_the_workflow_judges_every_failed_review_by_its_execution_file():
    """The action steps may not fail the job themselves, so the last step must always judge a failure of either."""
    steps = workflow()["jobs"]["review"]["steps"]
    actions = [step for step in steps if "claude-code-action" in step.get("uses", "")]
    assert {step["id"] for step in actions} == {"light", "full"}
    assert all(step["continue-on-error"] is True for step in actions)
    last = steps[-1]
    assert "steps.light.outcome == 'failure'" in last["if"]
    assert "steps.full.outcome == 'failure'" in last["if"]
    assert last["run"].strip() == (
        'uv run --script scripts/review_outcome.py judge "$RUNNER_TEMP/claude-execution-output.json"'
    )
