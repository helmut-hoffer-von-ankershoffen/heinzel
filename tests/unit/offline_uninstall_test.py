"""Offline uninstall binds one existing tool and never discovers plugins or bootstraps an installer."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from heinzel import HeinzelError
from heinzel.heads import cli
from heinzel.kernel import _installation
from heinzel.modules.system import _uninstall
from tests.unit.kernel.installation_continuity_test import tool

__all__ = ["tool"]
pytestmark = pytest.mark.unit


@pytest.fixture
def removable(tool):
    (tool.prefix / "bin/python").symlink_to(Path(sys.executable).resolve())
    uv = tool.prefix.parent.parent / "uv"
    uv.write_text("#!/bin/sh\nexit 99\n")
    uv.chmod(0o700)
    tool._uv = str(uv)
    tool.record_startup_state(expected=tool.startup_fingerprint())
    return tool


def test_offline_command_removes_only_the_verified_tool_and_owned_links(removable, tmp_path, monkeypatch):
    store, binary = removable.tool_destination()
    foreign = store / "unrelated"
    foreign.mkdir()
    sentinel = foreign / "kept.txt"
    sentinel.write_text("another tool")
    user_data = tmp_path / "data.txt"
    user_data.write_text("retained user work")
    calls = []

    def run(command, environment):
        calls.append((command, environment))
        assert environment["UV_TOOL_DIR"] == str(store)
        assert environment["UV_TOOL_BIN_DIR"] == str(binary)
        assert command[1:] == ["--offline", "--no-config", "--no-python-downloads", "tool", "uninstall", "heinzel"]
        (binary / "hz").unlink()
        shutil.rmtree(removable.prefix)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(removable, "_runner", run)
    removable.uninstall_offline(expected=removable.startup_fingerprint())
    assert len(calls) == 1
    assert sentinel.read_text() == "another tool"
    assert user_data.read_text() == "retained user work"
    assert not removable.prefix.exists()


@pytest.mark.parametrize("change", ["script", "receipt", "prefix-link", "unmarked"])
def test_changed_or_redirected_installation_never_invokes_uv(removable, change, monkeypatch):
    before = removable.startup_fingerprint()
    if change == "script":
        (removable.prefix / "bin/hz").write_text("changed script")
    elif change == "receipt":
        receipt = removable.prefix / "uv-receipt.toml"
        saved = receipt.with_suffix(".saved")
        receipt.rename(saved)
        receipt.symlink_to(saved)
    elif change == "prefix-link":
        original = removable.prefix
        saved = original.with_name("redirected")
        original.rename(saved)
        original.symlink_to(saved, target_is_directory=True)
    else:
        (removable.prefix / ".heinzel-startup-state").unlink()
    monkeypatch.setattr(removable, "_runner", lambda *args: pytest.fail("An unsafe target must not invoke uv"))
    with pytest.raises((HeinzelError, OSError)):
        removable.uninstall_offline(expected=before)
    assert removable.prefix.exists()


@pytest.mark.parametrize("outcome", ["refused", "uncertain", "reported-success-without-removal"])
def test_refused_or_uncertain_removal_never_reinstalls_or_reports_success(removable, outcome, monkeypatch):
    calls = []

    def run(command, environment):
        calls.append(command)
        if outcome == "uncertain":
            raise subprocess.TimeoutExpired(command, 1)
        return subprocess.CompletedProcess(command, 1 if outcome == "refused" else 0, "", "")

    monkeypatch.setattr(removable, "_runner", run)
    with pytest.raises(HeinzelError, match="removal"):
        removable.uninstall_offline(expected=removable.startup_fingerprint())
    assert len(calls) == 1
    assert calls[0][-3:] == ["tool", "uninstall", "heinzel"]
    assert removable.prefix.exists()


def test_cli_uninstall_and_help_dispatch_before_boot_or_background_checks(monkeypatch, capsys):
    monkeypatch.setattr(cli, "boot", lambda *args: pytest.fail("Offline recovery must not discover plugins"))
    monkeypatch.setattr(cli, "start_check", lambda *args: pytest.fail("Offline recovery must not check releases"))
    monkeypatch.setattr(_installation, "_run", lambda *args: pytest.fail("Help must not invoke an installer"))
    monkeypatch.setattr(sys, "argv", ["hz", "system", "uninstall", "--help"])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0
    assert "offline" in capsys.readouterr().out
    monkeypatch.setattr(
        _uninstall, "uninstall_installation", lambda: _uninstall.UninstallResult(daemon_removed=True, web_removed=True)
    )
    monkeypatch.setattr(sys, "argv", ["hz", "system", "uninstall", "--json"])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0
    assert '"data_retained":true' in capsys.readouterr().out
