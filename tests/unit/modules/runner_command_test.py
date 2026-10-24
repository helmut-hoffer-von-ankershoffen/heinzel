"""The generic command agent runner with a recorded fake instead of a real CLI."""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from heinzel import Budget, HeinzelError, ToolProfile
from heinzel.modules.system.runner._command import CommandRunner, environment, placeholders, read_output, render
from heinzel.modules.system.runner._models import RunnerSettings
from heinzel.modules.system.runner._process import Outcome

pytestmark = pytest.mark.unit

TRUSTED = {"command_enforces_read_only": True, "command_read_only_tools": ["read", "search"]}


class FakeCli:
    """Records the command line, the files it names and what they held; answers like a configured agent CLI."""

    def __init__(
        self, stdout: str = "A draft.\n", returncode: int | None = 0, write: str | None = None, truncated: bool = False
    ) -> None:
        self.outcome = Outcome(returncode=returncode, stdout=stdout, stderr="it broke", truncated=truncated)
        self.write = write
        self.command: list[str] = []
        self.kwargs: dict[str, object] = {}
        self.files: dict[str, str] = {}

    def __call__(self, command: list[str], **kwargs: object) -> Outcome:
        self.command, self.kwargs = command, kwargs
        for argument in command:
            path = Path(argument.rpartition("=")[2])
            if path.is_file():
                self.files[path.name] = path.read_text(encoding="utf-8")
                self.files[f"{path.name}:mode"] = oct(path.parent.stat().st_mode & 0o777)
            if path.name == "output" and self.write is not None:
                path.write_text(self.write, encoding="utf-8")
        return self.outcome


def runner(fake: FakeCli, **settings: object) -> CommandRunner:
    defaults: dict[str, object] = {"command": ["mytool", "ai", "run", "--prompt", "{prompt_file}"]} | TRUSTED
    return CommandRunner(RunnerSettings(**(defaults | settings)), execute=fake, which=lambda name: f"/usr/bin/{name}")


def run(target: CommandRunner, **overrides: object):
    arguments: dict[str, object] = {
        "contract": "Only summarize.",
        "tools": ToolProfile(tools=["read"]),
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
    assert fake.kwargs["timeout"] == 90
    assert fake.kwargs["input"] is None
    env = fake.kwargs["env"]
    assert isinstance(env, dict)
    assert "HZ_CREDENTIAL_ACME_TOKEN" not in env
    assert not Path(fake.command[4]).exists(), "the prompt file is removed after the run"
    assert result.status == "completed"
    assert result.draft == "A draft."
    assert result.runner == "command"
    assert result.warnings == []


def test_refuses_to_run_until_the_user_records_that_the_command_is_read_only():
    with pytest.raises(HeinzelError, match="refuses to run: nothing records that the configured command restricts"):
        run(runner(FakeCli(), command_enforces_read_only=False))


def test_refuses_tools_not_listed_as_read_only():
    with pytest.raises(HeinzelError, match=r"asks for write, which \[system.runner\] command_read_only_tools"):
        run(runner(FakeCli()), tools=ToolProfile(tools=["read", "write"]))


def test_the_environment_is_an_allowlist():
    source = {
        "PATH": "/bin",
        "HOME": "/home/a",
        "LANG": "C",
        "LC_ALL": "C",
        "TMPDIR": "/scratch",
        "MYTOOL_LOGIN": "x",
        "AWS_SECRET_ACCESS_KEY": "no",
        "HZ_CREDENTIAL_ACME_TOKEN": "no",
    }
    assert environment(["MYTOOL_LOGIN", "HZ_CREDENTIAL_ACME_TOKEN"], source) == {
        "PATH": "/bin",
        "HOME": "/home/a",
        "LANG": "C",
        "LC_ALL": "C",
        "TMPDIR": "/scratch",
        "MYTOOL_LOGIN": "x",
    }


def test_fills_every_placeholder_inside_one_argument(tmp_path):
    fake = FakeCli()
    command = ["mytool", "--contract={contract_file}", "--in", "{working_dir}", "-t", "{tools}", "{timeout_seconds}s"]
    target = runner(fake, command=[*command, "--model", "{model}", "--usd", "{max_usd}", "{{literal}}"], model="deep")
    run(target, budget=Budget(max_usd=2), tools=ToolProfile(tools=["read", "search"], working_dir=tmp_path))
    assert fake.command[1].startswith("--contract=")
    assert fake.files["contract.md"] == "Only summarize."
    assert fake.command[2:] == [
        "--in",
        str(tmp_path),
        "-t",
        "read,search",
        "90s",
        "--model",
        "deep",
        "--usd",
        "2",
        "{literal}",
    ]


def test_a_value_with_spaces_or_shell_characters_stays_one_argument():
    assert render(["--tools", "{tools}"], {"tools": "a b; rm -rf /"}) == ["--tools", "a b; rm -rf /"]


@pytest.mark.parametrize(
    ("argument", "problem"),
    [
        ("{secret}", "unknown placeholders"),
        ("{", "not a valid template"),
        ("{tools!r}", "format spec or conversion"),
        ("{model:>40}", "format spec or conversion"),
        ("{prompt_file.__class__}", "unknown placeholders"),
    ],
)
def test_refuses_unknown_placeholders_specs_and_broken_templates(argument, problem):
    with pytest.raises(HeinzelError, match=problem):
        placeholders(argument)


def test_a_bad_placeholder_is_refused_before_anything_runs():
    fake = FakeCli()
    with pytest.raises(HeinzelError, match="unknown placeholders"):
        run(runner(fake, command=["mytool", "{secret}"]))
    assert fake.command == []


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


def test_a_missing_output_file_is_a_failed_result():
    result = run(runner(FakeCli(stdout="ignored"), command_output="file"))
    assert (result.status, result.detail) == ("failed", "the command wrote no output file")


def test_the_output_file_is_never_read_through_a_symlink(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("private", encoding="utf-8")
    link = tmp_path / "output"
    link.symlink_to(secret)
    with pytest.raises(HeinzelError, match="not a regular file"):
        read_output(link)


def test_an_oversized_output_file_is_refused(tmp_path):
    big = tmp_path / "output"
    big.write_bytes(b"x" * 11)
    with pytest.raises(HeinzelError, match="larger than 10 bytes"):
        read_output(big, limit=10)
    assert read_output(tmp_path / "none") is None
    Path(big).chmod(0o600)
    assert read_output(big, limit=11) == "x" * 11


def test_oversized_stdout_is_a_failed_result():
    result = run(runner(FakeCli(truncated=True)))
    assert result.status == "failed"
    assert result.detail.startswith("the command printed more than")


@pytest.mark.parametrize(
    ("stdout", "fmt", "detail"),
    [
        ("not json", "json", "output is not JSON"),
        ('{"other": 1}', "json", "no text under 'result'"),
        ("[1]", "json", "no text under"),
        ('{"result": "  "}', "json", "no text under"),
        ("  \n", "text", "the command printed nothing"),
    ],
)
def test_unusable_or_empty_output_is_a_failed_result(stdout, fmt, detail):
    result = run(runner(FakeCli(stdout=stdout), command_format=fmt))
    assert result.status == "failed"
    assert detail in result.detail


def test_json_output_with_odd_usage_has_unknown_usage():
    stdout = '{"result": "ok", "usage": {"input_tokens": true}, "total_cost_usd": "free"}'
    result = run(runner(FakeCli(stdout=stdout), command_format="json"))
    assert result.draft == "ok"
    assert result.usage.input_tokens is None, "a bool is no token count"
    assert result.usage.cost_usd is None
    assert (
        run(runner(FakeCli(stdout='{"result": "ok", "usage": []}'), command_format="json")).usage.output_tokens is None
    )


def test_a_failing_command_is_a_failed_result():
    result = run(runner(FakeCli(returncode=3)))
    assert result.status == "failed"
    assert result.detail == "exit code 3: it broke"


def test_a_timeout_stops_the_run_and_is_reported():
    result = run(runner(FakeCli(returncode=None)))
    assert result.status == "timeout"
    assert result.detail == "stopped after 0:01:30, the run's timeout"


def test_says_when_it_cannot_enforce_the_budget():
    result = run(runner(FakeCli()), budget=Budget(max_usd=1.5))
    assert result.warnings == ["the budget of 1.5 USD is not passed to the command, so not enforced"]
    enforced = runner(FakeCli(), command=["mytool", "--usd={max_usd}"])
    assert run(enforced, budget=Budget(max_usd=1.5)).warnings == []
    mentioned = runner(FakeCli(), command=["mytool", "{{max_usd}}"])
    assert run(mentioned, budget=Budget(max_usd=1.5)).warnings != [], "an escaped brace is no placeholder"


def test_a_missing_command_executable_or_working_directory_says_how_to_fix_it(tmp_path):
    with pytest.raises(HeinzelError, match="No command is configured"):
        run(CommandRunner(RunnerSettings(**TRUSTED), execute=FakeCli()))
    with pytest.raises(HeinzelError, match=r"executable \(mytool\) is not on PATH"):
        run(CommandRunner(RunnerSettings(command=["mytool"], **TRUSTED), execute=FakeCli(), which=lambda _: None))
    with pytest.raises(HeinzelError, match="does not exist"):
        run(runner(FakeCli()), tools=ToolProfile(working_dir=tmp_path / "gone"))
