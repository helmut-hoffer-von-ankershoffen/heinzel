"""A packaged app's exit retries updates only under the current automatic policy."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from rich.console import Console
from typer.testing import CliRunner

from heinzel import Settings, __version__
from heinzel.heads import _desktop_installation as installer
from heinzel.heads.cli import _app
from heinzel.kernel import CorePin

pytestmark = pytest.mark.unit


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


@pytest.mark.parametrize("command", ["install", "open", "doctor", "uninstall"])
def test_help_ends_before_the_docstring_sections(command):
    """`--help` is for the person: the docstring's `Raises:` section stays behind the form feed."""
    group = _app.desktop_group(None, Console(width=200), Console(stderr=True, width=200))  # type: ignore[arg-type]
    result = CliRunner().invoke(group, [command, "--help"])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output
    assert "Raises" not in result.output
    assert "typer.Exit" not in result.output


def test_the_cli_reference_shows_no_docstring_sections():
    """The generated reference cuts help where `--help` does, for every command."""
    reference = (Path(__file__).resolve().parents[3] / "docs" / "CLI_REFERENCE.md").read_text(encoding="utf-8")
    assert "### `hz app install`" in reference
    assert "Raises:" not in reference
    assert "typer.Exit" not in reference


def _group(kernel=None):
    output, error = Console(width=200, record=True), Console(stderr=True, width=200, record=True)
    return _app.desktop_group(kernel, output, error), output, error  # type: ignore[arg-type]


@pytest.mark.parametrize("failure", [OSError("disk is read-only"), _app.HeinzelError("locked by another process")])
def test_hidden_refresh_reports_a_failed_update_without_failing_the_exit(monkeypatch, failure):
    """An orderly native exit never fails on its refresh: the reason is printed and the command exits 0."""

    def refuse(_kernel):
        raise failure

    monkeypatch.setattr(_app, "_refresh_after_exit", refuse)
    group, _output, error = _group()
    result = CliRunner().invoke(group, ["refresh"])
    assert result.exit_code == 0, result.output
    printed = error.export_text()
    assert "Desktop update failed." in printed
    assert str(failure) in printed


def test_uninstall_failure_prints_the_notes_and_the_reason_and_exits_1(monkeypatch):
    """A refused removal keeps what the installer set aside visible and names why it failed."""

    def refuse(*, notes, reporter):
        notes.append("Set aside an older copy.")
        msg = "the app is still running"
        raise OSError(msg)

    monkeypatch.setattr(installer, "uninstall_desktop", refuse)
    group, _output, error = _group()
    result = CliRunner().invoke(group, ["uninstall"])
    assert result.exit_code == 1
    printed = error.export_text()
    assert "Set aside an older copy." in printed
    assert "Desktop uninstall failed." in printed
    assert "the app is still running" in printed


def test_uninstall_cancelled_leaves_the_app_and_exits_130(monkeypatch):
    """Ctrl-C during removal says the app was left as it was."""

    def cancel(*, notes, reporter):
        raise KeyboardInterrupt

    monkeypatch.setattr(installer, "uninstall_desktop", cancel)
    group, _output, error = _group()
    result = CliRunner().invoke(group, ["uninstall"])
    assert result.exit_code == 130
    assert "Cancelled. The app was left as it was." in error.export_text()
