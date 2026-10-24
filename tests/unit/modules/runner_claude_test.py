"""The Claude Code agent runner with a recorded fake instead of the `claude` CLI; no test runs a real agent."""

import json
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from heinzel import Budget, HeinzelError, ToolProfile
from heinzel.modules.system.runner._claude import ClaudeCodeRunner, parse_events
from heinzel.modules.system.runner._models import RunnerSettings

pytestmark = pytest.mark.unit

EVENTS = [
    {"type": "system", "subtype": "init", "model": "claude-sonnet-test"},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result"}]}},
    {
        "type": "assistant",
        "message": {
            "content": [{"type": "text"}, {"type": "tool_use", "name": "Grep"}, {"type": "tool_use", "name": "Read"}]
        },
    },
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "The weekly digest.",
        "num_turns": 3,
        "total_cost_usd": 0.0123,
        "usage": {
            "input_tokens": 1200,
            "output_tokens": 300,
            "cache_read_input_tokens": 50,
            "cache_creation_input_tokens": 7,
        },
    },
]


def stream(events: list[object]) -> str:
    return "\n".join(json.dumps(event) for event in events) + "\nnot json\n"


class FakeRun:
    """Records the call and answers like `claude -p --output-format stream-json`."""

    def __init__(self, stdout: str = stream(EVENTS), returncode: int = 0, raises: Exception | None = None) -> None:
        self.stdout, self.returncode, self.raises = stdout, returncode, raises
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, kwargs))
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(command, self.returncode, self.stdout, "boom")


def runner(fake: FakeRun, **settings: object) -> ClaudeCodeRunner:
    return ClaudeCodeRunner(RunnerSettings(**settings), run=fake, which=lambda name: f"/bin/{name}")


def run(target: ClaudeCodeRunner, **overrides: object):
    arguments: dict[str, object] = {
        "contract": "Only summarize.",
        "tools": ToolProfile(working_dir=Path("/work")),
        "timeout": timedelta(minutes=5),
        "budget": Budget(max_usd=0.5),
    } | overrides
    return target.run("Summarize the week.", **arguments)  # type: ignore[arg-type]


def test_runs_claude_headless_with_a_read_only_profile_and_the_prompt_on_stdin(monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_GITHUB_API_TOKEN", "secret")
    monkeypatch.setenv("KEEP_ME", "1")
    fake = FakeRun()
    run(runner(fake, model="sonnet"))
    [(command, kwargs)] = fake.calls
    assert command == [
        "/bin/claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--tools",
        "Read,Grep,Glob",
        "--allowed-tools",
        "Read,Grep,Glob",
        "--permission-mode",
        "dontAsk",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--append-system-prompt",
        "Only summarize.",
        "--model",
        "sonnet",
        "--max-budget-usd",
        "0.5",
    ]
    assert "Summarize the week." not in command
    assert kwargs["input"] == "Summarize the week."
    assert kwargs["timeout"] == 300
    assert kwargs["cwd"] == Path("/work")
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert "HZ_CREDENTIAL_GITHUB_API_TOKEN" not in env
    assert env["KEEP_ME"] == "1"


def test_parses_draft_tool_calls_turns_and_cost():
    result = run(runner(FakeRun()))
    assert result.status == "completed"
    assert result.runner == "claude-code"
    assert result.model == "claude-sonnet-test"
    assert result.draft == "The weekly digest."
    assert result.tool_calls == {"Read": 2, "Grep": 1}
    assert result.turns == 3
    assert result.usage.model_dump() == {
        "input_tokens": 1200,
        "output_tokens": 300,
        "cache_read_tokens": 50,
        "cache_write_tokens": 7,
        "cost_usd": 0.0123,
    }


def test_leaves_out_what_is_not_given():
    fake = FakeRun()
    run(runner(fake), contract="", budget=Budget())
    command = fake.calls[0][0]
    assert "--append-system-prompt" not in command
    assert "--max-budget-usd" not in command
    assert "--model" not in command


def test_a_timeout_stops_the_run_and_is_reported_not_raised():
    partial = stream(EVENTS[:2]).encode()
    result = run(runner(FakeRun(raises=subprocess.TimeoutExpired(["claude"], 300, output=partial))))
    assert result.status == "timeout"
    assert result.draft == ""
    assert result.tool_calls == {"Read": 1}
    assert "0:05:00" in result.detail


def test_a_run_over_budget_is_reported():
    events = [{"type": "result", "subtype": "error_max_budget_usd", "is_error": True, "total_cost_usd": 0.51}]
    result = run(runner(FakeRun(stdout=stream(events), returncode=1)))
    assert result.status == "budget"
    assert result.usage.cost_usd == 0.51
    assert result.draft == ""


def test_a_failing_cli_is_a_failed_result():
    result = run(runner(FakeRun(stdout=stream(EVENTS), returncode=2)))
    assert result.status == "failed"
    assert result.detail == "exit code 2: boom"
    assert result.draft == ""


def test_an_error_result_is_failed():
    events = [{"type": "result", "subtype": "error_during_execution", "is_error": True}]
    result = parse_events(stream(events).splitlines(), 1.0)
    assert result.status == "failed"
    assert result.detail == "Claude Code ended with error_during_execution"


def test_output_without_a_result_is_failed():
    result = parse_events(["[]", json.dumps({"type": "assistant", "message": "odd"}), ""], 0.1)
    assert result.status == "failed"
    assert result.detail == "Claude Code printed no result"


def test_odd_usage_values_are_unknown():
    events = [{"type": "result", "subtype": "success", "result": "ok", "usage": "n/a", "total_cost_usd": True}]
    result = parse_events(stream(events).splitlines(), 0.1)
    assert result.usage.input_tokens is None
    assert result.usage.cost_usd is None
    assert result.turns is None


def test_refuses_tools_not_configured_as_read_only():
    with pytest.raises(HeinzelError, match="asks for Bash, Edit"):
        run(runner(FakeRun()), tools=ToolProfile(tools=["Read", "Bash", "Edit"]))


def test_a_profile_may_narrow_the_read_only_tools():
    fake = FakeRun()
    run(runner(fake), tools=ToolProfile(tools=["Read"]))
    assert fake.calls[0][0][6:9] == ["Read", "--allowed-tools", "Read"]


def test_a_missing_cli_says_how_to_fix_it():
    target = ClaudeCodeRunner(RunnerSettings(), run=FakeRun(), which=lambda _: None)
    with pytest.raises(HeinzelError, match=r'not on PATH; install it .* agent_runner = "command"'):
        run(target)
