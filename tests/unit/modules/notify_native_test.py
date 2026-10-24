"""Native notifiers (ADR 0041): which one a machine has, and that text reaches it as arguments, never as code."""

import pathlib
import subprocess

import pytest

from heinzel.modules.system.notify._macos import NotifierApp
from heinzel.modules.system.notify._native import (
    APP,
    BODY_LIMIT,
    DENIED,
    HEINZEL,
    TITLE_LIMIT,
    Native,
    native,
    sanitise,
)

pytestmark = pytest.mark.unit

INJECTION = '" & do shell script "touch /tmp/owned" & "'


def which(*found: str):
    return lambda name: f"/usr/bin/{name}" if name in found else None


class Runner:
    def __init__(self, code: int = 0) -> None:
        self.code = code
        self.commands: list[list[str]] = []

    def __call__(self, command, timeout=None):
        self.commands.append(list(command))
        return subprocess.CompletedProcess(command, self.code, "", "no notification server")


def test_sanitise_flattens_cleans_bounds_and_never_starts_like_an_option():
    assert sanitise("Draft\nready\t now", 80) == "Draft ready now"
    assert sanitise("a" + chr(0x202E) + "b" + chr(0) + "c" + chr(0x200B), 80) == "abc"
    assert sanitise("-open https://evil.example.test", 80) == "open https://evil.example.test"
    assert sanitise("[\\-x", 80) == "x"
    long = sanitise("word " * 100, 40)
    assert len(long) == 40
    assert long.endswith("…")


def test_osascript_gets_a_fixed_script_and_the_text_as_its_arguments():
    command = Native("osascript", "/usr/bin/osascript").argv(INJECTION, "body")
    scripts = [command[i + 1] for i, arg in enumerate(command) if arg == "-e"]
    assert scripts == [
        "on run argv",
        "display notification (item 2 of argv) with title (item 1 of argv) subtitle (item 3 of argv)",
        "end run",
    ]
    assert command[-3:] == [APP, "body", INJECTION]  # the title is one argument, never part of a script
    assert not any(INJECTION in script for script in scripts)


def test_notify_send_ends_options_and_escapes_the_markup_its_body_may_be_read_as():
    command = Native("notify-send", "/usr/bin/notify-send").argv("-u critical", "<b>a & b</b>")
    assert command == ["/usr/bin/notify-send", f"--app-name={APP}", "--", "u critical", "&lt;b&gt;a &amp; b&lt;/b&gt;"]


def test_long_text_is_bounded():
    command = Native("notify-send", "/usr/bin/notify-send").argv("t" * 500, "b" * 500)
    assert len(command[-2]) == TITLE_LIMIT
    assert len(command[-1]) == BODY_LIMIT


def app(tmp_path, *, bundled: bool, said: str = "posted") -> NotifierApp:
    bundle = tmp_path / "HeinzelNotifier.app"
    if bundled:
        (bundle / "Contents" / "MacOS").mkdir(parents=True)
        (bundle / "Contents" / "MacOS" / "notifier").write_bytes(b"app")

    def runner(command, timeout=None):
        if "--stdout" in command:
            with pathlib.Path(command[command.index("--stdout") + 1]).open("w", encoding="utf-8") as out:
                out.write(said)
        return subprocess.CompletedProcess(command, 0, "", "")

    return NotifierApp(tmp_path / "home", bundled=bundle, runner=runner)


def test_on_macos_heinzel_notifier_comes_first_and_osascript_last(tmp_path):
    first = native("darwin", which("osascript"), {}, app=app(tmp_path, bundled=True))
    assert (first.name, first.clicks) == (HEINZEL, True)
    assert "Allow notifications when mirroring or sharing the display" in first.fix
    fallback = native("darwin", which("osascript"), {}, app=app(tmp_path / "bare", bundled=False))
    assert (fallback.name, fallback.clicks) == ("osascript", False)
    assert "Script Editor" in fallback.fix
    assert native("darwin", which(), {}, app=app(tmp_path / "none", bundled=False)).name is None


def test_heinzel_notifier_installs_itself_on_first_use_and_reports_a_refusal(tmp_path):
    shown = native("darwin", which(), {}, app=app(tmp_path, bundled=True))
    assert shown.post("Draft ready", "Read it.", url="http://127.0.0.1:8737/") is None
    assert shown.app is not None
    assert shown.app.path.is_dir()
    denied = native("darwin", which(), {}, app=app(tmp_path / "denied", bundled=True, said="denied"))
    assert denied.post("Draft ready", "Read it.") == DENIED
    assert "System Settings > Notifications > Heinzel Notifier" in DENIED


def test_each_platform_picks_its_best_notifier():
    assert native("linux", which("notify-send"), {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/bus"}).name == (
        "notify-send"
    )
    headless = native("linux", which("notify-send"), {})
    assert headless.name is None
    assert "headless" in headless.detail
    assert "libnotify" in native("linux", which(), {"DISPLAY": ":0"}).fix
    assert native("win32", which(), {}).name is None


def test_post_runs_the_command_and_reports_a_refusal():
    runner = Runner()
    assert Native("notify-send", "/usr/bin/notify-send", runner=runner).post("t", "b") is None
    assert runner.commands[0][0] == "/usr/bin/notify-send"
    refused = Native("notify-send", "/usr/bin/notify-send", runner=Runner(code=1)).post("t", "b")
    assert refused == "notify-send failed: no notification server"
    assert Native(None, detail="headless").post("t", "b") == "no native notifier: headless"
