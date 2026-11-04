"""Offline uninstall binds one existing tool and never discovers plugins or bootstraps an installer."""

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from heinzel import HeinzelError
from heinzel.heads import cli
from heinzel.kernel import _installation
from heinzel.kernel._offline_uv import VerifiedUV
from heinzel.kernel._tool_uninstall import owned_file
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
    captured = owned_file(uv)
    tool.test_remover = VerifiedUV(uv, hashlib.sha256(captured.content).hexdigest(), captured.identity)
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

    monkeypatch.setattr(removable, "_exact_runner", run)
    removable.uninstall_offline(expected=removable.startup_fingerprint(), executable=removable.test_remover)
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
    monkeypatch.setattr(removable, "_exact_runner", lambda *args: pytest.fail("An unsafe target must not invoke uv"))
    with pytest.raises((HeinzelError, OSError)):
        removable.uninstall_offline(expected=before, executable=removable.test_remover)
    assert removable.prefix.exists()


@pytest.mark.parametrize("outcome", ["refused", "uncertain", "reported-success-without-removal"])
def test_refused_or_uncertain_removal_never_reinstalls_or_reports_success(removable, outcome, monkeypatch):
    calls = []

    def run(command, environment):
        calls.append(command)
        if outcome == "uncertain":
            raise subprocess.TimeoutExpired(command, 1)
        return subprocess.CompletedProcess(command, 1 if outcome == "refused" else 0, "", "")

    monkeypatch.setattr(removable, "_exact_runner", run)
    with pytest.raises(HeinzelError, match="removal"):
        removable.uninstall_offline(expected=removable.startup_fingerprint(), executable=removable.test_remover)
    assert len(calls) == 1
    assert calls[0][-3:] == ["tool", "uninstall", "heinzel"]
    assert removable.prefix.exists()


@pytest.mark.parametrize(("arguments", "code"), [(["--help"], 0), (["--unknown"], 2), (["extra"], 2)])
def test_cli_uninstall_parses_before_any_discovery_or_background_checks(monkeypatch, capsys, arguments, code):
    from heinzel.heads.cli import _uninstall as head

    def forbidden(*args, **kwargs):
        pytest.fail("Help and invalid arguments must not boot, discover or invoke an installer")

    monkeypatch.setattr(cli, "boot", forbidden)
    monkeypatch.setattr(head, "boot", forbidden)
    monkeypatch.setattr(cli, "start_check", forbidden)
    monkeypatch.setattr(_installation, "_run", forbidden)
    monkeypatch.setattr(sys, "argv", ["hz", "system", "uninstall", *arguments])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == code
    output = capsys.readouterr()
    assert "offline" in output.out if code == 0 else "unrecognized arguments" in output.err


def test_cli_uninstall_uses_only_builtin_port_and_home_configuration(monkeypatch, capsys, tmp_path):
    from heinzel.heads import bootstrap
    from heinzel.heads.cli import _uninstall as head
    from heinzel.kernel import Credentials, Kernel, OfflineUninstall, UninstallResult, _discovery

    def forbidden(*args, **kwargs):
        pytest.fail("Maintenance must not resolve accounts, providers, external plugins, checks or bootstrap")

    # Ordinary boot is forbidden; the maintenance host must request the public built-in-only mode explicitly.
    monkeypatch.setattr(cli, "boot", forbidden)
    monkeypatch.setattr(cli, "start_check", forbidden)
    monkeypatch.setattr(bootstrap, "main", forbidden)
    monkeypatch.setattr(Credentials, "__init__", forbidden)
    monkeypatch.setattr(_discovery, "entry_points", forbidden)
    monkeypatch.setattr(_installation, "_run", forbidden)
    original_boot, original_resolve = head.boot, Kernel.resolve
    resolutions, scopes, calls = [], [], []

    def resolve(kernel, kind):
        resolutions.append(kind)
        assert kind is OfflineUninstall
        return original_resolve(kernel, kind)

    def boot(**kwargs):
        assert kwargs == {"builtin_only": True, "cwd": Path.home()}
        kernel = original_boot(**kwargs)
        assert all(record.origin.builtin for record in kernel.modules)
        assert not kernel._factory_instances
        scopes.extend(file.path for file in kernel.config.files)
        return kernel

    def remove(**kwargs):
        calls.append(kwargs)
        kwargs["on_recovery"]("fixture-local-recovery")
        return UninstallResult(daemon_removed=True, web_removed=True)

    # A cwd project configuration cannot redirect the maintenance profile.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hz.toml").write_text("invalid = [TOML")
    monkeypatch.setattr(Kernel, "resolve", resolve)
    monkeypatch.setattr(head, "boot", boot)
    monkeypatch.setattr(_uninstall, "uninstall_installation", remove)
    monkeypatch.setattr(sys, "argv", ["hz", "system", "uninstall", "--json"])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0
    output = capsys.readouterr()
    assert '"data_retained":true' in output.out
    assert "fixture-local-recovery" in output.err
    assert resolutions == [OfflineUninstall]
    assert len(calls) == 1
    assert tmp_path / "hz.toml" not in scopes


@pytest.mark.parametrize(
    ("builtin_only", "enabled", "expected"), [(False, True, True), (False, False, False), (True, True, False)]
)
def test_public_boot_keeps_ordinary_plugin_policy_and_explicit_maintenance_scope(
    monkeypatch, tmp_path, builtin_only, enabled, expected
):
    from heinzel.kernel import Config, Settings, _boot

    selected, discovered = [], []

    def load(cls, cwd=None):
        selected.append(cwd)
        return Config.empty()

    def discover(**kwargs):
        discovered.append(kwargs["plugins"])
        return [], {}

    monkeypatch.setattr(Config, "load", classmethod(load))
    monkeypatch.setattr(_boot, "discover", discover)
    kwargs = {"builtin_only": True, "cwd": tmp_path} if builtin_only else {}
    _boot.boot(Settings(plugins_enabled=enabled), **kwargs)
    assert selected == [tmp_path if builtin_only else None]
    assert discovered == [expected]


@pytest.mark.parametrize("change", ["foreign-origin", "foreign-distribution", "foreign-class", "callback", "rebound"])
def test_maintenance_port_rejects_foreign_and_rebound_providers(make_kernel, change):
    from dataclasses import replace

    from heinzel.kernel import OfflineUninstall

    kernel = make_kernel(builtins=True)
    if change.startswith("foreign-") and change != "foreign-class":
        record = kernel._records["system"]
        origin = replace(
            record.origin, **({"builtin": False} if change == "foreign-origin" else {"distribution": "foreign"})
        )
        kernel._records["system"] = replace(record, origin=origin)
    elif change == "foreign-class":

        class Foreign(_uninstall.SystemOfflineUninstall):
            pass

        Foreign.__module__ = "foreign.maintenance"
        kernel._factories[OfflineUninstall] = Foreign
    elif change == "callback":
        kernel._factories[OfflineUninstall] = lambda: pytest.fail("A substituted callback must never run")
    else:
        kernel.bind(OfflineUninstall, _uninstall.SystemOfflineUninstall())
    with pytest.raises(HeinzelError, match="expected built-in service"):
        kernel.resolve_builtin_service(OfflineUninstall, "system")


def test_real_exact_runner_does_not_restore_removed_ambient_uv_policy(monkeypatch):
    import json

    for name in ("UV_REMOVAL_SENTINEL", "DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH"):
        monkeypatch.setenv(name, "/fixture-untrusted")
    result = _installation._run_exact(
        [sys.executable, "-I", "-c", "import os,json; print(json.dumps(dict(os.environ)))"],
        {"UV_TOOL_DIR": "/fixture/tools", "UV_TOOL_BIN_DIR": "/fixture/bin", "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0
    environment = json.loads(result.stdout)
    assert not {"UV_REMOVAL_SENTINEL", "DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH"} & environment.keys()
    assert environment["UV_TOOL_DIR"] == "/fixture/tools"
    assert environment["UV_TOOL_BIN_DIR"] == "/fixture/bin"


@pytest.mark.parametrize("change", ["replaced", "group-writable-parent", "tampered"])
def test_private_remover_swap_or_unsafe_copy_refuses_before_uv(removable, monkeypatch, change):
    uv = removable.test_remover.path
    if change == "replaced":
        uv.rename(uv.with_suffix(".old"))
        uv.write_text("#!/bin/sh\nexit 0\n")
        uv.chmod(0o700)
    elif change == "tampered":
        uv.write_text("#!/bin/sh\nexit 0\n")
    else:
        uv.parent.chmod(0o770)
    monkeypatch.setattr(removable, "_exact_runner", lambda *args: pytest.fail("An unsafe remover must never execute"))
    with pytest.raises((HeinzelError, OSError)):
        removable.uninstall_offline(expected=removable.startup_fingerprint(), executable=removable.test_remover)


def test_invalid_runtime_remover_preflight_precedes_all_service_actions(removable, monkeypatch):
    calls = []

    def reject(prefix, directory):
        calls.append(prefix)
        message = "untrusted runtime remover"
        raise HeinzelError(message)

    monkeypatch.setattr(_uninstall, "stage", reject)
    with pytest.raises(HeinzelError, match="untrusted runtime remover"):
        _uninstall.uninstall_installation(removable, daemon=object(), web=object())
    assert calls == [removable.prefix]
    assert removable.prefix.exists()
