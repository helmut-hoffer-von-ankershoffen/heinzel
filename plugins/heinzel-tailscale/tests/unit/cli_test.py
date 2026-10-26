"""Read-only commands accept only bounded JSON and never relay vendor diagnostics."""

import sys
from types import SimpleNamespace

import pytest
from heinzel_tailscale import _cli
from heinzel_tailscale._cli import Result, TailscaleCli

from heinzel import HeinzelError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("result", "expected"),
    [(Result(0, b'{"BackendState":"Running"}', b""), {"BackendState": "Running"}), (Result(0, b"null", b""), None)],
)
def test_approved_state_reads_decode_json_without_changing_the_command(monkeypatch, result, expected):
    cli = TailscaleCli()
    seen = []

    def run(arguments):
        seen.append(tuple(arguments))
        return result

    monkeypatch.setattr(cli, "run", run)
    assert cli.json(("status", "--json", "--peers=false")) == expected
    assert seen == [("status", "--json", "--peers=false")]


@pytest.mark.parametrize(
    "result", [Result(1, b"", b"sensitive vendor diagnostic"), Result(0, b"not JSON", b""), Result(0, b"\xff", b"")]
)
def test_failed_or_invalid_read_becomes_a_safe_actionable_error(monkeypatch, result):
    cli = TailscaleCli()
    monkeypatch.setattr(cli, "run", lambda _arguments: result)
    with pytest.raises(HeinzelError) as error:
        cli.json(("serve", "status", "--json"))
    assert "sensitive vendor diagnostic" not in str(error.value)


def test_missing_client_is_detected_before_any_process_is_started(monkeypatch):
    monkeypatch.setattr(_cli.shutil, "which", lambda *_args, **_kwargs: None)
    with pytest.raises(HeinzelError, match="Install Tailscale"):
        TailscaleCli().run(("status", "--json", "--peers=false"))


def test_mac_app_cli_is_found_without_installing_a_launcher_or_opening_the_gui(monkeypatch):
    path = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(_cli.shutil, "which", lambda name, **_kwargs: path if name == path else None)
    assert TailscaleCli().executable() == path
    assert TailscaleCli.environment()["TAILSCALE_BE_CLI"] == "1"


def test_unsupported_platform_is_refused_before_process_creation(monkeypatch):
    cli = TailscaleCli()
    monkeypatch.setattr(cli, "executable", lambda: "/fake/tailscale")
    monkeypatch.setattr(_cli, "os", SimpleNamespace(name="nt"))
    with pytest.raises(HeinzelError, match="macOS and Linux"):
        cli.run(("status", "--json", "--peers=false"))


def test_unstartable_client_reports_installation_action_without_raw_os_error(monkeypatch):
    cli = TailscaleCli()
    monkeypatch.setattr(cli, "executable", lambda: "/fake/tailscale")

    def denied(*_args, **_kwargs):
        raise PermissionError

    monkeypatch.setattr(_cli.subprocess, "Popen", denied)
    with pytest.raises(HeinzelError, match="permissions"):
        cli.run(("status", "--json", "--peers=false"))
