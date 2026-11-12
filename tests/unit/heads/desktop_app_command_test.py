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
