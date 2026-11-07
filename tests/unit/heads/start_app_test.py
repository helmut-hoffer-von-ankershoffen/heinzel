"""The one-command start asks for the desktop app first, remembers declines and never hangs without a terminal."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from heinzel.heads import _start_messages as words
from heinzel.heads import bootstrap

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("message", words.FAILURES)
def test_every_start_failure_is_two_short_lines_that_end_with_a_command(message):
    lines = message.splitlines()
    assert len(lines) <= 2
    assert all(len(line) <= words.MAX_LINE for line in lines)
    assert not any(word in message.lower() for word in words.BANNED_WORDS)
    if len(lines) == 2:
        assert " hz " in f" {lines[1]}"


def test_start_flags_are_split_from_delegated_arguments():
    assert bootstrap._start_options(["--yes", "--no-app"]) == ([], {"--yes", "--no-app"})
    assert bootstrap._start_options(["system", "doctor", "--ask"]) == (["system", "doctor"], {"--ask"})


@pytest.fixture
def silicon(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "_supports_app", lambda: True)
    monkeypatch.setattr(bootstrap, "_start_state", lambda: tmp_path / "state/start.json")
    monkeypatch.setattr(bootstrap, "_human_console", lambda: SimpleNamespace(print=lambda *a, **k: None))


def test_flags_decide_without_a_question(silicon):
    assert bootstrap._app_choice([], {"--yes"}, interactive=False) is True
    assert bootstrap._app_choice([], {"--yes", "--no-app"}, interactive=True) is False
    assert bootstrap._app_choice([], set(), interactive=False) is False
    assert bootstrap._app_choice(["doctor"], {"--yes"}, interactive=True) is False


def test_other_platforms_never_get_the_app(monkeypatch):
    monkeypatch.setattr(bootstrap, "_supports_app", lambda: False)
    monkeypatch.setattr(bootstrap, "_human_console", lambda: SimpleNamespace(print=lambda *a, **k: None))
    assert bootstrap._app_choice([], {"--yes"}, interactive=True) is False


def test_a_decline_is_remembered_and_ask_asks_again(silicon, monkeypatch):
    from heinzel.heads import _desktop_installation
    from heinzel.heads.cli import _setup

    asked = []
    answers = iter([False, True])

    class Prompter:
        def __init__(self, _console):
            pass

        def confirm(self, question, *, default):
            asked.append((question, default))
            return next(answers)

    monkeypatch.setattr(_setup, "ConsolePrompter", Prompter)
    monkeypatch.setattr(_desktop_installation, "desktop_location", lambda: None)
    assert bootstrap._app_choice([], set(), interactive=True) is False
    assert asked[0][1] is True
    assert "Install the desktop app too?" in asked[0][0]
    assert bootstrap._app_choice([], set(), interactive=True) is False  # remembered: no second question
    assert len(asked) == 1
    assert bootstrap._app_choice([], {"--ask"}, interactive=True) is True
    assert len(asked) == 2


def test_an_installed_app_is_not_asked_about_again(silicon, monkeypatch):
    from heinzel.heads import _desktop_installation

    monkeypatch.setattr(_desktop_installation, "desktop_location", lambda: "/Applications/Heinzelmännchen.app")
    assert bootstrap._app_choice([], set(), interactive=True) is True


@pytest.mark.parametrize(("outcome", "expected"), [((0, True), True), ((1, True), False), ((0, False), False)])
def test_app_install_requires_a_green_doctor_and_falls_back_with_two_lines(monkeypatch, capsys, outcome, expected):
    code, opens = outcome

    def run(command, **_kwargs):
        if "install" in command:
            return SimpleNamespace(returncode=code)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"opens": opens}))

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    assert bootstrap._install_app(Path("/x/bin/hz"), {}) is expected
    assert (words.APP_DOWNLOAD_FAILED in capsys.readouterr().err) is (not expected)


def test_a_failing_app_installer_never_stops_the_start(monkeypatch, capsys, tmp_path):
    def run(*_a, **_k):
        msg = "hz"
        raise subprocess.TimeoutExpired(msg, 1)

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    assert bootstrap._install_app(tmp_path / "bin/hz", {}) is False
    assert "Run hz app install later" in capsys.readouterr().err


def test_non_interactive_start_installs_and_ends_with_a_status(monkeypatch, capsys, tmp_path):
    executable = tmp_path / "bin/hz"
    monkeypatch.setattr(bootstrap.sys, "argv", ["heinzel"])
    monkeypatch.setattr(bootstrap, "_interactive_launch", lambda _args: False)
    monkeypatch.setattr(bootstrap, "_ensure_app", lambda **_k: executable)
    monkeypatch.setattr(bootstrap.os, "execve", lambda *_a: pytest.fail("A script must never get a foreground server"))
    bootstrap.main()
    out = capsys.readouterr().out
    assert "Nothing started." in out
    assert "hz start --yes" in out


def test_yes_installs_services_and_app_but_does_not_open_without_a_terminal(monkeypatch, capsys, tmp_path):
    executable = tmp_path / "bin/hz"
    calls = []
    monkeypatch.setattr(bootstrap.sys, "argv", ["heinzel", "--yes"])
    monkeypatch.setattr(bootstrap.sys.stdout, "isatty", lambda: False)
    monkeypatch.setattr(bootstrap, "_supports_app", lambda: True)
    monkeypatch.setattr(bootstrap, "_ensure_app", lambda **_k: executable)
    monkeypatch.setattr(bootstrap, "_app_environment", lambda _e: {})
    monkeypatch.setattr(bootstrap, "_install_app", lambda *_a: calls.append("app") or True)
    monkeypatch.setattr(bootstrap, "_workspace_command", lambda *_a: calls.append(_a[3]) or ["app", "open"])
    monkeypatch.setattr(bootstrap, "_install_services", lambda _e, choices, _env: calls.append(len(choices)))
    monkeypatch.setattr(bootstrap, "_prepare_profile", lambda *_a, **_k: pytest.fail("Names are left to the app"))
    monkeypatch.setattr(bootstrap.os, "execve", lambda *_a: pytest.fail("No open without a terminal"))
    bootstrap.main()
    assert calls == ["app", True, 3]
    assert "Heinzelmännchen is running" in capsys.readouterr().err
