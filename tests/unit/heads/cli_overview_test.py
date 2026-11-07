"""Interactive start screen is local and works without optional providers."""

from io import StringIO
from unittest.mock import Mock

import pytest
import typer
from rich.console import Console

from heinzel.heads.cli._overview import show
from heinzel.kernel import DaemonOverview, HeinzelError, LocalObservation, WebOverview

pytestmark = pytest.mark.unit


def test_show_keeps_optional_providers_unknown():
    def resolve(contract, _owner):
        if contract is DaemonOverview:
            return DaemonOverview(lambda _: LocalObservation("running", "scheduler loaded; current tick unknown"))
        msg = "not available"
        raise HeinzelError(msg)

    output = StringIO()
    show(Mock(resolve_builtin_service=resolve, lets=lambda _: []), Console(file=output, width=80))
    text = output.getvalue()
    assert "Scheduler: running · scheduler loaded; current tick unknown" in text
    assert "Web server: unknown" in text


def test_interactive_bare_invocation_adds_status_to_banner_and_help(monkeypatch: pytest.MonkeyPatch):
    from heinzel.heads import cli

    kernel = Mock()
    show_screen = Mock()
    banner = Mock()
    start_check = Mock(return_value=None)
    app = typer.Typer(no_args_is_help=True)
    app.callback()(lambda: None)
    app.command("example")(lambda: None)
    build = Mock(return_value=Mock(wraps=app))
    monkeypatch.setattr(cli, "boot", lambda: kernel)
    monkeypatch.setattr(cli, "console", Mock(is_terminal=True))
    output = StringIO()
    monkeypatch.setattr(output, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys, "stdout", output)
    monkeypatch.setattr(cli, "start_check", start_check)
    monkeypatch.setattr(cli, "build", build)
    monkeypatch.setattr(cli, "show", show_screen)
    monkeypatch.setattr(cli, "_banner", banner)
    monkeypatch.setattr(cli.sys, "argv", ["hz"])
    monkeypatch.setattr(cli.sys, "stdin", Mock(isatty=lambda: True))
    cli.main()
    show_screen.assert_called_once()
    start_check.assert_not_called()
    assert "example" in output.getvalue()
    banner.assert_called_once_with(kernel)
    build.assert_called_once_with(kernel)
    build.return_value.assert_called_once_with(args=["--help"], standalone_mode=False)
    build.reset_mock()

    monkeypatch.setattr(cli.sys, "argv", ["hz", "--help"])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0
    start_check.assert_called_once_with(kernel)
    build.assert_called_once_with(kernel)


def test_noninteractive_bare_keeps_existing_help_path(monkeypatch: pytest.MonkeyPatch):
    from heinzel.heads import cli

    kernel = Mock()
    build = Mock(return_value=Mock())
    monkeypatch.setattr(cli, "boot", lambda: kernel)
    monkeypatch.setattr(cli, "console", Mock(is_terminal=False))
    monkeypatch.setattr(cli.sys, "stdout", Mock(isatty=lambda: False))
    monkeypatch.setattr(cli, "start_check", lambda _: None)
    monkeypatch.setattr(cli, "build", build)
    monkeypatch.setattr(cli, "_banner", Mock())
    monkeypatch.setattr(cli.sys, "argv", ["hz"])
    cli.main()
    build.assert_called_once_with(kernel)


def test_redirected_stdin_keeps_existing_help_path(monkeypatch: pytest.MonkeyPatch):
    from heinzel.heads import cli

    kernel = Mock()
    build = Mock(return_value=Mock())
    monkeypatch.setattr(cli, "boot", lambda: kernel)
    monkeypatch.setattr(cli, "console", Mock(is_terminal=True))
    monkeypatch.setattr(cli.sys, "stdout", Mock(isatty=lambda: True))
    monkeypatch.setattr(cli, "start_check", lambda _: None)
    monkeypatch.setattr(cli, "build", build)
    monkeypatch.setattr(cli, "_banner", Mock())
    monkeypatch.setattr(cli.sys, "stdin", Mock(isatty=lambda: False))
    monkeypatch.setattr(cli.sys, "argv", ["hz"])
    cli.main()
    build.assert_called_once_with(kernel)


def test_provider_output_never_emits_terminal_control_sequences():
    def resolve(contract, _owner):
        if contract is DaemonOverview:
            return DaemonOverview(lambda _: LocalObservation("unknown", "bad\x1b]52;c;payload\x07\x9b31m"))
        msg = "unavailable"
        raise HeinzelError(msg)

    output = StringIO()
    show(
        Mock(resolve_builtin_service=resolve, lets=lambda _: []),
        Console(file=output, width=100, force_terminal=True, color_system=None),
    )
    rendered = output.getvalue()
    assert "\x1b" not in rendered
    assert "\x07" not in rendered
    assert "\x9b" not in rendered


def test_extension_declaration_never_resolves_or_runs_plugin_code(tmp_path):
    import json
    from datetime import UTC, datetime

    from heinzel.kernel import CachedOverviewLet, Settings

    directory = tmp_path / "overview"
    directory.mkdir(mode=0o700)
    tmp_path.chmod(0o700)
    path = directory / "example.sharing.json"
    path.write_text(json.dumps({"state": "running", "observed_at": datetime.now(UTC).isoformat()}))
    path.chmod(0o600)
    calls = []

    def resolve(contract, _owner=None):
        calls.append(contract)
        if contract is Settings:
            return Mock(state_dir=tmp_path)
        msg = "unavailable"
        raise HeinzelError(msg)

    kernel = Mock(
        resolve=resolve,
        resolve_builtin_service=resolve,
        lets=lambda _: [(Mock(qualname="example"), CachedOverviewLet(name="sharing"))],
    )
    output = StringIO()
    show(kernel, Console(file=output, width=120))
    assert "Sharing (example): unknown · last observed running at" in output.getvalue()
    assert calls == [DaemonOverview, WebOverview, Settings]


@pytest.mark.parametrize("contract", [DaemonOverview, WebOverview])
def test_plugin_cannot_supply_a_builtin_observer_contract(make_kernel, contract):
    from heinzel import Module

    class Trap:
        def __init__(self):
            pytest.fail("a plugin factory must never run from the overview")

        def observe(self, deadline):
            pytest.fail("a plugin observer must never run from the overview")

    module = Module("example", "An optional integration.")
    module.service(contract, Trap)
    kernel = make_kernel(example=module)
    output = StringIO()
    show(kernel, Console(file=output, width=120))
    assert "Scheduler: unknown" in output.getvalue()
    assert "Web server: unknown" in output.getvalue()


def test_forced_color_does_not_make_redirected_output_interactive(monkeypatch):
    from heinzel.heads import cli

    kernel = Mock()
    build = Mock(return_value=Mock())
    show_screen = Mock()
    monkeypatch.setattr(cli, "boot", lambda: kernel)
    monkeypatch.setattr(cli, "console", Mock(is_terminal=True))
    monkeypatch.setattr(cli.sys, "stdout", Mock(isatty=lambda: False))
    monkeypatch.setattr(cli.sys, "stdin", Mock(isatty=lambda: True))
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setattr(cli, "start_check", lambda _: None)
    monkeypatch.setattr(cli, "build", build)
    monkeypatch.setattr(cli, "show", show_screen)
    monkeypatch.setattr(cli, "_banner", Mock())
    monkeypatch.setattr(cli.sys, "argv", ["hz"])
    cli.main()
    show_screen.assert_not_called()
    build.assert_called_once_with(kernel)
