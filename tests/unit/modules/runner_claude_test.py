"""The Claude Code agent runner with a recorded fake instead of the `claude` CLI; no test runs a real agent."""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from heinzel import Budget, HeinzelError, ToolProfile
from heinzel.modules.system.runner._claude import ClaudeCodeRunner, parse_events
from heinzel.modules.system.runner._models import RunnerSettings
from heinzel.modules.system.runner._process import Outcome

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


class FakeExecute:
    """Records the call, and the contract file while it exists; answers like `claude -p --output-format stream-json`."""

    def __init__(self, outcome: Outcome | None = None) -> None:
        self.outcome = outcome or Outcome(returncode=0, stdout=stream(EVENTS), stderr="")
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.contract: tuple[str, str] | None = None

    def __call__(self, command: list[str], **kwargs: object) -> Outcome:
        self.calls.append((command, kwargs))
        if "--append-system-prompt-file" in command:
            path = Path(command[command.index("--append-system-prompt-file") + 1])
            self.contract = (path.read_text(encoding="utf-8"), oct(path.parent.stat().st_mode & 0o777))
        return self.outcome


def runner(fake: FakeExecute, **settings: object) -> ClaudeCodeRunner:
    return ClaudeCodeRunner(RunnerSettings(**settings), execute=fake, which=lambda name: f"/bin/{name}")


def run(target: ClaudeCodeRunner, tmp_path: Path | None = None, **overrides: object):
    arguments: dict[str, object] = {
        "contract": "Only summarize.",
        "tools": ToolProfile(working_dir=tmp_path),
        "timeout": timedelta(minutes=5),
        "budget": Budget(max_usd=0.5),
    } | overrides
    return target.run("Summarize the week.", **arguments)  # type: ignore[arg-type]


def test_runs_claude_isolated_from_the_users_setup_with_a_read_only_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("HZ_CREDENTIAL_GITHUB_API_TOKEN", "secret")
    monkeypatch.setenv("KEEP_ME", "1")
    fake = FakeExecute()
    run(runner(fake, model="sonnet"), tmp_path)
    [(command, kwargs)] = fake.calls
    contract_file = command[command.index("--append-system-prompt-file") + 1]
    assert command == [
        "/bin/claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--safe-mode",
        "--restricted",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--permission-mode",
        "dontAsk",
        "--tools",
        "Read,Grep,Glob",
        "--allowed-tools",
        "Read,Grep,Glob",
        "--append-system-prompt-file",
        contract_file,
        "--model",
        "sonnet",
        "--max-budget-usd",
        "0.5",
    ]
    assert "--bare" not in command, "--bare would refuse OAuth logins"
    assert "Summarize the week." not in command
    assert "Only summarize." not in command
    assert fake.contract == ("Only summarize.", "0o700")
    assert not Path(contract_file).exists(), "the contract file is removed after the run"
    assert kwargs["input"] == "Summarize the week."
    assert kwargs["timeout"] == 300
    assert kwargs["cwd"] == tmp_path
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert "HZ_CREDENTIAL_GITHUB_API_TOKEN" not in env
    assert env["KEEP_ME"] == "1"
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert env["DISABLE_AUTOUPDATER"] == "1"


def test_resolves_the_cli_to_an_absolute_path_before_changing_directory(tmp_path):
    fake = FakeExecute()
    ClaudeCodeRunner(RunnerSettings(claude_executable="./claude"), execute=fake, which=lambda name: name).run(
        "p", contract="", tools=ToolProfile(working_dir=tmp_path), timeout=timedelta(seconds=5), budget=Budget()
    )
    assert fake.calls[0][0][0] == str(Path("claude").resolve())


def test_parses_draft_tool_calls_turns_and_cost():
    result = run(runner(FakeExecute()))
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
    fake = FakeExecute()
    run(runner(fake), contract="", budget=Budget())
    command = fake.calls[0][0]
    assert "--append-system-prompt-file" not in command
    assert "--max-budget-usd" not in command
    assert "--model" not in command


def test_a_timeout_stops_the_run_and_is_reported_not_raised():
    result = run(runner(FakeExecute(Outcome(returncode=None, stdout=stream(EVENTS[:2]), stderr=""))))
    assert result.status == "timeout"
    assert result.draft == ""
    assert result.tool_calls == {"Read": 1}
    assert "0:05:00" in result.detail


def test_a_run_over_budget_is_reported():
    events = [{"type": "result", "subtype": "error_max_budget_usd", "is_error": True, "total_cost_usd": 0.51}]
    result = run(runner(FakeExecute(Outcome(returncode=1, stdout=stream(events), stderr=""))))
    assert result.status == "budget"
    assert result.usage.cost_usd == 0.51
    assert result.draft == ""


def test_a_failing_cli_with_a_result_reports_the_exit_code():
    result = run(runner(FakeExecute(Outcome(returncode=2, stdout=stream(EVENTS), stderr="boom"))))
    assert result.status == "failed"
    assert result.detail == "exit code 2 (ended): boom"
    assert result.draft == ""


def test_a_failing_cli_without_a_result_reports_exit_code_and_stderr():
    result = run(runner(FakeExecute(Outcome(returncode=1, stdout="", stderr="x" * 600 + "not logged in"))))
    assert result.status == "failed"
    assert result.detail.startswith("exit code 1 (Claude Code printed no result): ")
    assert result.detail.endswith("not logged in")
    assert len(result.detail) < 600


def test_missing_or_empty_output_is_failed():
    assert run(runner(FakeExecute(Outcome(returncode=0, stdout="", stderr="")))).detail == (
        "Claude Code printed no result"
    )
    empty = [{"type": "result", "subtype": "success", "result": "  "}]
    result = run(runner(FakeExecute(Outcome(returncode=0, stdout=stream(empty), stderr=""))))
    assert (result.status, result.detail) == ("failed", "Claude Code returned an empty result")


def test_truncated_output_says_so():
    result = run(runner(FakeExecute(Outcome(returncode=0, stdout=stream(EVENTS[:2]), stderr="", truncated=True))))
    assert result.status == "failed"
    assert result.detail.startswith("Claude Code printed no result; output over")


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
    events = [
        {
            "type": "result",
            "subtype": "success",
            "result": "ok",
            "usage": {"input_tokens": True, "output_tokens": "9"},
            "total_cost_usd": True,
        }
    ]
    result = parse_events(stream(events).splitlines(), 0.1)
    assert result.usage.input_tokens is None, "a bool is no token count"
    assert result.usage.output_tokens is None
    assert result.usage.cost_usd is None
    assert result.turns is None
    assert (
        parse_events([json.dumps({"type": "result", "subtype": "success", "usage": "n/a"})], 0).usage.input_tokens
        is None
    )


def test_refuses_tools_not_configured_as_read_only():
    with pytest.raises(HeinzelError, match="asks for Bash, Edit"):
        run(runner(FakeExecute()), tools=ToolProfile(tools=["Read", "Bash", "Edit"]))


def test_a_profile_may_narrow_the_read_only_tools():
    fake = FakeExecute()
    run(runner(fake), tools=ToolProfile(tools=["Read"]))
    command = fake.calls[0][0]
    assert command[command.index("--tools") : command.index("--tools") + 4] == [
        "--tools",
        "Read",
        "--allowed-tools",
        "Read",
    ]


def test_a_missing_cli_says_how_to_fix_it():
    target = ClaudeCodeRunner(RunnerSettings(), execute=FakeExecute(), which=lambda _: None)
    with pytest.raises(
        HeinzelError, match=r"not on PATH. Install it .*: hz system config set capabilities.agent_runner command$"
    ):
        run(target)


def test_a_missing_working_directory_is_an_error(tmp_path):
    with pytest.raises(HeinzelError, match="does not exist or is not a directory"):
        run(runner(FakeExecute()), tools=ToolProfile(working_dir=tmp_path / "gone"))
