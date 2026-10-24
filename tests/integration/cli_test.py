"""CLI head -> kernel -> modules -> services, with real collaborators and file I/O. Offline."""

import io
import json
import subprocess
import sys
import urllib.error
from pathlib import Path
from typing import Annotated

import click
import heinzel_clock
import pytest
import typer
from pydantic import BaseModel, Field
from typer.testing import CliRunner

from heinzel import Config, HeinzelError, Module, Settings, __version__
from heinzel.heads import cli
from heinzel.heads.cli import build
from heinzel.kernel import Kernel, ReleaseCache, discover
from heinzel.modules.system import _github
from heinzel.modules.system import _service as system_service

pytestmark = pytest.mark.integration

runner = CliRunner()
SKILLS = Path(__file__).resolve().parents[2] / "agent-skills" / "skills"  # the skills `system agent install` ships


class Greeting(BaseModel):
    text: str
    times: int


def example_modules() -> dict[str, Module]:
    example = Module("example", "An example.")

    @example.operation
    def greet(
        name: Annotated[str, Field(description="Who to greet.")],
        times: Annotated[int, Field(description="How often.")] = 1,
    ) -> Greeting:
        """Greet someone."""
        return Greeting(text=f"Hello, {name}!", times=times)

    @example.operation
    def fail() -> None:
        """Always fail."""
        msg = "this went wrong"
        raise HeinzelError(msg)

    @example.info
    def section() -> dict[str, str]:
        return {"greeting": "hi"}

    ping = Module("ping", "Ping.")

    @ping.operation
    def ping_() -> str:
        """Answer with pong."""
        return "pong"

    return {"example": example, "example__ping": ping}


@pytest.fixture
def app(make_kernel):
    return build(make_kernel(builtins=True, **example_modules()))


def test_version(app):
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    lines = result.output.splitlines()
    assert lines[0] == f"hz {__version__}"
    assert lines[1].startswith("python ")
    assert "plugin heinzel-test 9.9.9" in lines


HINT = "v9.0.0 is available: hz system update"
RELEASE = {"tag_name": "v9.0.0", "html_url": "https://github.com/org/heinzel/releases/tag/v9.0.0"}


def fake_release_source(monkeypatch, answer):
    """The GitHub releases API answering `answer` (raising it if it is an exception); the suite stays offline."""

    def get_json(url, token, timeout):
        assert url == f"{_github.GITHUB_API}/repos/{system_service.DEFAULT_REPOSITORY}/releases/latest"
        assert (token, timeout) == (None, system_service.RELEASE_TIMEOUT)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(_github, "get_json", get_json)


def test_system_release_reports_a_newer_release_and_caches_it(app, monkeypatch):
    fake_release_source(monkeypatch, RELEASE)
    check = json.loads(runner.invoke(app, ["system", "release", "--json"]).output)
    assert (check["current"], check["latest"], check["update_available"]) == (__version__, "9.0.0", True)
    text = runner.invoke(app, ["system", "release"], env={"COLUMNS": "200"}).output
    assert f"v9.0.0 is available (installed: {__version__}): hz system update" in text
    assert ReleaseCache(Settings().cache_dir).latest == "9.0.0"


def test_system_release_says_when_hz_is_up_to_date(app, monkeypatch):
    fake_release_source(monkeypatch, {**RELEASE, "tag_name": f"v{__version__}"})
    result = runner.invoke(app, ["system", "release"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert f"hz {__version__} is up to date" in result.output


def test_system_release_offline_is_a_warning_not_a_failure(app, monkeypatch):
    fake_release_source(monkeypatch, urllib.error.URLError("no route to host"))
    result = runner.invoke(app, ["system", "release"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "Warning: Could not ask" in result.output
    assert "(no route to host)" in result.output
    assert json.loads(runner.invoke(app, ["system", "release", "--json"]).output)["latest"] is None


@pytest.fixture
def newer_release_cached(monkeypatch):
    """A newer release in the cache, stdout a terminal and no CI variable: every condition for the hint holds."""
    ReleaseCache(Settings().cache_dir).remember("9.0.0")
    monkeypatch.setattr(cli, "stdout_is_terminal", lambda: True)  # CliRunner's stdout is never a terminal
    for variable in cli.CI_VARIABLES:
        monkeypatch.delenv(variable, raising=False)


@pytest.mark.usefixtures("newer_release_cached")
def test_version_shows_the_update_hint(app):
    assert runner.invoke(app, ["--version"]).output.splitlines()[-1] == HINT


@pytest.mark.usefixtures("newer_release_cached")
def test_system_info_text_shows_the_update_hint(app):
    assert runner.invoke(app, ["system", "info"], env={"COLUMNS": "200"}).output.rstrip().endswith(HINT)


@pytest.mark.usefixtures("newer_release_cached")
def test_system_info_json_has_the_latest_release_as_data_but_no_hint(app):
    as_json = runner.invoke(app, ["system", "info", "--json"]).output
    assert HINT not in as_json
    assert json.loads(as_json)["latest_release"] == "9.0.0"


@pytest.mark.usefixtures("newer_release_cached")
def test_only_system_info_text_gets_the_update_hint(app):
    assert HINT not in runner.invoke(app, ["system", "modules"], env={"COLUMNS": "200"}).output


@pytest.mark.usefixtures("newer_release_cached")
def test_no_update_hint_when_the_setting_is_off(app, monkeypatch):
    monkeypatch.setenv("HZ_UPDATE_CHECK", "false")
    off = build(Kernel(Config.empty().settings(Settings, "hz"), discover(plugins=False)[0]))
    assert HINT not in runner.invoke(off, ["--version"]).output
    assert HINT not in runner.invoke(off, ["system", "info"], env={"COLUMNS": "200"}).output


@pytest.mark.usefixtures("newer_release_cached")
def test_no_update_hint_when_stdout_is_not_a_terminal(app, monkeypatch):
    monkeypatch.setattr(cli, "stdout_is_terminal", lambda: False)
    assert HINT not in runner.invoke(app, ["--version"]).output
    assert HINT not in runner.invoke(app, ["system", "info"], env={"COLUMNS": "200"}).output


@pytest.mark.usefixtures("newer_release_cached")
@pytest.mark.parametrize("variable", ["CI", "GITHUB_ACTIONS"])
def test_no_update_hint_in_ci(app, monkeypatch, variable):
    monkeypatch.setenv(variable, "true")
    assert HINT not in runner.invoke(app, ["--version"]).output


@pytest.mark.usefixtures("newer_release_cached")
def test_an_empty_ci_variable_is_not_ci(app, monkeypatch):
    monkeypatch.setenv("CI", "")
    assert runner.invoke(app, ["--version"]).output.splitlines()[-1] == HINT


def test_no_update_hint_without_a_newer_release(app, monkeypatch):
    ReleaseCache(Settings().cache_dir).remember(__version__)
    monkeypatch.setattr(cli, "stdout_is_terminal", lambda: True)
    for variable in cli.CI_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    assert "is available" not in runner.invoke(app, ["--version"]).output


def test_stdout_is_terminal_asks_stdout(monkeypatch):
    class Terminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("sys.stdout", Terminal())
    assert cli.stdout_is_terminal()


def test_help_lists_modules(app):
    result = runner.invoke(app, ["--help"])
    assert all(name in result.output for name in ("system", "plugin", "example"))


def test_system_info_json(app):
    result = runner.invoke(app, ["system", "info", "--json"])
    assert result.exit_code == 0, result.output
    info = json.loads(result.output)
    assert info["version"] == __version__
    assert info["sections"] == {"example": {"greeting": "hi"}}
    assert {m["qualname"] for m in info["modules"]} >= {"system", "system.changelog", "system.plugin", "example.ping"}


def test_system_info_rendered(app):
    result = runner.invoke(app, ["system", "info"])
    assert result.exit_code == 0, result.output
    assert "HZ_LOG_LEVEL" in result.output
    assert "example" in result.output


def test_changelog_reads_repository_changelog(app):
    result = runner.invoke(app, ["system", "changelog", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output).startswith("# Changelog")


def test_arguments_options_and_json(app):
    result = runner.invoke(app, ["example", "greet", "Ada", "--times", "2", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"text": "Hello, Ada!", "times": 2}


def test_default_rendering_of_models(app):
    result = runner.invoke(app, ["example", "greet", "Ada"])
    assert "Hello, Ada!" in result.output


def test_submodule_with_single_namesake_operation_collapses(app):
    result = runner.invoke(app, ["example", "ping"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "pong"


def test_heinzel_errors_exit_with_code_1(app):
    result = runner.invoke(app, ["example", "fail"])
    assert result.exit_code == 1
    assert "this went wrong" in result.output


def test_plugin_list_shows_plugin_modules(app):
    result = runner.invoke(app, ["system", "plugin", "list", "--json"])
    assert json.loads(result.output) == [
        {"distribution": "heinzel-test", "version": "9.9.9", "modules": ["example", "example.ping"]}
    ]


def fake_github(monkeypatch, answer):
    """GitHub's search API answering `answer` (raising it if it is an exception); the suite stays offline."""
    from heinzel.modules.system.plugin import _service

    def get_json(url, token, timeout):
        assert url.startswith(_service.GITHUB_SEARCH)
        assert token is None
        if isinstance(answer, Exception):
            raise answer
        return answer

    for name in ("GH_TOKEN", "GITHUB_TOKEN", "HZ_SYSTEM_PLUGIN__GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(_service, "get_json", get_json)


CRM = {
    "name": "heinzel-crm",
    "description": "Customers and [bold]deals[/bold].",
    "html_url": "https://github.com/someone/heinzel-crm",
    "stargazers_count": 7,
}


def test_plugin_search_lists_first_party_and_community_plugins(app, monkeypatch):
    fake_github(monkeypatch, {"items": [CRM]})
    result = runner.invoke(app, ["system", "plugin", "search", "--json"])
    assert result.exit_code == 0, result.output
    found = json.loads(result.output)
    assert [(p["name"], p["source"], p["community"], p["install_spec"]) for p in found["plugins"]] == [
        ("heinzel-clock", "first-party", False, "clock"),
        ("heinzel-crm", "github", True, "git+https://github.com/someone/heinzel-crm"),
    ]
    assert found["warnings"] == []


def test_plugin_search_says_community_plugins_run_with_your_rights(app, monkeypatch):
    fake_github(monkeypatch, {"items": [CRM]})
    result = runner.invoke(app, ["system", "plugin", "search", "--format", "text"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "community, GitHub" in result.output
    assert "[bold]" in result.output  # text from GitHub is printed literally, not as markup (short: tables wrap in CI)
    assert "heinzel-crm" in result.output  # the install spec itself is checked in the --json tests
    assert "Plugins run with your rights." in result.output


def test_plugin_search_offline_shows_first_party_plugins_and_a_warning(app, monkeypatch):
    fake_github(monkeypatch, OSError("network is unreachable"))
    result = runner.invoke(app, ["system", "plugin", "search", "--json"])
    assert result.exit_code == 0, result.output
    found = json.loads(result.output)
    assert [p["name"] for p in found["plugins"]] == ["heinzel-clock"]
    assert found["warnings"] == [
        "GitHub could not be searched (network is unreachable); showing first-party plugins only."
    ]


def test_plugin_search_help_says_it_queries_github(app):
    result = runner.invoke(app, ["system", "plugin", "search", "--help"], env={"COLUMNS": "200"})
    assert "by querying the GitHub API" in result.output


def test_plugin_remove_refuses_heinzel_itself(app):
    result = runner.invoke(app, ["system", "plugin", "remove", "heinzel"])
    assert result.exit_code == 1


def test_disabled_modules_disappear(make_kernel):
    settings = Settings(disabled_modules=frozenset({"example"}))
    app = build(make_kernel(builtins=True, settings_=settings, **example_modules()))
    assert runner.invoke(app, ["example", "greet", "Ada"]).exit_code != 0


def test_python_module_entry_point():
    result = subprocess.run(
        [sys.executable, "-m", "heinzel", "--version"], capture_output=True, text=True, check=True, timeout=10
    )
    assert result.stdout.splitlines()[0] == f"hz {__version__}"


def test_plugins_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("HZ_PLUGINS_ENABLED", "false")
    result = subprocess.run(
        [sys.executable, "-m", "heinzel", "system", "plugin", "list", "--json"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == []


def test_system_modules_rendered(app):
    result = runner.invoke(app, ["system", "modules"])
    assert result.exit_code == 0, result.output
    assert "system.changelog" in result.output


def test_changelog_rendered(app):
    result = runner.invoke(app, ["system", "changelog"])
    assert result.exit_code == 0, result.output
    assert "Changelog" in result.output


def test_config_layers_files_and_environment(tmp_path, monkeypatch):
    """Through a real `python -m heinzel` process: user file < project hz.toml < environment."""
    user = tmp_path / "user"
    user.mkdir()
    (user / "config.toml").write_text(
        '[hz]\nlog_level = "INFO"\ndisabled_modules = ["system.plugin"]\n', encoding="utf-8"
    )
    project = tmp_path / "project"
    project.mkdir()
    (project / "hz.toml").write_text('[hz]\nlog_level = "ERROR"\n', encoding="utf-8")
    monkeypatch.setenv("HZ_CONFIG_HOME", str(user))

    def show() -> dict[str, object]:
        result = subprocess.run(
            [sys.executable, "-m", "heinzel", "system", "config", "show", "--json"],
            capture_output=True, text=True, check=True, timeout=10, cwd=project,
        )  # fmt: skip
        report = json.loads(result.stdout)
        assert [f["scope"] for f in report["files"] if f["exists"]] == ["user", "project"]
        return {s["key"]: s["value"] for s in report["settings"] if s["section"] == "hz"}

    assert show()["log_level"] == "ERROR"
    assert show()["disabled_modules"] == ["system.plugin"]
    monkeypatch.setenv("HZ_LOG_LEVEL", "DEBUG")
    assert show()["log_level"] == "DEBUG"


def test_config_init(app, tmp_path):
    target = tmp_path / "config.toml"
    result = runner.invoke(app, ["system", "config", "init", "--path", str(target)])
    assert result.exit_code == 0, result.output
    assert target.is_file()
    assert runner.invoke(app, ["system", "config", "init", "--path", str(target)]).exit_code == 1


def test_config_show_rendered(app):
    result = runner.invoke(app, ["system", "config", "show"])
    assert result.exit_code == 0, result.output
    assert "HZ_LOG_LEVEL" in result.output


def test_agent_install(make_kernel, monkeypatch, tmp_path):
    monkeypatch.setenv("HZ_SYSTEM_AGENT__SKILL_DIRS", json.dumps([str(tmp_path / "skills")]))
    app = build(make_kernel(builtins=True))
    result = runner.invoke(app, ["system", "agent", "install", "--json"])
    assert result.exit_code == 0, result.output
    shipped = {skill.parent.name for skill in SKILLS.glob("*/SKILL.md")}
    assert shipped
    assert {Path(i["target"]).name for i in json.loads(result.output)} == shipped
    assert (tmp_path / "skills" / "hz-init" / "SKILL.md").is_file()


def test_disabling_a_module_hides_operations_that_depend_on_it(make_kernel):
    class Clock:
        pass

    provider, consumer = Module("provider", "p"), Module("consumer", "c")
    provider.service(Clock)

    @consumer.operation
    def tick(clock: Clock) -> str:
        """Tick."""
        return "tick"

    @consumer.operation
    def ping() -> str:
        """Ping."""
        return "pong"

    settings = Settings(disabled_modules=frozenset({"provider", "system.plugin"}))
    app = build(make_kernel(builtins=True, settings_=settings, provider=provider, consumer=consumer))
    assert runner.invoke(app, ["consumer", "ping"]).output.strip() == "pong"
    assert runner.invoke(app, ["consumer", "tick"]).exit_code != 0
    assert runner.invoke(app, ["system", "update", "--help"]).exit_code == 0


def test_format_option_and_output_format_setting(app, monkeypatch):
    as_json = runner.invoke(app, ["example", "greet", "Ada", "--format", "json"])
    assert json.loads(as_json.output) == {"text": "Hello, Ada!", "times": 1}
    assert "Hello, Ada!" in runner.invoke(app, ["example", "greet", "Ada", "--format", "text"]).output


def test_output_format_setting_makes_json_the_default(make_kernel, monkeypatch):
    monkeypatch.setenv("HZ_OUTPUT_FORMAT", "json")
    from heinzel import Config

    app = build(make_kernel(builtins=True, settings_=Config.empty().settings(Settings, "hz"), **example_modules()))
    assert json.loads(runner.invoke(app, ["example", "greet", "Ada"]).output)["text"] == "Hello, Ada!"


def test_doctor_reports_and_exits_by_status(app):
    result = runner.invoke(app, ["system", "doctor", "--json"])
    report = json.loads(result.output)
    assert report["status"] in {"up", "degraded"}
    assert result.exit_code == 0
    assert {c["name"] for c in report["checks"]["system.doctor"]} >= {"python", "uv", "installation", "data_dir"}


def test_doctor_is_down_when_a_check_fails(make_kernel):
    from heinzel import Check, Status

    broken = Module("broken", "b")
    broken.health(lambda: [Check(name="db", status=Status.DOWN, detail="unreachable", fix="start it")])
    exploding = Module("exploding", "e")

    @exploding.health
    def boom() -> list[Check]:
        msg = "no"
        raise RuntimeError(msg)

    app = build(make_kernel(builtins=True, broken=broken, exploding=exploding))
    result = runner.invoke(app, ["system", "doctor"])
    assert result.exit_code == 1
    assert "down" in result.output
    report = json.loads(runner.invoke(app, ["system", "doctor", "--json"]).output)
    assert report["checks"]["exploding"][0]["detail"] == "RuntimeError: no"


def test_commands_are_sorted(app):
    root = typer.main.get_command(app)
    system = root.commands["system"]  # type: ignore[attr-defined]
    assert root.list_commands(None) == sorted(root.list_commands(None))  # type: ignore[arg-type]
    expected = [
        "agent",
        "changelog",
        "config",
        "credential",
        "doctor",
        "info",
        "modules",
        "plugin",
        "release",
        "serve",
        "update",
    ]
    assert system.list_commands(None) == expected


def test_config_get_and_set_through_the_cli(app, tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    assert runner.invoke(app, ["system", "config", "set", "hz.log_level", "ERROR"]).exit_code == 0
    assert "log_level" in (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert runner.invoke(app, ["system", "config", "set", "hz.log_level", "LOUD"]).exit_code == 1


def test_os_errors_are_one_line_messages(app, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["system", "config", "init", "--path", str(blocker / "config.toml")])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Traceback" not in result.output


def test_unexpected_errors_are_reported_as_bugs(make_kernel):
    broken = Module("broken", "b")

    @broken.operation
    def explode() -> None:
        """Explode."""
        msg = "boom"
        raise RuntimeError(msg)

    app = build(make_kernel(builtins=True, broken=broken))
    result = runner.invoke(app, ["broken", "explode"])
    assert result.exit_code == 1
    assert "This is a bug" in result.output


def test_invalid_arguments_are_rejected_before_running(make_kernel):
    counting = Module("counting", "c")

    @counting.operation
    def count(n: Annotated[int, Field(ge=1, description="How many.")] = 1) -> int:
        """Count."""
        return n

    app = build(make_kernel(builtins=True, counting=counting))
    result = runner.invoke(app, ["counting", "count", "--n", "0"])
    assert result.exit_code == 1
    assert "greater than or equal to 1" in result.output


# Agent-ready: every operation's command takes --json. The servers are commands but no operations: they run until
# stopped and print no result to format.
SERVERS = {"hz system serve api", "hz system serve mcp"}


def leaves_without_json(command, path: str = "hz") -> list[str]:
    """Paths of leaf commands in a click tree that have no --json option."""
    if isinstance(command, typer.core.TyperGroup):
        return [leaf for name, sub in command.commands.items() for leaf in leaves_without_json(sub, f"{path} {name}")]
    has_json = any(
        "--json" in parameter.opts for parameter in command.params if isinstance(parameter, typer.core.TyperOption)
    )
    return [] if has_json else [path]


def test_finds_commands_without_json():
    app = typer.Typer()

    @app.command()
    def plain() -> None:
        """A command without --json."""

    @app.command()
    def other() -> None:
        """Another command without --json."""

    assert sorted(leaves_without_json(typer.main.get_command(app))) == ["hz other", "hz plain"]


def test_every_operation_accepts_json(make_kernel):
    app = build(make_kernel(builtins=True, clock=heinzel_clock.module, **example_modules()))
    assert set(leaves_without_json(typer.main.get_command(app))) == SERVERS
    help_text = " ".join(click.unstyle(runner.invoke(app, ["--help"]).output).split())  # CI forces colours
    assert "Every operation accepts --json" in help_text


def test_the_cli_loads_no_serving_stack():
    """`hz` starts fast: FastAPI, MCP and uvicorn load only when `hz system serve` starts a server."""
    code = (
        "import sys\nimport heinzel.heads.cli as cli\nfrom heinzel.kernel import boot\ncli.build(boot())\n"
        "print(' '.join(sorted(m for m in ('fastapi', 'mcp', 'uvicorn') if m in sys.modules)))"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=30)
    assert result.stdout.strip() == ""
