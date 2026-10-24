"""The update notice and the hourly check (ADR 0031) through the CLI head, with a fake package source. Offline."""

import json
import sys
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from heinzel import Module, Settings, __version__
from heinzel.heads import cli
from heinzel.heads.cli import _update, build
from heinzel.kernel import Kernel, ModuleRecord, Origin, ReleaseCache, ReleaseSource, discover

pytestmark = pytest.mark.integration

runner = CliRunner()
NOTICE = f"hz 9.0.0 is available (installed {__version__}): hz system update"
CLOCK = Origin("heinzel-clockwork", "1.2.3", builtin=False, url="https://github.com/someone/heinzel-clockwork")


class FakeSource(ReleaseSource):
    """A package source that answers `answer` (raising it if it is an exception) and counts the questions."""

    def __init__(self, answer: object = "9.0.0") -> None:
        self.answer = answer
        self.asked: list[float] = []

    def latest_version(self, timeout: float) -> str:
        self.asked.append(timeout)
        if isinstance(self.answer, Exception):
            raise self.answer
        return str(self.answer)


def plugin_module() -> Module:
    module = Module("clockwork", "Wind the clocks.")

    @module.operation
    def wind() -> str:
        """Wind them."""
        return "wound"

    return module


def make_kernel(*, update_check: bool = True) -> Kernel:
    records = [*discover(plugins=False)[0], ModuleRecord("clockwork", plugin_module(), CLOCK)]
    return Kernel(Settings(update_check=update_check), records)


@pytest.fixture
def cache() -> ReleaseCache:
    return ReleaseCache(Settings().cache_dir)


@pytest.fixture
def human(monkeypatch, cache):
    """A newer release cached, a terminal, no CI variable: every condition for the notice holds."""
    cache.remember("9.0.0")
    monkeypatch.setattr(_update, "is_terminal", lambda console: True)  # CliRunner's streams are never terminals
    for variable in _update.CI_VARIABLES:
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def app(human):
    """The CLI with every condition for the notice holding; built after them, as the start screen is."""
    del human
    return build(make_kernel())


def test_text_output_is_followed_by_the_notice_on_stderr(app):
    result = runner.invoke(app, ["clockwork", "wind"])
    assert result.stdout.strip() == "wound"
    assert result.stderr.strip() == NOTICE


@pytest.mark.parametrize("options", [["--json"], ["--format", "json"]])
def test_json_never_gets_the_notice(app, options):
    result = runner.invoke(app, ["clockwork", "wind", *options])
    assert json.loads(result.stdout) == "wound"
    assert NOTICE not in result.output


@pytest.mark.usefixtures("human")
def test_json_by_setting_never_gets_the_notice():
    app = build(Kernel(Settings(update_check=True, output_format="json"), discover(plugins=False)[0]))
    assert NOTICE not in runner.invoke(app, ["system", "modules"]).output


def test_version_lists_repositories_and_shows_the_notice_on_stderr(app):
    result = runner.invoke(app, ["--version"])
    lines = result.stdout.splitlines()
    assert lines[0] == f"hz {__version__}"
    assert lines[1] == "repository https://github.com/helmut-hoffer-von-ankershoffen/heinzel"
    assert "plugin heinzel-clockwork 1.2.3 https://github.com/someone/heinzel-clockwork" in lines
    assert result.stderr.strip() == NOTICE


def test_the_start_screen_shows_plugins_with_versions_and_links_and_the_notice(app):
    screen = runner.invoke(app, []).output
    plugins = screen[screen.index("Plugins") : screen.index("Core")]
    assert "heinzel-clockwork 1.2.3" in plugins
    assert "https://github.com/someone/heinzel-clockwork" in plugins
    assert NOTICE in " ".join(screen.split())
    footer = screen[screen.index("Heinzelmännchen hz") :]
    assert "https://" not in footer  # the footer names, versions and describes; no link


def test_subcommand_help_keeps_the_footer_without_notice_or_link(app):
    result = runner.invoke(app, ["clockwork", "--help"], env={"COLUMNS": "200"})
    assert f"hz {__version__} · open-source helpers" in result.stdout
    assert NOTICE not in result.stdout
    assert "https://" not in result.stdout


def test_system_info_carries_the_repository_and_latest_release_as_data(app):
    info = json.loads(runner.invoke(app, ["system", "info", "--json"]).stdout)
    assert info["repository"] == "https://github.com/helmut-hoffer-von-ankershoffen/heinzel"
    assert info["latest_release"] == "9.0.0"
    clockwork = next(module for module in info["modules"] if module["qualname"] == "clockwork")
    assert clockwork["repository"] == CLOCK.url
    text = runner.invoke(app, ["system", "info"], env={"COLUMNS": "200"})
    assert text.stderr.strip() == NOTICE


def test_doctor_reports_the_newer_release_instead_of_the_notice(app):
    result = runner.invoke(app, ["system", "doctor", "--json"])
    [release] = [check for check in json.loads(result.stdout)["checks"]["system.doctor"] if check["name"] == "release"]
    assert release == {
        "name": "release",
        "status": "up",
        "detail": f"hz 9.0.0 is available (installed {__version__})",
        "fix": "hz system update",
    }
    assert NOTICE not in runner.invoke(app, ["system", "doctor"], env={"COLUMNS": "200"}).stderr


def test_changelog_starts_with_a_header_naming_the_newer_release_and_its_notes(app):
    result = runner.invoke(app, ["system", "changelog"])
    text = " ".join(result.stdout.split())
    assert text.startswith(
        f"You're on hz {__version__}; hz 9.0.0 is available: hz system update. What's new: "
        "https://github.com/helmut-hoffer-von-ankershoffen/heinzel/releases"
    )
    assert "Changelog" in text
    assert NOTICE not in result.stderr
    as_json = json.loads(runner.invoke(app, ["system", "changelog", "--json"]).stdout)
    assert as_json.startswith("# Changelog")  # unchanged: the header is for people only


@pytest.mark.usefixtures("human")
@pytest.mark.parametrize(
    "condition",
    ["setting off", "stderr not a terminal", "CI", "nothing newer"],
)
def test_no_notice_when(monkeypatch, cache, condition):
    update_check = condition != "setting off"
    if condition == "stderr not a terminal":
        monkeypatch.setattr(_update, "is_terminal", lambda console: False)
    if condition == "CI":
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
    if condition == "nothing newer":
        cache.remember(__version__)
    app = build(make_kernel(update_check=update_check))
    for args in (["clockwork", "wind"], ["--version"], []):
        assert "is available" not in runner.invoke(app, args, env={"COLUMNS": "200"}).output


def test_an_empty_ci_variable_is_not_ci(app, monkeypatch):
    monkeypatch.setenv("CI", "")
    assert runner.invoke(app, ["clockwork", "wind"]).stderr.strip() == NOTICE


def run_main(monkeypatch, kernel: Kernel, *args: str) -> None:
    """Run the `hz` entry point on `kernel`, as a shell would: it ends with SystemExit."""
    monkeypatch.setattr(cli, "boot", lambda: kernel)
    monkeypatch.setattr(sys, "argv", ["hz", *args])
    with pytest.raises(SystemExit):
        cli.main()


@pytest.fixture
def checking(monkeypatch):
    """The check on, no CI variable."""
    for variable in _update.CI_VARIABLES:
        monkeypatch.delenv(variable, raising=False)


@pytest.mark.usefixtures("checking")
def test_the_entry_point_checks_once_an_hour_and_caches_the_answer(monkeypatch, cache):
    kernel, source = make_kernel(), FakeSource()
    kernel.bind(ReleaseSource, source)
    run_main(monkeypatch, kernel, "clockwork", "wind")
    run_main(monkeypatch, kernel, "clockwork", "wind")
    assert source.asked == [_update.TIMEOUT]
    assert cache.latest == "9.0.0"


@pytest.mark.usefixtures("checking")
def test_a_failed_check_is_not_retried_within_the_hour(monkeypatch, cache):
    kernel, source = make_kernel(), FakeSource(OSError("offline"))
    kernel.bind(ReleaseSource, source)
    run_main(monkeypatch, kernel, "clockwork", "wind", "--json")
    run_main(monkeypatch, kernel, "clockwork", "wind")
    assert len(source.asked) == 1
    assert cache.error == "OSError: offline"
    assert cache.latest is None


@pytest.mark.usefixtures("checking")
def test_the_check_runs_again_after_an_hour(monkeypatch, cache):
    cache.remember("8.0.0", datetime.now(UTC) - timedelta(hours=1, minutes=1))
    kernel, source = make_kernel(), FakeSource()
    kernel.bind(ReleaseSource, source)
    run_main(monkeypatch, kernel, "--version")
    assert (len(source.asked), cache.latest) == (1, "9.0.0")


@pytest.mark.usefixtures("checking")
@pytest.mark.parametrize("ci", [False, True])
def test_no_check_when_the_setting_is_off_or_in_ci(monkeypatch, ci):
    if ci:
        monkeypatch.setenv("CI", "true")
    kernel, source = make_kernel(update_check=ci), FakeSource()
    kernel.bind(ReleaseSource, source)
    run_main(monkeypatch, kernel, "clockwork", "wind")
    assert source.asked == []
