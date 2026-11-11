"""hz app install's two-line failures and its forget prompt, and hz app open, with the installer and `open` faked.

Nothing here touches a real Applications folder or launches anything.
"""

import io
import subprocess
from pathlib import Path

import pytest
import typer
from rich.console import Console

from heinzel.heads import _desktop_installation as desktop
from heinzel.heads import _desktop_messages as say
from heinzel.heads import _desktop_seal as seal
from heinzel.heads.cli import _app

pytestmark = pytest.mark.unit

APP = Path("/Applications/Heinzelmaennchen.app")


def _consoles() -> tuple[Console, Console, io.StringIO, io.StringIO]:
    out, err = io.StringIO(), io.StringIO()
    return Console(file=out, width=300), Console(file=err, width=300), out, err


def _exit_code(call) -> int:
    try:
        call()
    except typer.Exit as exit_:
        return exit_.exit_code
    return 0


@pytest.fixture
def installer(monkeypatch):
    """A fake install_desktop that fails with a leftover record until it may forget it; records every call."""
    calls: list[dict[str, object]] = []

    def install(_version, _executable, **options):
        calls.append(options)
        if not options.get("forget"):
            problem = say.leftover_install(Path("/Applications"), everyone=True)
            msg = "diagnosis: ~/.config/heinzel/desktop-installation.json but no app"
            raise desktop.LeftoverInstallError(msg, problem)
        options["notes"].append("Set aside  old install record (backup)")
        return "installed"

    monkeypatch.setattr(desktop, "install_desktop", install)
    monkeypatch.setattr(_app, "desktop_executable", lambda _kernel: Path("/usr/local/bin/hz"))
    monkeypatch.setattr(_app, "_located", lambda _error: APP)
    monkeypatch.delenv("HZ_DEBUG", raising=False)
    return calls


def test_a_leftover_install_fails_in_two_plain_lines_without_a_terminal(installer, monkeypatch):
    monkeypatch.setattr(_app, "_interactive", lambda: False)
    output, error, _out, err = _consoles()
    assert _exit_code(lambda: _app._install_app(None, output, error)) == 1
    lines = err.getvalue().splitlines()
    assert lines == [
        (
            "An earlier install of Heinzelmännchen is recorded, but the app is gone from /Applications (for "
            "everyone on this Mac)."
        ),
        "Run hz app install --yes, or hz app uninstall, to forget it.",
    ]
    assert len(installer) == 1


def test_details_adds_the_diagnosis(installer, monkeypatch):
    monkeypatch.setattr(_app, "_interactive", lambda: False)
    output, error, _out, err = _consoles()
    assert _exit_code(lambda: _app._install_app(None, output, error, details=True)) == 1
    assert "desktop-installation.json" in err.getvalue()


def test_in_a_terminal_it_asks_once_and_continues(installer, monkeypatch):
    monkeypatch.setattr(_app, "_interactive", lambda: True)
    asked: list[str] = []
    monkeypatch.setattr(typer, "confirm", lambda text, default: asked.append(text) or default)
    output, error, _out, err = _consoles()
    assert _exit_code(lambda: _app._install_app(None, output, error)) == 0
    assert asked == [say.FORGET_PROMPT]
    assert [call.get("forget") for call in installer] == [False, True]
    assert "ok Set aside  old install record (backup)" in err.getvalue()


def test_answering_no_changes_nothing(installer, monkeypatch):
    monkeypatch.setattr(_app, "_interactive", lambda: True)
    monkeypatch.setattr(typer, "confirm", lambda _text, default: False)
    output, error, _out, _err = _consoles()
    assert _exit_code(lambda: _app._install_app(None, output, error)) == 1
    assert len(installer) == 1


def test_yes_forgets_without_asking(installer, monkeypatch):
    monkeypatch.setattr(typer, "confirm", lambda *_args, **_kwargs: pytest.fail("must not ask"))
    output, error, _out, _err = _consoles()
    assert _exit_code(lambda: _app._install_app(None, output, error, yes=True)) == 0
    assert [call.get("forget") for call in installer] == [True]


@pytest.fixture
def launcher(monkeypatch):
    """Fakes for the installed location, the installer, the seal check and macOS `open`."""
    state: dict[str, object] = {"location": APP, "opens": True, "quarantine": "none", "open_code": 0}
    launched: list[list[str]] = []
    installs: list[bool] = []

    def location():
        return state["location"]

    def install(*_args, **_kwargs):
        installs.append(True)
        state["location"] = APP

    def inspect(bundle):
        return seal.SealReport(bundle, None if state["opens"] else "invalid signature", state["quarantine"], "ok")

    def run(command, **_kwargs):
        launched.append(command)
        return subprocess.CompletedProcess(command, state["open_code"], "", "")

    monkeypatch.setattr(desktop, "desktop_location", location)
    monkeypatch.setattr(_app, "_install_app", install)
    monkeypatch.setattr(seal, "inspect_bundle", inspect)
    monkeypatch.setattr(subprocess, "run", run)
    return state, launched, installs


def test_open_launches_the_installed_app_and_says_which(launcher):
    _state, launched, installs = launcher
    output, error, out, _err = _consoles()
    assert _app._open_app(None, output, error) == APP
    assert launched == [["/usr/bin/open", str(APP)]]
    assert not installs
    assert out.getvalue().strip() == f"Opened {APP}"


def test_open_installs_first_when_the_app_is_missing(launcher):
    state, launched, installs = launcher
    state["location"] = None
    output, error, out, _err = _consoles()
    assert _app._open_app(None, output, error) == APP
    assert installs == [True]
    assert out.getvalue().splitlines()[0] == "Not installed yet, installing…"
    assert launched == [["/usr/bin/open", str(APP)]]


def test_open_never_launches_a_copy_macos_would_call_damaged(launcher):
    state, launched, _installs = launcher
    state["opens"] = False
    output, error, _out, err = _consoles()
    assert _exit_code(lambda: _app._open_app(None, output, error)) == 1
    assert not launched
    assert err.getvalue().splitlines() == say.would_not_open(APP).lines()


def test_open_says_macos_asks_once_for_a_quarantined_copy(launcher):
    state, launched, _installs = launcher
    state["quarantine"] = "ours"
    output, error, out, _err = _consoles()
    _app._open_app(None, output, error)
    assert say.asks_once() in out.getvalue()
    assert launched


def test_open_reports_when_macos_refuses(launcher):
    state, _launched, _installs = launcher
    state["open_code"] = 1
    output, error, _out, err = _consoles()
    assert _exit_code(lambda: _app._open_app(None, output, error)) == 1
    assert err.getvalue().splitlines() == say.open_failed(APP).lines()


SAMPLE = Path("/Users/someone/Applications")
PROBLEMS = [
    say.leftover_install(SAMPLE, everyone=False),
    say.foreign_copy(SAMPLE / "Heinzelmaennchen.app", everyone=False),
    say.trash_failed(SAMPLE / "Heinzelmaennchen.app", everyone=False),
    say.app_running(SAMPLE),
    say.app_running(SAMPLE, command="uninstall"),
    say.interrupted(SAMPLE, everyone=False),
    say.busy(),
    say.would_not_open(SAMPLE / "Heinzelmaennchen.app"),
    say.open_failed(SAMPLE / "Heinzelmaennchen.app"),
]


@pytest.mark.parametrize("problem", PROBLEMS)
def test_every_failure_is_two_plain_lines_ending_in_something_to_do(problem):
    lines = problem.lines()
    assert len(lines) == 2
    for line in lines:
        assert "\n" not in line
        assert len(line.replace(str(SAMPLE), "")) <= say.MAX_LINE
        assert not any(word in line.lower() for word in say.BANNED_WORDS), line
    assert any(verb in lines[1] for verb in ("Run hz", "Quit it", "Move it", "Wait for it")), lines[1]


def _open_events(caplog) -> list[tuple[str, str, object]]:
    return [(r.event, r.phase, getattr(r, "outcome", None)) for r in caplog.records if r.name == "heinzel.desktop"]


def test_open_logs_check_and_launch(launcher, caplog):
    caplog.set_level("INFO", logger="heinzel.desktop")
    output, error, _out, _err = _consoles()
    _app._open_app(None, output, error)
    assert _open_events(caplog) == [
        ("desktop.open.check", "start", None),
        ("desktop.open.check", "end", "ok"),
        ("desktop.open.launch", "start", None),
        ("desktop.open.launch", "end", "ok"),
    ]
    assert all(r.path == str(APP) for r in caplog.records)


def test_open_logs_why_it_did_not_launch(launcher, caplog):
    state, _launched, _installs = launcher
    state["opens"] = False
    caplog.set_level("INFO", logger="heinzel.desktop")
    output, error, _out, _err = _consoles()
    assert _exit_code(lambda: _app._open_app(None, output, error)) == 1
    assert _open_events(caplog) == [
        ("desktop.open.check", "start", None),
        ("desktop.open.check", "end", "error"),
    ]
    check = caplog.records[-1]
    assert (check.happened, check.action) == tuple(say.would_not_open(APP).lines())
