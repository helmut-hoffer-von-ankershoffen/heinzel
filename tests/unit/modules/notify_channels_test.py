"""`hz system notify test` says how each way of reaching you stands and what fixes it (ADR 0043)."""

import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from heinzel.modules.system.notify import NotifyService, NotifySettings
from heinzel.modules.system.notify._channels import DaemonState
from heinzel.modules.system.notify._macos import NotifierApp
from heinzel.modules.system.notify._native import HEINZEL, Native

pytestmark = pytest.mark.unit

ORG = {"working_hours": "09-18", "timezone": "Europe/Berlin"}
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))  # a Wednesday, working


def plain(settings, **values: object) -> NotifyService:
    """A service whose native notifier is notify-send, which always posts."""

    def run(command, timeout=None):
        return subprocess.CompletedProcess(command, 0, "", "")

    notifier = Native("notify-send", "/usr/bin/notify-send", detail="through notify-send", runner=run)
    return NotifyService(settings, NotifySettings.model_validate(values), ORG, clock=lambda: NOW, notifier=notifier)


def heinzel(settings, tmp_path, said: str, *, installed: bool = True) -> NotifyService:
    """A service with Heinzel Notifier, whom macOS answers `said` about permission."""
    bundle = tmp_path / "HeinzelNotifier.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    (bundle / "Contents" / "MacOS" / "notifier").write_bytes(b"app")

    def run(command, timeout=None):
        if "--stdout" in command:
            answer = said if "--status" in command else ("denied" if said == "denied" else "posted")
            Path(command[command.index("--stdout") + 1]).write_text(answer, encoding="utf-8")
        return subprocess.CompletedProcess(command, 0 if command[0] != "/usr/bin/xattr" else 1, "", "")

    app = NotifierApp(tmp_path / "home", bundled=bundle, runner=run)
    if installed:
        app.ensure()
    notifier = Native(HEINZEL, str(app.path), clicks=True, app=app)
    return NotifyService(settings, NotifySettings(), ORG, clock=lambda: NOW, notifier=notifier)


def states(tested) -> dict[str, tuple[str, str | None]]:
    return {channel.name: (channel.state, channel.fix) for channel in tested.channels}


def test_a_test_nothing_can_pop_up_says_so_and_what_fixes_each_way(settings, tmp_path):
    tested = heinzel(settings, tmp_path, "denied").test(native=True)
    assert tested.pops_up is False
    assert states(tested) == {
        "web": ("closed", None),
        "native": ("blocked", "open-settings"),
        "daemon": ("unknown", None),
    }


@pytest.mark.macos_specific
def test_a_missing_notifier_and_daemon_name_their_install_commands(settings, tmp_path):
    service = heinzel(settings, tmp_path, "not-determined", installed=False)
    service.daemon = lambda: DaemonState(installed=False, loaded=False, paused=False)
    tested = service.test(native=False)
    native, daemon = tested.channels[1], tested.channels[2]
    assert tested.pops_up is False
    assert (native.state, native.fix, native.command) == ("missing", "install-notifier", "hz system notify install")
    assert (daemon.state, daemon.fix, daemon.command) == ("missing", "install-daemon", "hz system daemon install")


def test_shown_now_it_pops_up(settings, tmp_path):
    tested = heinzel(settings, tmp_path, "authorized").test(native=True)
    assert (tested.pops_up, states(tested)["native"]) == (True, ("shown", None))


def test_an_open_web_app_pops_it_up(settings):
    service = plain(settings)
    service.watched()
    tested = service.test(native=False)
    assert (tested.pops_up, states(tested)["web"]) == (True, ("ready", None))


@pytest.mark.macos_specific
def test_a_ticking_daemon_hands_it_to_a_native_notifier_that_can(settings):
    service = plain(settings)
    service.daemon = lambda: DaemonState(installed=True, loaded=True, paused=False)
    tested = service.test(native=False)
    assert tested.pops_up is True
    assert states(tested) == {"web": ("closed", None), "native": ("ready", None), "daemon": ("ready", None)}


def test_a_paused_daemon_names_how_to_resume_it(settings):
    service = plain(settings)
    service.daemon = lambda: DaemonState(installed=True, loaded=True, paused=True)
    tested = service.test(native=False)
    assert (tested.pops_up, tested.channels[2].state, tested.channels[2].command) == (
        False,
        "off",
        "hz system daemon resume",
    )


def test_native_pop_ups_turned_off_are_a_channel_to_turn_on(settings):
    native = plain(settings, native=False).test(native=False).channels[1]
    assert (native.state, native.fix, native.command) == (
        "off",
        "turn-on",
        "hz system config set system.notify.native true",
    )


@pytest.mark.macos_specific
def test_the_notification_settings_open_on_macos_only(settings):
    service = plain(settings)
    opened: list[list[str]] = []
    service.opener = opened.append
    service.platform = "darwin"
    assert service.open_settings().opened is True
    assert opened == [["open", "x-apple.systempreferences:com.apple.Notifications-Settings.extension"]]
    service.platform = "linux"
    assert service.open_settings().opened is False
    assert len(opened) == 1
