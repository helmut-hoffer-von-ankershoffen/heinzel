"""`hz setup` and `hz setup <module>` through the CLI head, answered on stdin as a person would (ADR 0032). Offline."""

import json
import tomllib

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from pydantic import Field
from typer.testing import CliRunner

from heinzel import (
    Check,
    Config,
    ConfigEditor,
    CredentialLet,
    Credentials,
    ManualInstall,
    Module,
    ModuleSettings,
    PrerequisiteService,
    Progress,
    Prompter,
    Status,
)
from heinzel.heads import api, mcp
from heinzel.heads.cli import _setup, build
from heinzel.kernel import ScriptedPrompter, user_config_dir
from heinzel.modules.system.prerequisite._service import ToolPrerequisites
from heinzel.testing import MemoryBackend
from tests._prerequisite_machine import ready_everywhere

pytestmark = pytest.mark.integration

runner = CliRunner()


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
def settings(settings):
    """The built-in org step asks who you are first in every run; tests/integration/org_test.py runs it."""
    return settings.model_copy(update={"disabled_modules": frozenset({"org"})})


@pytest.fixture
def terminal(monkeypatch, tmp_path):
    monkeypatch.setattr(_setup, "has_terminal", lambda: True)
    monkeypatch.setattr(_setup.getpass, "getpass", input)  # CliRunner's stdin is no terminal device to hide input on
    monkeypatch.chdir(tmp_path)  # doctor after setup reads the files again; no hz.toml from above the repository


@pytest.fixture
def kernel(make_kernel):
    kernel = make_kernel(builtins=True, greet=greet(), vault=vault())
    kernel.bind(Credentials, Credentials(MemoryBackend(), [(r.qualname, let) for r, let in _credentials(kernel)]))
    return kernel


def _credentials(kernel):
    return kernel.lets(CredentialLet)


def user_config() -> dict:
    return tomllib.loads((user_config_dir() / "config.toml").read_text(encoding="utf-8"))


def test_setup_runs_every_step_writes_through_the_services_and_ends_with_doctor(kernel, terminal):
    answers = ["", "Moin", "s3cret"]  # optional greeting, required vault token
    result = runner.invoke(build(kernel), ["setup"], input="\n".join(answers) + "\n")
    assert result.exit_code == 0, result.output
    assert user_config() == {
        "greet": {"greeting": "Moin"},
    }
    assert kernel.resolve(Credentials).backend.values == {("vault", "api-token"): "s3cret"}
    output = result.output
    assert "Still to do: greet (optional), vault (required)." in output
    assert "Set up vault now?" not in output
    assert "s3cret" not in output
    assert output.index("Setup") < output.index("Heinzelmännchen is")  # the summary, then doctor


def test_optional_steps_can_be_skipped_and_configured_ones_kept(make_kernel, terminal):
    kernel = make_kernel(builtins=True, greet=greet(), vault=vault())
    kernel.bind(Credentials, Credentials(MemoryBackend(), [(r.qualname, let) for r, let in _credentials(kernel)]))
    result = runner.invoke(build(kernel), ["setup"], input="n\ns3cret\n")
    assert result.exit_code == 0, result.output
    assert " skipped " in result.output
    configured = Config(files=(), data={"greet": {"greeting": "Servus"}})
    kept = make_kernel(builtins=True, config=configured, greet=greet())
    result = runner.invoke(build(kept), ["setup"], input="\n")
    assert result.exit_code == 0, result.output
    assert "greet is already set up. Change it? [y/N]" in result.output
    assert " kept " in result.output


def test_ctrl_c_keeps_finished_steps(kernel, terminal):
    result = runner.invoke(build(kernel), ["setup"], input="\nHi\n")  # input ends at the vault token
    assert result.exit_code == 130
    assert user_config() == {"greet": {"greeting": "Hi"}}
    assert "interrupted" in result.output
    assert "Heinzelmännchen is" not in result.output


def test_setup_with_a_module_runs_only_its_step(kernel, terminal):
    result = runner.invoke(build(kernel), ["setup", "greet"], input="Hej\n")
    assert result.exit_code == 0, result.output
    assert "Set up greet now?" not in result.output
    assert result.output.rstrip().endswith("greet: done")
    assert user_config() == {"greet": {"greeting": "Hej"}}


@pytest.mark.parametrize(
    ("name", "problem"),
    [("nope", "No module is named 'nope'."), ("system", "Module 'system' has no setup step.")],
)
def test_setup_names_the_modules_that_have_a_step_when_given_another(kernel, terminal, name, problem):
    result = runner.invoke(build(kernel), ["setup", name])
    assert result.exit_code == 2
    text = " ".join(result.stderr.replace("│", " ").split())  # the error is boxed
    assert f"{problem} Modules with one: greet, vault." in text
    assert "Usage: hz setup [OPTIONS] [MODULE]" in text


def test_setup_says_when_a_modules_step_cant_run(make_kernel, settings, terminal):
    provider = Module("provider", "Provides.")

    class Service:
        pass

    provider.service(Service)
    needy = Module("needy", "Needs the service of a disabled module.")

    @needy.setup
    def setup(service: Service) -> None:
        """Use it."""

    disabled = settings.model_copy(update={"disabled_modules": frozenset({"provider"})})
    kernel = make_kernel(builtins=True, settings_=disabled, provider=provider, needy=needy)
    result = runner.invoke(build(kernel), ["setup", "needy"])
    assert result.exit_code == 2
    assert "The setup step of 'needy' can't run" in " ".join(result.stderr.replace("│", " ").split())


def test_setup_help_names_the_module_argument(kernel):
    text = " ".join(runner.invoke(build(kernel), ["setup", "--help"]).output.split())
    assert "[MODULE]" in text
    assert "With MODULE, set up only that module." in text


def test_a_failing_step_exits_with_1(kernel, terminal):
    kernel.bind(Credentials, Credentials(MemoryBackend(), []))  # the token is no longer declared: storing fails
    result = runner.invoke(build(kernel), ["setup", "vault"], input="s3cret\n")
    assert result.exit_code == 1
    assert "vault: failed" in result.output


def test_without_a_terminal_setup_refuses_with_the_equivalent_commands(kernel):
    result = runner.invoke(build(kernel), ["setup"])
    assert result.exit_code == 1
    text = " ".join(result.stderr.split())
    assert "needs a terminal" in text
    assert "hz system config set greet.greeting '\"Hello\"'" in text
    assert "hz system credential set api-token --service vault" in text
    assert runner.invoke(build(kernel), ["setup", "greet"]).exit_code == 1


def test_a_bound_prompter_answers_instead_of_the_terminal(kernel):
    prompter = ScriptedPrompter(["Hallo"])
    kernel.bind(Prompter, prompter)
    result = runner.invoke(build(kernel), ["setup", "greet"])
    assert result.exit_code == 0, result.output
    assert "Greeting [Hello]: Hallo" in prompter.transcript


def test_with_no_steps_setup_says_so(make_kernel):
    result = runner.invoke(build(make_kernel(builtins=True)), ["setup"])
    assert result.exit_code == 0
    assert "No module has a setup step." in result.output


def tools_only() -> Module:
    module = Module("tools", "Needs a tool and asks nothing.")
    module.prerequisite(
        "gizmo",
        title="Gizmo",
        purpose="Powers the tools.",
        linux=ManualInstall("Install the gizmo with your package manager."),
    )
    return module


def test_an_owner_without_a_step_shows_its_missing_tool_and_does_not_fail_the_run(make_kernel, terminal):
    kernel = make_kernel(builtins=True, tools=tools_only())
    kernel.bind(PrerequisiteService, ToolPrerequisites(kernel, Progress(), ready_everywhere("gizmo")))

    result = runner.invoke(build(kernel), ["setup", "--json"])

    assert result.exit_code == 0, result.output
    steps = json.loads(result.stdout)["steps"]
    assert steps == [
        {
            "module": "tools",
            "required": False,
            "outcome": "pending",
            "detail": "gizmo: Install the gizmo with your package manager.",
            "prerequisites": True,
        }
    ]


def test_ready_tools_leave_setup_with_nothing_to_say_about_them(make_kernel, terminal):
    kernel = make_kernel(builtins=True, tools=tools_only())
    kernel.bind(PrerequisiteService, ToolPrerequisites(kernel, Progress(), ready_everywhere()))

    result = runner.invoke(build(kernel), ["setup"])

    assert result.exit_code == 0, result.output
    assert "No module has a setup step." in result.output
    assert "gizmo" not in result.output


def test_json_without_any_step_or_missing_tool_is_empty(make_kernel, terminal):
    kernel = make_kernel(builtins=True, tools=tools_only())
    kernel.bind(PrerequisiteService, ToolPrerequisites(kernel, Progress(), ready_everywhere()))

    result = runner.invoke(build(kernel), ["setup", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"steps": [], "doctor": None}


def test_setup_for_a_module_with_only_tools_points_to_the_check(make_kernel, terminal):
    result = runner.invoke(build(make_kernel(builtins=True, tools=tools_only())), ["setup", "tools"])

    assert result.exit_code == 2
    assert "hz system prerequisite check --owner tools" in " ".join(result.stderr.replace("│", " ").split())


def test_mcp_and_rest_expose_no_setup(kernel):
    async def tools() -> set[str]:
        async with Client(mcp.build(kernel)) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    names = anyio.run(tools)
    assert "greet_hello" in names
    assert not [name for name in names if "setup" in name]
    paths = TestClient(api.build(kernel), base_url="http://localhost").get("/openapi.json").json()["paths"]
    assert "/greet/hello" in paths
    assert not [path for path in paths if "setup" in path]


def test_a_broken_configuration_file_is_an_error_line(make_kernel, terminal):
    (user_config_dir() / "config.toml").write_text("[greet\n", encoding="utf-8")
    result = runner.invoke(build(make_kernel(builtins=True, greet=greet())), ["setup"], input="\n\n")
    assert result.exit_code == 1
    assert "greet failed:" in result.output  # writing the greeting needs the file
    assert "Error:" in result.stderr  # and doctor can't read it
    assert "is not valid TOML" in result.stderr


def test_without_doctor_setup_ends_with_the_summary(make_kernel, settings, terminal):
    disabled = settings.model_copy(update={"disabled_modules": settings.disabled_modules | {"system.doctor"}})
    kernel = make_kernel(builtins=True, settings_=disabled, greet=greet())
    result = runner.invoke(build(kernel), ["setup"], input="\n\n")
    assert result.exit_code == 0, result.output
    assert "Heinzelmännchen is" not in result.output
    assert " done " in result.output


def test_a_setup_step_never_changes_a_modules_command_path(make_kernel, terminal):
    """A collapsed module whose operation takes an argument stays `hz echo hi`, as without a step and in MCP/REST."""
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
    assert runner.invoke(app, ["echo", "hi", "--json"]).stdout.strip() == '"hi"'
    assert runner.invoke(app, ["echo", "setup", "--json"]).stdout.strip() == '"setup"'  # just an argument
    result = runner.invoke(app, ["setup", "echo"])
    assert result.exit_code == 0, result.output
    assert "echo: done" in result.output


def test_setup_fails_when_doctor_reports_a_check_down(make_kernel, terminal):
    broken = greet()
    broken.health(lambda: [Check(name="db", status=Status.DOWN, detail="unreachable", fix="start it")])
    result = runner.invoke(build(make_kernel(builtins=True, greet=broken)), ["setup"], input="\n\n")
    assert result.exit_code == 1
    assert " done " in result.output
    assert "hz system doctor reports a problem with the configuration" in " ".join(result.stderr.split())
