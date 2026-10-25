"""The Codex CLI runner and AI access option (ADR 0029, 0057), with recorded `codex exec --json` events."""

import json
import tomllib
from datetime import timedelta
from pathlib import Path

import pytest

from heinzel import Budget, HeinzelError, McpServer, ToolProfile
from heinzel.modules.system.runner._codex import READS, CodexAccess, CodexRunner, mcp_options, parse_events
from heinzel.modules.system.runner._models import RunnerSettings
from heinzel.modules.system.runner._process import Outcome

pytestmark = pytest.mark.unit

EVENTS = [
    {"type": "thread.started", "thread_id": "t-1"},
    {"type": "turn.started"},
    {
        "type": "item.completed",
        "item": {"id": "i1", "type": "command_execution", "command": "ls", "status": "completed"},
    },
    {"type": "item.completed", "item": {"id": "i2", "type": "mcp_tool_call", "tool": "system_doctor"}},
    {"type": "item.completed", "item": {"id": "i3", "type": "agent_message", "text": "The weekly digest."}},
    {"type": "turn.completed", "usage": {"input_tokens": 1200, "cached_input_tokens": 1000, "output_tokens": 40}},
]


def jsonl(events: list[object]) -> str:
    return "\n".join(json.dumps(event) for event in events) + "\nnot json\n"


class Fake:
    def __init__(self, outcome: Outcome | None = None, login: Outcome | None = None) -> None:
        self.outcome = outcome or Outcome(returncode=0, stdout=jsonl(EVENTS), stderr="")
        self.login = login or Outcome(returncode=0, stdout="Logged in using ChatGPT\n", stderr="")
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command: list[str], **kwargs: object) -> Outcome:
        self.calls.append((command, kwargs))
        return self.login if command[1:3] == ["login", "status"] else self.outcome


def runner(fake: Fake, **settings: object) -> CodexRunner:
    return CodexRunner(RunnerSettings(**settings), execute=fake, which=lambda name: f"/bin/{name}")


def run(target: CodexRunner, tmp_path: Path, **overrides: object):
    arguments: dict[str, object] = {
        "contract": "Only summarize.",
        "tools": ToolProfile(working_dir=tmp_path),
        "timeout": timedelta(minutes=5),
        "budget": Budget(),
    } | overrides
    return target.run("Summarize the week.", **arguments)  # type: ignore[arg-type]


def test_runs_codex_exec_read_only_ephemeral_with_the_prompt_on_standard_input(tmp_path):
    fake = Fake()
    result = run(runner(fake, codex_model="gpt-5-codex", codex_effort="high"), tmp_path)
    [(command, kwargs)] = fake.calls
    assert command[:7] == [
        "/bin/codex",
        "exec",
        "--json",
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--skip-git-repo-check",
    ]
    assert command[command.index("-C") + 1] == str(tmp_path)
    assert command[command.index("-m") + 1] == "gpt-5-codex"
    assert 'model_reasoning_effort="high"' in command
    assert command[-1] == "-"
    assert kwargs["input"] == "Only summarize.\n\n---\n\nSummarize the week."
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert not any(key.startswith("HZ_CREDENTIAL_") for key in env)
    assert (result.status, result.draft, result.runner, result.model) == (
        "completed",
        "The weekly digest.",
        "codex",
        "gpt-5-codex",
    )
    assert result.tool_calls == {"shell": 1, "system_doctor": 1}
    assert (result.usage.input_tokens, result.usage.cache_read_tokens, result.usage.output_tokens) == (200, 1000, 40)
    assert READS in result.warnings


def test_mcp_servers_become_config_overrides_in_toml():
    server = McpServer(command=["/usr/bin/python3", "-m", "heinzel"], env={"CODEX_FLAG": "1"})
    options = mcp_options(ToolProfile(mcp_servers={"hz": server}))
    assert options == [
        "-c", 'mcp_servers.hz.command="/usr/bin/python3"',
        "-c", 'mcp_servers.hz.args=["-m", "heinzel"]',
        "-c", 'mcp_servers.hz.env={"CODEX_FLAG" = "1"}',
    ]  # fmt: skip


def test_what_codex_can_t_enforce_is_a_warning(tmp_path):
    result = run(
        runner(Fake()), tmp_path, budget=Budget(max_usd=0.5), tools=ToolProfile(tools=["Read"], working_dir=tmp_path)
    )
    assert any("0.5 USD is not enforced" in warning for warning in result.warnings)
    assert any("no tool allowlist" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    ("events", "detail"),
    [
        ([{"type": "turn.failed", "error": {"message": "usage limit reached"}}], "usage limit reached"),
        ([{"type": "error", "message": "stream disconnected"}], "stream disconnected"),
        ([{"type": "turn.completed", "usage": {}}], "Codex printed no answer"),
    ],
)
def test_a_failed_turn_says_why(events, detail):
    result = parse_events(jsonl(events).splitlines(), 1.0, None)
    assert (result.status, result.detail, result.draft) == ("failed", detail, "")


def test_the_events_of_codex_before_0_44_are_read_too():
    legacy = [{"type": "item.completed", "item": {"item_type": "assistant_message", "text": "Old."}}]
    assert parse_events(jsonl(legacy).splitlines(), 1.0, None).draft == "Old."


def test_a_failing_cli_reports_the_exit_code_and_a_login_fix(tmp_path):
    failing = Outcome(returncode=1, stdout="", stderr="Error: not logged in; run codex login")
    result = run(runner(Fake(failing)), tmp_path)
    assert result.status == "failed"
    assert "exit code 1" in result.detail
    assert result.fix == "codex login"


def test_a_timeout_stops_the_run_and_is_reported(tmp_path):
    result = run(runner(Fake(Outcome(returncode=None, stdout="", stderr=""))), tmp_path)
    assert result.status == "timeout"


def test_a_missing_cli_says_how_to_fix_it(tmp_path):
    target = CodexRunner(RunnerSettings(), execute=Fake(), which=lambda _name: None)
    with pytest.raises(HeinzelError, match="Install it"):
        run(target, tmp_path)


def test_the_access_option_reads_the_login_and_names_its_runner():
    fake = Fake()
    found = CodexAccess(RunnerSettings(), execute=fake, which=lambda name: f"/bin/{name}").status()
    assert (found.installed, found.signed_in, found.runner) == (True, True, "codex")
    assert "Logged in using ChatGPT" in found.detail
    assert fake.calls[0][0] == ["/bin/codex", "login", "status"]
    out = Fake(login=Outcome(returncode=1, stdout="Not logged in\n", stderr=""))
    found = CodexAccess(RunnerSettings(), execute=out, which=lambda name: f"/bin/{name}").status()
    assert (found.signed_in, found.fix) == (False, "codex login")
    missing = CodexAccess(RunnerSettings(), execute=fake, which=lambda _name: None).status()
    assert (missing.installed, missing.fix.startswith("Install Codex")) == (False, True)


def test_the_access_test_is_one_short_answer():
    fake = Fake(
        Outcome(
            returncode=0,
            stdout=jsonl([{"type": "item.completed", "item": {"type": "agent_message", "text": "OK"}}]),
            stderr="",
        )
    )
    result = CodexAccess(RunnerSettings(), execute=fake, which=lambda name: f"/bin/{name}").test()
    assert (result.ok, result.detail) == (True, "OK")
    assert not CodexAccess(RunnerSettings(), execute=fake, which=lambda _name: None).test().ok


def test_a_notice_mid_run_is_no_failure_when_an_answer_follows():
    events = [
        {"type": "error", "message": "Reconnecting... 1/5"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Done."}},
        {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 1}},
    ]
    result = parse_events(jsonl(events).splitlines(), 1.0, None)
    assert (result.status, result.draft) == ("completed", "Done.")


def test_hostile_mcp_values_stay_inside_their_toml_strings():
    command = ['/bin/x" = 1 evil', 'a"] = [', "back\\slash", "😀 ünïcode"]
    env = {'K" = "v': 'x"}'}
    options = mcp_options(ToolProfile(mcp_servers={"hz": McpServer(command=command, env=env)}))
    values = [option.partition("=")[2] for option in options[1::2]]
    parsed = [tomllib.loads(f"v = {value}")["v"] for value in values]
    assert parsed == [command[0], command[1:], env]  # each value reads back as itself, nothing more


def test_a_control_character_in_an_mcp_value_is_refused(tmp_path):
    server = McpServer(command=["/bin/hz", "bell\x07"])
    with pytest.raises(HeinzelError, match="control character"):
        mcp_options(ToolProfile(mcp_servers={"hz": server}))


def test_an_mcp_server_name_can_t_break_out_of_its_key():
    with pytest.raises(ValueError, match="mcp_servers"):
        ToolProfile(mcp_servers={"hz.command": McpServer(command=["/bin/x"])})


def test_the_environment_has_codex_home_and_no_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-y")
    fake = Fake()
    run(runner(fake), tmp_path)
    env = fake.calls[0][1]["env"]
    assert isinstance(env, dict)
    assert env["CODEX_HOME"] == str(tmp_path)
    assert "OPENAI_API_KEY" not in env
    assert "HZ_CREDENTIAL_AI_ACCESS_API_KEY" not in env


def test_a_non_zero_exit_fails_a_run_even_with_an_answer(tmp_path):
    result = run(runner(Fake(Outcome(returncode=2, stdout=jsonl(EVENTS), stderr="boom"))), tmp_path)
    assert (result.status, result.draft) == ("failed", "")
    assert "exit code 2" in result.detail


def test_cached_tokens_beyond_the_input_count_as_none_sent():
    events = [
        {"type": "item.completed", "item": {"type": "agent_message", "text": "x"}},
        {"type": "turn.completed", "usage": {"input_tokens": 5, "cached_input_tokens": 9, "output_tokens": 1}},
    ]
    assert parse_events(jsonl(events).splitlines(), 1.0, None).usage.input_tokens == 0


def test_a_login_status_that_hangs_or_can_t_start_is_reported():
    hangs = Fake(login=Outcome(returncode=None, stdout="", stderr=""))
    found = CodexAccess(RunnerSettings(), execute=hangs, which=lambda name: f"/bin/{name}").status()
    assert (found.installed, found.signed_in, "in time" in found.detail) == (True, None, True)

    def broken(command: list[str], **kwargs: object) -> Outcome:
        raise PermissionError(13, "Permission denied")

    found = CodexAccess(RunnerSettings(), execute=broken, which=lambda name: f"/bin/{name}").status()
    assert "didn't start" in found.detail


def test_a_model_that_looks_like_an_option_is_refused():
    with pytest.raises(ValueError, match="codex_model"):
        RunnerSettings(codex_model="--yolo")
    assert "codex_executable" in RunnerSettings.ignored_in_project
