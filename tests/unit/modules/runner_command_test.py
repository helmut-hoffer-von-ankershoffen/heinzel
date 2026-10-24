"""The generic command agent runner with a recorded fake instead of a real CLI."""

import json
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from heinzel import Budget, HeinzelError, ToolProfile
from heinzel.modules.system.runner._command import CommandRunner, render
from heinzel.modules.system.runner._models import RunnerSettings

pytestmark = pytest.mark.unit


class FakeCli:
    """Records the command line, the files it names and what they held; answers like a configured agent CLI."""

    def __init__(self, stdout: str = "A draft.\n", returncode: int = 0, write: str | None = None) -> None:
        self.stdout, self.returncode, self.write = stdout, returncode, write
        self.command: list[str] = []
        self.kwargs: dict[str, object] = {}
        self.files: dict[str, str] = {}

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.command, self.kwargs = command, kwargs
        for argument in command:
            path = Path(argument.rpartition("=")[2])
            if path.is_file():
                self.files[path.name] = path.read_text(encoding="utf-8")
                self.files[f"{path.name}:mode"] = oct(path.parent.stat().st_mode & 0o777)
            if path.name == "output" and self.write is not None:
                path.write_text(self.write, encoding="utf-8")
        return subprocess.CompletedProcess(command, self.returncode, self.stdout, "it broke")


def runner(fake: FakeCli, **settings: object) -> CommandRunner:
    defaults: dict[str, object] = {"command": ["mytool", "ai", "run", "--prompt", "{prompt_file}"]}
    return CommandRunner(RunnerSettings(**(defaults | settings)), run=fake, which=lambda name: f"/usr/bin/{name}")


def run(target: CommandRunner, **overrides: object):
    arguments: dict[str, object] = {
        "contract": "Only summarize.",
        "tools": ToolProfile(tools=["read"], working_dir=Path("/work")),
        "timeout": timedelta(seconds=90),
        "budget": Budget(),
    } | overrides
    return target.run("Summarize the week.", **arguments)  # type: ignore[arg-type]


def test_runs_the_configured_command_without_a_shell_with_the_prompt_in_a_private_file(monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_ACME_TOKEN", "secret")
    fake = FakeCli()
    result = run(runner(fake))
    assert fake.command[:4] == ["/usr/bin/mytool", "ai", "run", "--prompt"]
    assert Path(fake.command[4]).name == "prompt.md"
    assert fake.files["prompt.md"] == "Summarize the week."
    assert fake.files["prompt.md:mode"] == "0o700"
    assert "shell" not in fake.kwargs
    assert fake.kwargs["timeout"] == 90
    assert fake.kwargs["stdin"] is subprocess.DEVNULL
    env = fake.kwargs["env"]
    assert isinstance(env, dict)
    assert "HZ_CREDENTIAL_ACME_TOKEN" not in env
    assert not Path(fake.command[4]).exists(), "the prompt file is removed after the run"
    assert result.status == "completed"
    assert result.draft == "A draft."
    assert result.runner == "command"


def test_fills_every_placeholder_inside_one_argument():
    fake = FakeCli()
    command = ["mytool", "--contract={contract_file}", "--in", "{working_dir}", "-t", "{tools}", "{timeout_seconds}s"]
    run(
        runner(fake, command=[*command, "--model", "{model}", "--usd", "{max_usd}"], model="deep"),
        budget=Budget(max_usd=2),
    )
    assert fake.command[1].startswith("--contract=")
    assert fake.command[1].endswith("contract.md")
    assert fake.files["contract.md"] == "Only summarize."
    assert fake.command[2:] == ["--in", "/work", "-t", "read", "90s", "--model", "deep", "--usd", "2"]


def test_a_value_with_spaces_or_shell_characters_stays_one_argument():
    assert render(["--tools", "{tools}"], {"tools": "a b; rm -rf /"}) == ["--tools", "a b; rm -rf /"]


@pytest.mark.parametrize(("argument", "problem"), [("{secret}", "unknown placeholders"), ("{", "not a valid template")])
def test_refuses_unknown_placeholders_and_broken_templates(argument, problem):
    with pytest.raises(HeinzelError, match=problem):
        render([argument], {})


def test_reads_json_output_from_the_output_file():
    answer = {"text": " The draft. ", "usage": {"input_tokens": 10, "output_tokens": 5}, "total_cost_usd": 0.01}
    fake = FakeCli(stdout="", write=json.dumps(answer))
    target = runner(
        fake,
        command=["mytool", "--out", "{output_file}"],
        command_output="file",
        command_format="json",
        command_draft_key="text",
    )
    result = run(target)
    assert result.status == "completed"
    assert result.draft == "The draft."
    assert (result.usage.input_tokens, result.usage.output_tokens, result.usage.cost_usd) == (10, 5, 0.01)


def test_a_missing_output_file_is_an_empty_draft():
    result = run(runner(FakeCli(stdout="ignored"), command_output="file"))
    assert result.status == "completed"
    assert result.draft == ""


@pytest.mark.parametrize(
    ("stdout", "detail"),
    [("not json", "output is not JSON"), ('{"other": 1}', "no text under 'result'"), ("[1]", "no text under")],
)
def test_unusable_json_output_is_a_failed_result(stdout, detail):
    result = run(runner(FakeCli(stdout=stdout), command_format="json"))
    assert result.status == "failed"
    assert detail in result.detail


def test_json_output_without_usage_has_unknown_usage():
    result = run(
        runner(FakeCli(stdout='{"result": "ok", "usage": [], "total_cost_usd": "free"}'), command_format="json")
    )
    assert result.draft == "ok"
    assert result.usage.input_tokens is None
    assert result.usage.cost_usd is None


def test_a_failing_command_is_a_failed_result():
    result = run(runner(FakeCli(returncode=3)))
    assert result.status == "failed"
    assert result.detail == "exit code 3: it broke"


def test_a_timeout_stops_the_run_and_is_reported():
    def slow(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 90)

    target = CommandRunner(RunnerSettings(command=["mytool"]), run=slow, which=lambda name: name)
    result = run(target)
    assert result.status == "timeout"
    assert result.detail == "stopped after 0:01:30, the run's timeout"


def test_says_which_limits_it_cannot_enforce():
    result = run(runner(FakeCli()), budget=Budget(max_usd=1.5))
    assert result.warnings == [
        "the command is not known to restrict its agent to read-only tools",
        "the budget of 1.5 USD is not passed to the command, so not enforced",
    ]
    enforced = runner(FakeCli(), command=["mytool", "--usd", "{max_usd}"], command_enforces_read_only=True)
    assert run(enforced, budget=Budget(max_usd=1.5)).warnings == []


def test_a_missing_command_or_executable_says_how_to_fix_it():
    with pytest.raises(HeinzelError, match="No command is configured"):
        run(CommandRunner(RunnerSettings(), run=FakeCli()))
    with pytest.raises(HeinzelError, match="'mytool' is not on PATH"):
        run(CommandRunner(RunnerSettings(command=["mytool"]), run=FakeCli(), which=lambda _: None))
