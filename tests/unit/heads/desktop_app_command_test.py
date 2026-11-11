"""The desktop update command and the packaged app's automatic refresh policy."""

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from rich.console import Console
from typer.testing import CliRunner

from heinzel import HeinzelError, Settings, __version__
from heinzel.heads import _desktop_installation as installer
from heinzel.heads import _desktop_messages as say
from heinzel.heads.cli import _app, build
from heinzel.kernel import CorePin

pytestmark = pytest.mark.unit


@pytest.fixture
def update_command(monkeypatch, make_kernel, tmp_path):
    executable = tmp_path / "hz"
    install = Mock()
    monkeypatch.setattr(installer, "install_desktop", install)
    monkeypatch.setattr(_app, "desktop_executable", lambda _kernel: executable)
    monkeypatch.delenv("HZ_DEBUG", raising=False)
    app = _app.desktop_group(make_kernel(), Console(), Console(stderr=True))
    return app, executable, install


def test_update_is_listed_and_help_distinguishes_cli_updates(make_kernel):
    app = build(make_kernel())
    runner = CliRunner()
    group_help = runner.invoke(app, ["app", "--help"])
    command_help = runner.invoke(app, ["app", "update", "--help"])
    assert group_help.exit_code == command_help.exit_code == 0
    assert "update" in group_help.stdout
    assert "hz system update" in command_help.stdout


@pytest.mark.parametrize(
    ("state", "code", "expected"),
    [
        ("updated", 0, f"Updated the Heinzelmännchen desktop app to {__version__}."),
        ("current", 0, f"The desktop app already matches hz {__version__}."),
        ("newer", 0, f"The desktop app is newer than hz {__version__}."),
        ("absent", 1, "No managed Heinzelmännchen desktop app is installed.\nRun hz app install to install it."),
        ("deferred", 1, "The Heinzelmännchen desktop app is in use.\nQuit it, then run hz app update again."),
    ],
)
def test_update_only_targets_the_running_cli_version(update_command, state, code, expected, caplog):
    app, executable, install = update_command
    install.return_value = state
    with caplog.at_level(logging.INFO, logger="heinzel.desktop"):
        result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == code
    assert (result.stderr if code else result.stdout).strip() == expected
    assert (result.stdout if code else result.stderr) == ""
    install.assert_called_once()
    assert install.call_args.args == (__version__, executable)
    assert install.call_args.kwargs == {
        "update_only": True,
        "explicit_update": True,
        "reporter": install.call_args.kwargs["reporter"],
    }
    events = [record for record in caplog.records if record.message == "desktop.update.run"]
    assert [record.phase for record in events] == ["start", "end"]
    assert events[-1].outcome == state


def test_update_rejects_a_foreign_copy_without_offering_replacement(update_command):
    app, _executable, install = update_command
    path = Path("/Applications") / installer.BUNDLE_NAME
    install.side_effect = installer.DesktopInstallationError("diagnosis", say.foreign_copy(path, everyone=True))

    result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 1
    assert "that hz did not install" in result.stderr
    assert "Run hz app update --details" in result.stderr
    assert "diagnosis" not in result.stderr
    assert "--replace" not in result.stderr
    install.assert_called_once()


def test_update_points_a_same_version_binding_conflict_at_explicit_install(update_command):
    app, executable, install = update_command
    install.side_effect = installer.DesktopBindingMismatchError("/other/bin/hz", executable)
    runner = CliRunner()

    result = runner.invoke(app, ["update"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        "The desktop app uses a different installation of hz.",
        "Run hz app install from this CLI installation. Run hz app update --details for the exact command.",
    ]
    install.assert_called_once()

    details = runner.invoke(app, ["update", "--details"])
    assert details.exit_code == 1
    assert details.stderr.splitlines()[-1] == f"{executable} app install"


def test_update_details_show_the_diagnosis_for_a_failed_transaction(update_command):
    app, _executable, install = update_command
    install.side_effect = OSError("internal diagnosis")

    result = CliRunner().invoke(app, ["update", "--details"])

    assert result.exit_code == 1
    assert result.stderr.splitlines() == [
        "Desktop update failed.",
        "Run hz app update to try again.",
        "internal diagnosis",
    ]


def test_a_failed_download_retries_the_update_command(update_command, caplog):
    app, _executable, install = update_command
    install.side_effect = HeinzelError("Download failed. Check your connection, then run hz app install again.")
    with caplog.at_level(logging.INFO, logger="heinzel.desktop"):
        result = CliRunner().invoke(app, ["update"])

    assert result.exit_code == 1
    assert "Check your connection, then run hz app update again." in result.stderr
    assert "hz app install" not in result.stderr
    events = [record for record in caplog.records if record.message == "desktop.update.run"]
    assert [record.phase for record in events] == ["start", "end"]
    assert events[-1].outcome == "failed"


def test_an_interrupted_update_can_be_retried(update_command):
    app, _executable, install = update_command
    install.side_effect = KeyboardInterrupt
    result = CliRunner().invoke(app, ["update"])
    assert result.exit_code == 130
    assert "Run hz app update to try again." in result.stderr


@pytest.mark.parametrize(
    ("settings", "pin", "allowed"),
    [
        (Settings(auto_update=True, update_check=True), CorePin("index", "heinzel"), True),
        (Settings(auto_update=False, update_check=True), CorePin("index", "heinzel"), False),
        (Settings(auto_update=True, update_check=False), CorePin("index", "heinzel"), False),
        (Settings(auto_update=True, update_check=True, update_channel="edge"), CorePin("index", "heinzel"), False),
        (Settings(auto_update=True, update_check=True), CorePin("index", "heinzel==0.9.21", ref="==0.9.21"), False),
    ],
)
def test_exit_refresh_respects_current_settings_and_core_source(monkeypatch, tmp_path, settings, pin, allowed):
    executable = tmp_path / "hz"
    config = SimpleNamespace(reread=lambda: SimpleNamespace(settings=lambda *_args: settings))
    installation = SimpleNamespace(core_pin=lambda: pin, tool_entrypoints=lambda: (executable,))
    kernel = SimpleNamespace(config=config, resolve=lambda _service: installation)
    install = Mock(side_effect=["deferred", "updated"])
    monkeypatch.setattr(_app.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(_app.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(installer, "install_desktop", install)

    _app._refresh_after_exit(kernel)

    expected = call(__version__, executable, update_only=True)
    assert install.call_args_list == ([expected, expected] if allowed else [])
