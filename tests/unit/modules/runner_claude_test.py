"""The Claude Code agent runner with a recorded fake instead of the `claude` CLI; no test runs a real agent."""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from heinzel import Budget, HeinzelError, McpServer, Progress, Status, ToolProfile
from heinzel.modules.system.runner._claude import ClaudeCodeRunner, login_check, parse_events
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
        "--include-partial-messages",
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


def test_mcp_servers_go_into_a_private_config_file_and_replace_safe_mode(tmp_path):
    fake = FakeExecute()
    seen: dict[str, object] = {}

    def capture(command: list[str], **kwargs: object) -> Outcome:
        path = Path(command[command.index("--mcp-config") + 1])
        seen["config"] = json.loads(path.read_text(encoding="utf-8"))
        seen["mode"] = oct(path.parent.stat().st_mode & 0o777)
        return fake(command, **kwargs)

    server = McpServer(command=["/usr/bin/python3", "-m", "heinzel", "system", "serve", "mcp", "--chat", "ab12"])
    tools = ToolProfile(working_dir=tmp_path, mcp_servers={"hz": server})
    target = ClaudeCodeRunner(RunnerSettings(), execute=capture, which=lambda name: f"/bin/{name}")
    run(target, tmp_path, tools=tools)
    [(command, _)] = fake.calls
    assert "--safe-mode" not in command, "Claude Code loads no --mcp-config server under --safe-mode"
    assert command[command.index("--setting-sources") + 1] == ""
    assert {"--restricted", "--strict-mcp-config", "--disable-slash-commands"} <= set(command)
    assert command[command.index("--allowed-tools") + 1] == "Read,Grep,Glob,mcp__hz"
    assert command[command.index("--tools") + 1] == "Read,Grep,Glob"
    assert seen["mode"] == "0o700"
    assert seen["config"] == {
        "mcpServers": {"hz": {"type": "stdio", "command": "/usr/bin/python3", "args": server.command[1:], "env": {}}}
    }


def test_streams_text_deltas_and_tool_calls_to_the_progress_emitter():
    events: list[tuple[str, dict[str, object]]] = []

    class Listening(Progress):
        def emitter(self):  # type: ignore[override]
            return lambda kind, data: events.append((kind, dict(data)))

    lines = [
        json.dumps({"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "The "}}}),
        json.dumps({"type": "stream_event", "event": {"delta": {"type": "input_json_delta", "partial_json": "{"}}}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__hz__x"}]}}),
        "not json",
        json.dumps(["a list"]),
        json.dumps({"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "week"}}}),
    ]

    def execute(command: list[str], **kwargs: object) -> Outcome:
        on_line = kwargs["on_line"]
        for line in lines:
            on_line(line + "\n")  # type: ignore[operator]
        return Outcome(returncode=0, stdout=stream(EVENTS), stderr="")

    target = ClaudeCodeRunner(RunnerSettings(), Listening(), execute=execute, which=lambda name: f"/bin/{name}")
    run(target)
    assert events == [("text", {"text": "The "}), ("tool", {"name": "mcp__hz__x"}), ("text", {"text": "week"})]


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
    assert result.detail == "Claude Code ended (exit code 2): boom"
    assert result.draft == ""


def test_a_failing_cli_without_a_result_reports_exit_code_and_stderr():
    result = run(runner(FakeExecute(Outcome(returncode=1, stdout="", stderr="x" * 600 + "not logged in"))))
    assert result.status == "failed"
    assert result.detail.startswith("Claude Code printed no result (exit code 1): ")
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


def recorded(error: str | None, result: str, *, subtype: str = "success", reason: str = "api_error") -> str:
    """What Claude Code prints when the API refuses a run, as recorded from Claude Code 2.1 (2026-10-24)."""
    assistant: dict[str, object] = {
        "type": "assistant",
        "message": {"model": "<synthetic>", "content": [{"type": "text", "text": result}]},
        "is_api_error_message": True,
    }
    if error is not None:
        assistant["error"] = error
    final = {
        "type": "result",
        "subtype": subtype,
        "is_error": True,
        "terminal_reason": reason,
        "result": result,
        "num_turns": 1,
        "total_cost_usd": 0,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
    return stream([{"type": "system", "subtype": "init", "model": "claude-sonnet-test"}, assistant, final])


@pytest.mark.parametrize(
    ("error", "said", "detail", "fix"),
    [
        (
            "authentication_failed",
            "Not logged in · Please run /login",
            "Claude Code isn't logged in: run `claude auth login` (Not logged in · Please run /login)",
            "claude auth login",
        ),
        (
            "authentication_failed",
            "Failed to authenticate: OAuth session expired and could not be refreshed",
            (
                "Claude Code's login expired: run `claude auth login` "
                "(Failed to authenticate: OAuth session expired and could not be refreshed)"
            ),
            "claude auth login",
        ),
        (
            "rate_limit",
            "API Error: 429 rate_limit_error",
            "Claude Code hit a rate limit; try again in a minute (API Error: 429 rate_limit_error)",
            "",
        ),
        (
            None,
            "API Error: 529 overloaded_error",
            "The model is overloaded right now; try again shortly (API Error: 529 overloaded_error)",
            "",
        ),
        (None, "Something else broke", "Claude Code: Something else broke", ""),
    ],
)
def test_an_api_error_is_a_failure_that_says_why_and_how_to_fix_it(error, said, detail, fix):
    outcome = Outcome(returncode=1, stdout=recorded(error, said), stderr="")
    result = run(runner(FakeExecute(outcome)))
    assert result.status == "failed"
    assert result.draft == ""
    assert result.detail == f"{detail} (exit code 1)"
    assert result.fix == fix


def test_an_error_with_exit_code_zero_is_still_a_failure():
    """`is_error: true` wins over `subtype: success`, whatever the exit code."""
    result = run(runner(FakeExecute(Outcome(returncode=0, stdout=recorded(None, "Not logged in"), stderr=""))))
    assert result.status == "failed"
    assert result.fix == "claude auth login"


def test_an_error_text_is_cleaned_and_cut():
    said = "\x1b[31m" + "e" * 1000
    result = run(runner(FakeExecute(Outcome(returncode=0, stdout=recorded(None, said), stderr=""))))
    assert "\x1b" not in result.detail
    assert len(result.detail) < 400


def test_a_budget_stop_stays_a_budget_stop():
    stopped = recorded(None, "", subtype="error_max_budget_usd", reason="budget")
    assert run(runner(FakeExecute(Outcome(returncode=1, stdout=stopped, stderr="")))).status == "budget"


def test_a_run_without_file_tools_gets_no_built_in_tools_only_its_mcp_servers():
    fake = FakeExecute()
    server = McpServer(command=["/usr/bin/python3"])
    run(runner(fake), tools=ToolProfile(read_files=False, mcp_servers={"hz": server}))
    [(command, _)] = fake.calls
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--allowed-tools") + 1] == "mcp__hz"


def test_the_login_check_runs_in_an_empty_directory_and_survives_a_broken_cli():
    seen: dict[str, object] = {}

    def execute(command: list[str], **kwargs: object) -> Outcome:
        cwd = kwargs["cwd"]
        assert isinstance(cwd, Path)
        seen["cwd"], seen["files"] = cwd, list(cwd.iterdir())
        return Outcome(returncode=1, stdout='{"loggedIn": false}', stderr="")

    check = login_check("/bin/claude", execute=execute)
    assert (check.status, check.fix) == (Status.DEGRADED, "claude auth login")
    assert seen["files"] == []
    assert not Path(str(seen["cwd"])).exists()

    def broken(command: list[str], **kwargs: object) -> Outcome:
        raise PermissionError(13, "Permission denied")

    assert login_check("/bin/claude", execute=broken).status == Status.DEGRADED
