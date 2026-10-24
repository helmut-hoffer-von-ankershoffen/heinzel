"""The update notice and the hourly check (ADR 0031) through the CLI head, with a fake package source. Offline."""

import json
import sys
import urllib.error
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from heinzel import Module, Prompter, Settings, __version__
from heinzel.heads import cli
from heinzel.heads.cli import _setup, _update, build
from heinzel.kernel import Kernel, ModuleRecord, Origin, ReleaseCache, ReleaseSource, discover
from heinzel.modules.system import _github
from heinzel.modules.system import _service as system_service

pytestmark = pytest.mark.integration

runner = CliRunner()
NOTICE = f"hz 9.0.0 is available (installed {__version__}): hz system update\n{_update.WHATS_NEW}"
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

    @module.operation(audience="everyday")  # listed on the start screen
    def wind() -> str:
        """Wind them."""
        return "wound"

    return module


def echo_module() -> Module:
    """A module that collapses into one command taking an argument, with a setup step (ADR 0032)."""
    module = Module("echo", "Echoes.")

    @module.operation
    def echo(text: str) -> str:
        """Echo the text."""
        return text

    @module.setup
    def setup(prompter: Prompter) -> None:
        """Say hi."""
        prompter.say("hi")

    return module


def make_kernel(*, update_check: bool = True, disabled: frozenset[str] = frozenset()) -> Kernel:
    records = [
        *discover(plugins=False)[0],
        ModuleRecord("clockwork", plugin_module(), CLOCK),
        ModuleRecord("echo", echo_module(), CLOCK),
    ]
    return Kernel(Settings(update_check=update_check, disabled_modules=disabled), records)


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


def test_a_collapsed_module_with_a_setup_step_gets_exactly_one_notice(app):
    result = runner.invoke(app, ["echo", "hi"])
    assert result.stdout.strip() == "hi"
    assert result.stderr.strip() == NOTICE


@pytest.mark.parametrize("options", [["--json"], ["--format", "json"]])
@pytest.mark.parametrize(("command", "answer"), [(["clockwork", "wind"], "wound"), (["echo", "hi"], "hi")])
def test_json_never_gets_the_notice(app, options, command, answer):
    result = runner.invoke(app, [*command, *options])
    assert json.loads(result.stdout) == answer
    assert NOTICE not in result.output


def test_setup_of_one_module_is_followed_by_the_notice(app, monkeypatch):
    monkeypatch.setattr(_setup, "has_terminal", lambda: True)
    result = runner.invoke(app, ["setup", "echo"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().endswith("echo: done")
    assert result.stderr.strip() == NOTICE


@pytest.mark.usefixtures("human")
def test_setup_of_every_module_leaves_the_news_to_doctor(monkeypatch, tmp_path):
    monkeypatch.setattr(_setup, "has_terminal", lambda: True)
    monkeypatch.chdir(tmp_path)
    kernel = make_kernel(disabled=frozenset({"org"}))  # only echo's optional step, not org's questions
    result = runner.invoke(build(kernel), ["setup"], input="\n", env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    doctor = " ".join(result.stdout[result.stdout.index("Heinzelmännchen is") :].replace("│", " ").split())
    assert "release ✔ up hz 9.0.0 is" in doctor  # its release check names the newer release
    assert NOTICE not in result.stderr


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
    start = screen.index("Plugins")
    plugins = screen[start : screen.index("╰", start)]  # the panel, wherever it sits among the others
    assert "heinzel-clockwork 1.2.3" in plugins
    assert "https://github.com/someone/heinzel-clockwork" in plugins
    assert " ".join(NOTICE.split()) in " ".join(screen.split())
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
    assert text.startswith(f"You're on hz {__version__}; hz 9.0.0 is available: hz system update. {_update.WHATS_NEW}")
    assert "Changelog" in text
    assert NOTICE not in result.stderr
    as_json = json.loads(runner.invoke(app, ["system", "changelog", "--json"]).stdout)
    assert as_json.startswith("# Changelog")  # unchanged: the header is for people only


def fake_releases(monkeypatch, answer: object) -> list[str]:
    """The GitHub releases API answering `answer` (raising it if it is an exception); the suite stays offline."""
    asked: list[str] = []
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "HZ_SYSTEM_CHANGELOG__GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    def get_json(url, token, timeout):
        assert (token, timeout) == (None, system_service.RELEASE_TIMEOUT)  # no token unless one is set (ADR 0031)
        asked.append(url)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(_github, "get_json", get_json)
    return asked


NEWER = [
    {
        "tag_name": "v9.0.0",
        "published_at": "2026-10-20T08:00:00Z",
        "html_url": "https://github.com/helmut-hoffer-von-ankershoffen/heinzel/releases/tag/v9.0.0",
        "body": "### Features\n\n- \u202ebrand new\u202c things",
    },
    {"tag_name": f"v{__version__}", "body": "what you have"},
]


def test_changelog_newer_shows_what_newer_releases_bring(app, monkeypatch):
    asked = fake_releases(monkeypatch, NEWER)
    result = runner.invoke(app, ["system", "changelog", "--newer"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert "You're on hz" not in result.stdout  # the header is for the installed changelog
    assert "hz 9.0.0 (2026-10-20)" in result.stdout
    assert "brand new things" in result.stdout
    assert "\u202e" not in result.stdout
    assert "what you have" not in result.stdout
    assert NOTICE not in result.stderr
    assert asked == [f"{_github.GITHUB_API}/repos/helmut-hoffer-von-ankershoffen/heinzel/releases?per_page=30&page=1"]
    as_json = json.loads(runner.invoke(app, ["system", "changelog", "--newer", "--json"]).stdout)
    assert as_json == [
        {
            "version": "9.0.0",
            "date": "2026-10-20",
            "notes": "### Features\n\n- brand new things",
            "url": "https://github.com/helmut-hoffer-von-ankershoffen/heinzel/releases/tag/v9.0.0",
        }
    ]


def test_changelog_newer_says_when_nothing_is_newer(app, monkeypatch):
    fake_releases(monkeypatch, NEWER[1:])
    result = runner.invoke(app, ["system", "changelog", "--newer"], env={"COLUMNS": "200"})
    assert (result.exit_code, result.stdout.strip()) == (
        0,
        f"hz {__version__} is the latest release; nothing newer to show.",
    )
    assert json.loads(runner.invoke(app, ["system", "changelog", "--newer", "--json"]).stdout) == []


def test_changelog_newer_offline_says_so_and_fails(app, monkeypatch):
    fake_releases(monkeypatch, urllib.error.URLError("nodename nor servname provided"))
    result = runner.invoke(app, ["system", "changelog", "--newer"], env={"COLUMNS": "300"})
    assert result.exit_code == 1
    assert "Could not ask GitHub for the releases newer than" in result.stderr
    assert "nodename nor servname provided" in result.stderr
    assert result.stdout == ""


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
