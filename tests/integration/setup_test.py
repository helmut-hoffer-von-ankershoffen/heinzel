"""`hz setup` and `hz <module> setup` through the CLI head, answered on stdin as a person would (ADR 0032). Offline."""

import tomllib

import anyio
import heinzel_clock
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from pydantic import Field
from typer.testing import CliRunner

from heinzel import Config, ConfigEditor, CredentialLet, Credentials, Module, ModuleSettings, Prompter
from heinzel.heads import api, mcp
from heinzel.heads.cli import _setup, build
from heinzel.kernel import ScriptedPrompter, user_config_dir
from heinzel.testing import MemoryBackend

pytestmark = pytest.mark.integration

runner = CliRunner()
AT = "2026-10-23T12:00:00Z"


class GreetSettings(ModuleSettings):
    """Settings of the greet module."""

    greeting: str = Field(default="Hello", description="How to greet.")


def greet() -> Module:
    module = Module("greet", "Greets.")
    module.settings(GreetSettings)

    @module.operation
    def hello(settings: GreetSettings) -> str:
        """Greet the world."""
        return f"{settings.greeting}, world!"

    @module.setup
    def setup(prompter: Prompter, settings: GreetSettings, editor: ConfigEditor) -> None:
        """Choose a greeting."""
        editor.write("greet.greeting", prompter.text("Greeting", default=settings.greeting))

    return module


def vault() -> Module:
    module = Module("vault", "Keeps a token.")
    token = module.credential("api-token", purpose="Token for the vault")

    @module.setup(required=True)
    def setup(prompter: Prompter, credentials: Credentials) -> None:
        """Store the vault token."""
        credentials.set(token, prompter.secret("Token"))

    return module


@pytest.fixture
def terminal(monkeypatch, tmp_path):
    monkeypatch.setattr(_setup, "stdin_is_terminal", lambda: True)
    monkeypatch.chdir(tmp_path)  # doctor after setup reads the files again; no hz.toml from above the repository


@pytest.fixture
def kernel(make_kernel):
    kernel = make_kernel(builtins=True, clock=heinzel_clock.module, greet=greet(), vault=vault())
    kernel.bind(Credentials, Credentials(MemoryBackend(), [(r.qualname, let) for r, let in _credentials(kernel)]))
    return kernel


def _credentials(kernel):
    return kernel.lets(CredentialLet)


def user_config() -> dict:
    return tomllib.loads((user_config_dir() / "config.toml").read_text(encoding="utf-8"))


def test_setup_runs_every_step_writes_through_the_services_and_ends_with_doctor(kernel, terminal):
    answers = [
        "",  # Set up clock now?
        "1",  # keep Europe/Berlin only
        "tokyo",  # add a zone
        "",  # done adding
        "",  # Berlin starts at 9
        "17",  # Berlin ends at 17
        "10",  # Tokyo starts at 10
        "19",  # Tokyo ends at 19
        "",  # save
        "",  # Set up greet now?
        "Moin",  # greeting
        "s3cret",  # vault token, required: no skip question
    ]
    result = runner.invoke(build(kernel), ["setup"], input="\n".join(answers) + "\n")
    assert result.exit_code == 0, result.output
    assert user_config() == {
        "clock": {"zones": [{"name": "Europe/Berlin", "hours": [9, 17]}, {"name": "Asia/Tokyo", "hours": [10, 19]}]},
        "greet": {"greeting": "Moin"},
    }
    assert kernel.resolve(Credentials).backend.values == {("vault", "api-token"): "s3cret"}
    output = result.output
    assert "Setting up 3 modules: clock (optional), greet (optional), vault (required)." in output
    assert "Set up vault now?" not in output
    assert "s3cret" not in output
    assert "Asia/Tokyo 10-19" in output
    assert output.index("Setup") < output.index("Heinzelmännchen is")  # the summary, then doctor
    doctor = output[output.index("Heinzelmännchen is") :]
    assert "Tokyo" in doctor  # doctor sees what was just written, not the defaults hz started with
    assert "Los_Angeles" not in doctor


def test_optional_steps_can_be_skipped_and_configured_ones_kept(make_kernel, terminal):
    configured = Config(files=(), data={"greet": {"greeting": "Servus"}})
    kernel = make_kernel(builtins=True, config=configured, clock=heinzel_clock.module, greet=greet(), vault=vault())
    kernel.bind(Credentials, Credentials(MemoryBackend(), [(r.qualname, let) for r, let in _credentials(kernel)]))
    result = runner.invoke(build(kernel), ["setup"], input="n\n\ns3cret\n")
    assert result.exit_code == 0, result.output
    assert "greet is already set up. Change it? [y/N]" in result.output
    assert " skipped " in result.output
    assert " kept " in result.output


def test_ctrl_c_keeps_finished_steps(kernel, terminal):
    result = runner.invoke(build(kernel), ["setup"], input="n\n\nHi\n")  # input ends at the vault token
    assert result.exit_code == 130
    assert user_config() == {"greet": {"greeting": "Hi"}}
    assert "interrupted" in result.output
    assert "Heinzelmännchen is" not in result.output


def test_one_module_has_its_own_setup_command(kernel, terminal):
    result = runner.invoke(build(kernel), ["greet", "setup"], input="Hej\n")
    assert result.exit_code == 0, result.output
    assert "Set up greet now?" not in result.output
    assert result.output.rstrip().endswith("greet: done")
    assert user_config() == {"greet": {"greeting": "Hej"}}


def test_a_collapsed_module_keeps_its_command_and_gains_setup(kernel, terminal):
    app = build(kernel)
    times = runner.invoke(app, ["clock", "--at", AT, "--json"])
    assert times.exit_code == 0, times.output
    assert times.stdout.startswith("[")
    help_text = runner.invoke(app, ["clock", "--help"]).output
    assert "--at" in help_text
    assert "setup" in help_text
    result = runner.invoke(app, ["clock", "setup"], input="\n\n" + "\n" * 6 + "n\n")
    assert result.exit_code == 0, result.output
    assert "Nothing was saved." in result.output


def test_a_failing_step_exits_with_1(kernel, terminal):
    kernel.bind(Credentials, Credentials(MemoryBackend(), []))  # the token is no longer declared: storing fails
    result = runner.invoke(build(kernel), ["vault", "setup"], input="s3cret\n")
    assert result.exit_code == 1
    assert "vault: failed" in result.output


def test_without_a_terminal_setup_refuses_with_the_equivalent_commands(kernel):
    result = runner.invoke(build(kernel), ["setup"])
    assert result.exit_code == 1
    text = " ".join(result.stderr.split())
    assert "needs a terminal" in text
    assert (
        """hz system config set clock.zones '[{ name = "Europe/Berlin", hours = [9, 18] }, """
        """{ name = "America/New_York", hours = [9, 18] }, { name = "America/Los_Angeles", hours = [9, 18] }]'"""
    ) in text
    assert "hz system config set greet.greeting '\"Hello\"'" in text
    assert "hz system credential set api-token --service vault" in text
    assert runner.invoke(build(kernel), ["greet", "setup"]).exit_code == 1


def test_a_bound_prompter_answers_instead_of_the_terminal(kernel):
    prompter = ScriptedPrompter(["Hallo"])
    kernel.bind(Prompter, prompter)
    result = runner.invoke(build(kernel), ["greet", "setup"])
    assert result.exit_code == 0, result.output
    assert "Greeting [Hello]: Hallo" in prompter.transcript


def test_with_no_steps_setup_says_so(make_kernel):
    result = runner.invoke(build(make_kernel(builtins=True)), ["setup"])
    assert result.exit_code == 0
    assert "No module has a setup step." in result.output


def test_mcp_and_rest_expose_no_setup(kernel):
    async def tools() -> set[str]:
        async with Client(mcp.build(kernel)) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    names = anyio.run(tools)
    assert "clock" in names
    assert not [name for name in names if "setup" in name]
    paths = TestClient(api.build(kernel), base_url="http://localhost").get("/openapi.json").json()["paths"]
    assert "/clock" in paths
    assert not [path for path in paths if "setup" in path]


INVALID_CLOCK = Config(files=(), data={"clock": {"zones": [{"name": "Asia/Tokyo", "hours": [19, 10]}]}})


def test_invalid_settings_fail_the_step_with_a_message_not_a_traceback(make_kernel, terminal):
    kernel = make_kernel(builtins=True, config=INVALID_CLOCK, clock=heinzel_clock.module)
    result = runner.invoke(build(kernel), ["clock", "setup"])
    assert result.exit_code == 1
    assert "clock failed: its configuration is invalid: ClockSettings zones.0" in " ".join(result.output.split())
    assert "hz system config show" in result.output


def test_invalid_settings_without_a_terminal_are_a_configuration_error(make_kernel):
    kernel = make_kernel(builtins=True, config=INVALID_CLOCK, clock=heinzel_clock.module)
    result = runner.invoke(build(kernel), ["setup"])
    assert result.exit_code == 1
    assert "Configuration error:" in result.stderr
    assert "Traceback" not in result.output


def test_a_broken_configuration_file_is_an_error_line(make_kernel, terminal):
    (user_config_dir() / "config.toml").write_text("[greet\n", encoding="utf-8")
    result = runner.invoke(build(make_kernel(builtins=True, greet=greet())), ["setup"], input="\n\n")
    assert result.exit_code == 1
    assert "greet failed:" in result.output  # writing the greeting needs the file
    assert "Error:" in result.stderr  # and doctor can't read it
    assert "is not valid TOML" in result.stderr


def test_without_doctor_setup_ends_with_the_summary(make_kernel, settings, terminal):
    disabled = settings.model_copy(update={"disabled_modules": frozenset({"system.doctor"})})
    kernel = make_kernel(builtins=True, settings_=disabled, greet=greet())
    result = runner.invoke(build(kernel), ["setup"], input="\n\n")
    assert result.exit_code == 0, result.output
    assert "Heinzelmännchen is" not in result.output
    assert " done " in result.output


def test_a_module_whose_only_command_takes_arguments_keeps_both_reachable(make_kernel, terminal):
    module = Module("echo", "Echoes.")

    @module.operation
    def echo(text: str) -> str:
        """Echo the text."""
        return text

    @module.setup
    def setup(prompter: Prompter) -> None:
        """Say hi."""
        prompter.say("hi")

    app = build(make_kernel(builtins=True, echo=module))
    assert runner.invoke(app, ["echo", "echo", "setup", "--json"]).stdout.strip() == '"setup"'
    result = runner.invoke(app, ["echo", "setup"])
    assert result.exit_code == 0, result.output
    assert "echo: done" in result.output
