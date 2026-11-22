"""`hz app install --restart`: quit an open app only when it needs the update, reopen it, never force it.

Offline and without a real app: the Apple-event quit, the process check and `open` are fakes. What a real macOS does
with the Apple event (an Automation prompt, an unsaved-changes dialog) needs a real run on a Mac.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from rich.console import Console

from heinzel import __version__
from heinzel.heads import _desktop_installation as installer
from heinzel.heads.cli import _app

pytestmark = pytest.mark.unit

BUNDLE = Path("/Applications/Heinzelmaennchen.app")


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def quit_with(*, open_for: float, run=None, wait: float = 20.0):
    """`quit_app` where the app stays open for `open_for` seconds after the quit was asked; the commands it ran."""
    clock, commands = Clock(), []

    def fake_run(command, **kwargs):
        commands.append(command)
        if run is not None:
            raise run
        return SimpleNamespace(returncode=0)

    gone = installer.quit_app(
        BUNDLE, wait=wait, clock=clock, sleep=clock.sleep, run=fake_run, running=lambda _bundle: clock() < open_for
    )
    return gone, commands


def test_an_app_that_quits_in_time_is_gone_after_one_apple_event_and_nothing_else():
    gone, commands = quit_with(open_for=3.0)
    assert gone is True
    [command] = commands
    assert command[0] == "/usr/bin/osascript"
    assert command[-1] == str(BUNDLE)  # the path is an argument, never part of the script
    assert "tell application (item 1 of argv) to quit" in command


def test_an_app_still_open_after_the_wait_is_reported_open_and_never_signalled():
    gone, commands = quit_with(open_for=1e9, wait=20.0)
    assert gone is False
    assert [command[0] for command in commands] == ["/usr/bin/osascript"]  # no kill, no signal, no second try


def test_an_app_that_cannot_be_asked_still_gets_the_wait_and_is_then_deferred():
    gone, _ = quit_with(open_for=1e9, run=OSError("no osascript"))
    assert gone is False


def test_a_quit_is_asked_for_exactly_the_wait_the_update_expects():
    assert installer.QUIT_SECONDS == 20.0


def patched(monkeypatch, *, installed, open_now, quit_ok=True):
    quits = []
    monkeypatch.setattr(installer, "desktop_to_update", lambda: installed)
    monkeypatch.setattr(installer, "is_open", lambda bundle: open_now)
    monkeypatch.setattr(installer, "quit_app", lambda bundle: quits.append(bundle) or quit_ok)
    return quits


def test_only_an_open_app_that_needs_the_update_is_quit(monkeypatch):
    error = Console(record=True, width=120)
    old = (BUNDLE, "0.0.1")
    for installed, open_now in ((None, True), ((BUNDLE, __version__), True), (old, False)):
        quits = patched(monkeypatch, installed=installed, open_now=open_now)
        assert _app._quit_for_update(error) is False
        assert quits == []  # not installed, already current, or not open: nothing is quit
    quits = patched(monkeypatch, installed=old, open_now=True)
    assert _app._quit_for_update(error) is True
    assert quits == [BUNDLE]


def test_an_open_genuine_app_hz_did_not_record_is_quit_and_reopened(monkeypatch):
    # From a download, an older hz or the app's own updater: no release on record, so it needs the update.
    quits = patched(monkeypatch, installed=(BUNDLE, None), open_now=True)
    assert _app._quit_for_update(Console(record=True, width=120)) is True
    assert quits == [BUNDLE]


def test_an_app_that_will_not_quit_ends_the_install_with_the_deferred_code_and_stays_open(monkeypatch):
    error = Console(record=True, width=120)
    patched(monkeypatch, installed=(BUNDLE, "0.0.1"), open_now=True, quit_ok=False)
    with pytest.raises(typer.Exit) as exit_:
        _app._quit_for_update(error)
    assert exit_.value.exit_code == _app.RESTART_DEFERRED == 75
    text = error.export_text()
    assert "is still open after 20 s, so it was not replaced and was left open." in text
    assert "Quit it, then run hz app install again." in text


def install_with(monkeypatch, *, restart: bool, was_open: bool):
    """Run `_install_app` where the installer succeeds; whether the app was opened again."""
    opened = []
    monkeypatch.setattr(_app, "_quit_for_update", lambda error: was_open)
    monkeypatch.setattr(_app, "desktop_executable", lambda kernel: Path("/hz"))
    monkeypatch.setattr(
        _app, "_plan", lambda error, keep_quarantine: (SimpleNamespace(close=lambda complete: None), BUNDLE.parent)
    )
    monkeypatch.setattr(_app, "_print_notes", lambda steps, notes: None)
    monkeypatch.setattr(_app, "_located", lambda error: BUNDLE)
    monkeypatch.setattr(_app, "_launch", opened.append)
    monkeypatch.setattr(installer, "install_desktop", lambda *args, **kwargs: "updated")
    _app._install_app(
        SimpleNamespace(), Console(record=True, width=120), Console(record=True, width=120), restart=restart
    )
    return opened


def test_the_app_is_reopened_only_after_a_restart_install_that_found_it_open(monkeypatch):
    assert install_with(monkeypatch, restart=True, was_open=True) == [BUNDLE]
    assert install_with(monkeypatch, restart=True, was_open=False) == []  # it was closed: stays closed
    assert install_with(monkeypatch, restart=False, was_open=True) == []  # no --restart: never quit, never opened


def test_a_reopen_that_fails_is_told_and_the_update_still_succeeded(monkeypatch):
    def refuse(location):
        raise _app._RefusedError(_app.say.open_failed(location))

    monkeypatch.setattr(_app, "_launch", refuse)
    error = Console(record=True, width=120)
    _app._reopen(error, BUNDLE)
    assert "open" in error.export_text()
