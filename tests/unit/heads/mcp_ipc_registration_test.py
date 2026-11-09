"""Committed installed-CLI registration and recovery in a fenced scratch profile."""

import json
import os
import pty
import re
import select
import shutil
import subprocess
import sys
import termios
from contextlib import nullcontext
from pathlib import Path
from unittest import TestCase

import pytest

from heinzel import Config, HeinzelError, Progress
from heinzel.heads import cli
from heinzel.heads._tool_install import resolve_target
from heinzel.heads.cli import _mcp_lifecycle
from heinzel.heads.mcp import _ipc_registration as registration
from heinzel.heads.mcp import _ipc_runtime_root as roots
from heinzel.kernel import (
    HostIntegrationScope,
    HostServiceManager,
    Installation,
    Kernel,
    MaintenanceLocks,
    ProcessOutcome,
    Settings,
    host_maintenance_directory,
    host_maintenance_lock,
    installation_inventory_complete,
    installation_mutation_pending,
    mcp_registration_inventory,
)
from heinzel.kernel._mcp_native import MAC_LABEL_PREFIX, McpNativePlan
from heinzel.modules.system import recover_announced
from heinzel.modules.system._announced import ToolSnapshot
from heinzel.modules.system.daemon import _maintenance
from heinzel.modules.system.daemon._models import DaemonSettings
from heinzel.modules.system.daemon._service import DaemonService

pytestmark = pytest.mark.unit
_ID = "fixture-installed"
_BOOT = "e8263cb8-6123-48d0-8147-cb24075f3181"
_LAUNCHER = """
import fcntl, runpy, sys, termios
from pathlib import Path
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
runpy.run_path(sys.argv[1])['_run_installed_cli'](Path(sys.argv[2]), sys.argv[3])
"""


class _Manager(HostServiceManager):
    def command(self, args):
        assert args == ["/bin/launchctl", "list"]
        return subprocess.CompletedProcess(args, 0, "PID\tStatus\tLabel\n", "")


def _write_sdk(directory):
    prefix = directory / "tools" / "heinzel"
    scripts = prefix / "bin"
    scripts.mkdir(parents=True)
    (scripts / "hz").write_text("scratch installed entrypoint")
    (scripts / "hz").chmod(0o700)
    interpreter = scripts / "python"
    interpreter.write_bytes(b"scratch interpreter identity; execution is already isolated")
    interpreter.chmod(0o700)
    bin_dir = directory / "entrypoints"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "hz").symlink_to(scripts / "hz")
    (prefix / "uv-receipt.toml").write_text(
        '[tool]\nrequirements = [{ name = "heinzel" }]\n'
        f'entrypoints = [{{ name = "hz", install-path = {str(bin_dir / "hz")!r} }}]\n'
    )
    module = prefix / "lib" / "_ipc_registration.py"
    module.parent.mkdir()
    module.write_bytes(Path(registration.__file__).read_bytes())
    sdk = Installation(Progress(), prefix=prefix, uv="unused")
    sdk.record_startup_state(expected=sdk.startup_fingerprint())
    return sdk, interpreter, module


def _sdk(directory, *, fresh=False):
    if not fresh:
        return _write_sdk(directory)
    target, service = _fresh_setup(directory)
    with service.quiescence_guard(target.binary, fresh_store=target.fresh_store):
        installed = _write_sdk(directory)
    assert installation_inventory_complete(target.prefix)
    return installed


def _fresh_setup(directory):
    def uv_directories(args, **_kwargs):
        folder = directory / ("entrypoints" if args[-1] == "--bin" else "tools")
        return subprocess.CompletedProcess(args, 0, str(folder), "")

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(subprocess, "run", uv_directories)
        target = resolve_target("scratch-uv")
    assert target.fresh_store is not None
    selected = Installation(Progress(), prefix=target.prefix, uv="unused")
    service = _ordinary_service(selected, directory / "fresh-install-profile")
    service._platform = "darwin"
    facts = {"managername": "Aqua\n", "manageruid": f"{os.getuid()}\n", "list": "PID Status Label\n"}
    service._quiescence_probe = lambda args: ProcessOutcome(0, facts[args[-1]], "")
    return target, service


def _invoke(patcher, action, expected, *, registration_logged=True):
    patcher.setattr(sys, "argv", ["hz", "system", "mcp", action, _ID])
    logging_scope = (
        TestCase().assertLogs("heinzel.mcp")
        if registration_logged and action in {"register", "recover"}
        else nullcontext()
    )
    with logging_scope as logged, pytest.raises(SystemExit) as status:
        cli.main()
    assert status.value.code == expected
    if logged is not None:
        records = [record for record in logged.records if record.message == "mcp.ipc.registration"]
        assert [record.phase for record in records] == ["start", "end"]
        assert records[-1].outcome == ("ok" if expected == 0 else "error")
    sys.stdout.flush()


def _interrupt_commit(patcher):
    original = registration._record

    def interrupt(directory, name, data):
        if name.endswith(".commit"):
            raise OSError
        original(directory, name, data)

    patcher.setattr(registration, "_record", interrupt)


def _exercise_registration(patcher, sdk, phase, native_root):
    inventory = host_maintenance_directory(sdk.prefix)
    if phase == "recover":
        with pytest.MonkeyPatch.context() as crash:
            _interrupt_commit(crash)
            _invoke(patcher, "register", 1)
        (intent,) = inventory.glob("*.intent")
        data = intent.read_bytes()
        assert not list(inventory.glob("*.commit"))
        with pytest.raises(HeinzelError, match="explicit recovery"):
            mcp_registration_inventory(sdk.prefix)
        _invoke(patcher, "recover", 0)
        assert intent.read_bytes() == data
    else:
        _invoke(patcher, "register", 0)
    (receipt,) = mcp_registration_inventory(sdk.prefix)
    assert receipt.id == _ID
    assert receipt.fingerprint == sdk.startup_fingerprint()
    assert not native_root.exists()  # Registration and recovery never start a listener.
    if phase == "start":
        _invoke(patcher, "start", 0)  # The foreground command returns after a verified reviewed Stop.
        assert native_root.is_dir()
    elif phase == "sdk-changed":
        (sdk.prefix / "bin" / "hz").write_text("changed launcher")
        sdk.record_startup_state(expected=sdk.startup_fingerprint())
        _invoke(patcher, "start", 1)
        assert not native_root.exists()
    elif phase == "lock-changed":
        lock = host_maintenance_lock(sdk.prefix)
        lock.rename(lock.with_name("displaced.lock"))
        lock.write_bytes(b"same-UID substitute")
        lock.chmod(0o600)
        _invoke(patcher, "start", 1)
        assert lock.read_bytes() == b"same-UID substitute"
        assert not native_root.exists()


def _run_installed_cli(directory, phase):
    sdk, interpreter, module = _sdk(directory, fresh=phase != "legacy-pending")
    native_root = Path(os.confstr(65537)).resolve() / roots._locator_name()
    scratch_units = directory / "native-units"
    scratch_units.mkdir(mode=0o700)
    scope = HostIntegrationScope(scratch_units, MAC_LABEL_PREFIX, MAC_LABEL_PREFIX)
    try:
        with pytest.MonkeyPatch.context() as patcher:
            # The process runs the actual CLI route. Substitute only the installed image metadata and manager.
            patcher.setattr(sys, "prefix", str(sdk.prefix))
            patcher.setattr(sys, "executable", str(interpreter))
            patcher.setattr(registration, "__file__", str(module))
            manager = _Manager()
            patcher.setattr(_maintenance.SystemHostServiceManager, "command", lambda _self, args: manager.command(args))
            patcher.setattr(Config, "load", lambda *_args, **_kwargs: pytest.fail("Ambient config was loaded"))
            patcher.setattr(_mcp_lifecycle, "native_mcp_scope", lambda: scope)
            patcher.setattr(roots, "_locator_name", lambda: native_root.name)
            patcher.setenv("HZ_STATE_DIR", str(directory / "forged"))
            patcher.setattr(cli, "boot", lambda: pytest.fail("Project discovery ran"))
            if phase == "legacy-pending":
                marker = directory / "unenrolled-legacy-profile" / "daemon" / "mutation-pending.json"
                marker.parent.mkdir(parents=True)
                marker.write_text(json.dumps({"token": "a" * 64}, indent=2) + "\n")
                runtime = registration.InstalledMcpAuthority(sdk, sdk.startup_fingerprint()).runtime
                assert not installation_mutation_pending(sdk.prefix, runtime.state)
                _invoke(patcher, "register", 1, registration_logged=False)
                assert marker.is_file()
                assert not runtime.state.exists()
                assert not native_root.exists()
            elif phase == "foreign-unit":
                runtime = registration.InstalledMcpAuthority(sdk, sdk.startup_fingerprint()).runtime
                unit = scratch_units / (McpNativePlan.capture(runtime, _ID).name + ".plist")
                unit.write_bytes(b"foreign unit")
                _invoke(patcher, "register", 1)
                assert unit.read_bytes() == b"foreign unit"
                assert not list(host_maintenance_directory(sdk.prefix).glob("*.intent"))
            elif phase == "decline":
                _invoke(patcher, "register", 1)
                assert not list(host_maintenance_directory(sdk.prefix).glob("*.intent"))
                assert not native_root.exists()
            else:
                _exercise_registration(patcher, sdk, phase, native_root)
            assert not (directory / "forged").exists()
    finally:
        if native_root.is_dir():
            shutil.rmtree(native_root)


def _approve(terminal, action, *, approve=True):
    output = bytearray()
    while b"\r\n> " not in output:
        assert select.select([terminal], [], [], 2)[0]
        output.extend(os.read(terminal, 4096))
    assert f'"action": "{action}"'.encode() in output
    match = re.search(rb"Type exactly: (approve MCP [0-9a-f]{8})\r\n", output)
    assert match is not None
    os.write(terminal, (match[1] if approve else b"decline") + b"\n")


def _observe_foreground(terminal, stderr):
    _approve(terminal, "start")
    assert select.select([stderr], [], [], 2)[0]
    assert b"MCP foreground session" in stderr.readline()
    os.write(terminal, b"stop\n")
    _approve(terminal, "stop")


@pytest.mark.skipif(
    sys.platform != "darwin", reason="Uses real Darwin IPC with a simulated installed image and manager"
)
@pytest.mark.parametrize(
    "phase", ["start", "recover", "sdk-changed", "lock-changed", "foreign-unit", "decline", "legacy-pending"]
)
def test_installed_mcp_registration_and_explicit_recovery(short_socket_root, phase):
    master, terminal = pty.openpty()
    settings = termios.tcgetattr(terminal)
    settings[3] &= ~termios.ECHO
    termios.tcsetattr(terminal, termios.TCSANOW, settings)
    try:
        with subprocess.Popen(
            [sys.executable, "-I", "-c", _LAUNCHER, __file__, str(short_socket_root), phase],
            stdin=terminal,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        ) as process:
            os.close(terminal)
            terminal = -1
            try:
                if phase not in {"foreign-unit", "legacy-pending"}:
                    _approve(master, "register", approve=phase != "decline")
                if phase == "recover":
                    _approve(master, "recover")
                if phase == "start":
                    _observe_foreground(master, process.stderr)
                stdout, stderr = process.communicate(timeout=4)
                assert process.returncode == 0, stderr.decode()
            finally:
                if process.poll() is None:
                    os.close(master)
                    master = -1
                    process.kill()
    finally:
        if master >= 0:
            os.close(master)
        if terminal >= 0:
            os.close(terminal)
    if phase in {"foreign-unit", "decline", "legacy-pending"}:
        assert not stdout
        if phase == "legacy-pending":
            assert b"legacy mutation inventory has been verified" in stderr
    else:
        rows = [json.loads(line) for line in stdout.splitlines()]
        assert rows[0] == {"registration": "committed", "activation": "dormant"}
        if phase == "start":
            assert rows[1]["transport"] == "reachable"
            assert rows[1]["mcp_readiness"] == "unverified"
            assert rows[2]["worker"] == "stopped"


@pytest.mark.parametrize("path", ["mutation", "refresh"])
def test_standard_sdk_mutation_cannot_cross_a_foreground_maintenance_fence(tmp_path, path):
    sdk, _, _ = _sdk(tmp_path)
    settings = Settings(state_dir=tmp_path / "state")
    service = DaemonService(Kernel(settings), settings, DaemonSettings(), quiescence_installation=sdk)
    operation = (
        service.mutation_guard(lambda: pytest.fail("Checked SDK before fencing it"), lambda: "")
        if path == "mutation"
        else service.quiescence_guard(tmp_path / "entrypoints")
    )
    with (
        MaintenanceLocks((host_maintenance_lock(sdk.prefix),)),
        pytest.raises(HeinzelError, match="active host integration"),
        operation,
    ):
        pytest.fail("Changed an SDK with an owned foreground lifecycle")


def _ordinary_service(sdk, state):
    settings = Settings(state_dir=state)
    return DaemonService(
        Kernel(settings),
        settings,
        DaemonSettings(unit_dir=state.parent / "scratch-units"),
        continuity_fingerprint=sdk.startup_fingerprint,
        quiescence_installation=sdk,
        host_boot_evidence=lambda: (_BOOT, 0),
    )


def _interrupt_ordinary_update(prefix, state):
    sdk = Installation(Progress(), prefix=Path(prefix), uv="unused")
    service = _ordinary_service(sdk, Path(state))
    with service.mutation_guard(sdk.startup_state_matches, sdk.rollback_identity):
        os._exit(17)  # The writer dies with the ordinary gate still present and the SDK unchanged.


def test_interrupted_ordinary_profile_fences_mcp_until_verified_recovery(tmp_path):
    sdk, _, _ = _sdk(tmp_path, fresh=True)
    state = tmp_path / "ordinary-profile"
    fingerprint = sdk.startup_fingerprint()
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import runpy, sys; runpy.run_path(sys.argv[1])['_interrupt_ordinary_update'](*sys.argv[2:])",
            __file__,
            os.fspath(sdk.prefix),
            os.fspath(state),
        ],
        capture_output=True,
        check=False,
        timeout=4,
    )
    assert result.returncode == 17, result.stderr.decode()
    ordinary = state / "daemon" / "mutation-pending.json"
    marker = ordinary.read_bytes()
    authority = registration.InstalledMcpAuthority(sdk, fingerprint)
    assert sdk.startup_state_matches()
    assert sdk.startup_fingerprint() == fingerprint
    assert not (authority.runtime.state / "daemon" / ordinary.name).exists()
    with pytest.raises(HeinzelError, match="committed MCP registration"):
        authority.require_current()
    other = _ordinary_service(sdk, tmp_path / "different-profile")
    with (
        pytest.raises(HeinzelError, match="another profile requires recovery"),
        other.mutation_guard(sdk.startup_state_matches, sdk.rollback_identity),
    ):
        pytest.fail("Another ordinary profile bypassed the interrupted SDK mutation")
    service = _ordinary_service(sdk, state)
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(service, "_host_boot", lambda: (None, None))
        assert service._recover_abandoned_mutation() is None
        assert ordinary.read_bytes() == marker
        assert installation_mutation_pending(sdk.prefix, authority.runtime.state)
    receipt = service._recover_abandoned_mutation()
    assert receipt is not None
    assert json.loads(receipt.read_bytes())["status"] == "archived"
    assert not ordinary.exists()
    authority.require_current()


def test_profile_replacement_cannot_hide_an_sdk_mutation(tmp_path):
    sdk, _, _ = _sdk(tmp_path, fresh=True)
    state = tmp_path / "ordinary-profile"
    service = _ordinary_service(sdk, state)
    authority = registration.InstalledMcpAuthority(sdk, sdk.startup_fingerprint())
    msg = "interrupted"
    with (
        pytest.raises(RuntimeError, match="interrupted"),
        service.mutation_guard(sdk.startup_state_matches, sdk.rollback_identity),
    ):
        raise RuntimeError(msg)
    retained = state / "retained-daemon"
    service.state_dir.rename(retained)
    service.state_dir.mkdir(mode=0o700)
    assert not (service.state_dir / "mutation-pending.json").exists()
    with pytest.raises(HeinzelError, match="committed MCP registration"):
        authority.require_current()
    assert (retained / "mutation-pending.json").is_file()


@pytest.mark.parametrize("rollback", [False, True])
def test_ordinary_mutation_releases_sdk_fence_only_after_verified_outcome(tmp_path, rollback):
    sdk, _, _ = _sdk(tmp_path, fresh=True)
    state = tmp_path / "ordinary-profile"
    other_state = tmp_path / "other-profile"
    service = _ordinary_service(sdk, state)
    other = _ordinary_service(sdk, other_state)
    authority = registration.InstalledMcpAuthority(sdk, sdk.startup_fingerprint())
    with service.mutation_guard(sdk.startup_state_matches, sdk.rollback_identity) as scope:
        plan = scope.capture()
        assert installation_mutation_pending(sdk.prefix, other_state)
        with (
            pytest.raises(HeinzelError, match="active host integration"),
            other.mutation_guard(sdk.startup_state_matches, sdk.rollback_identity),
        ):
            pytest.fail("A second writer bypassed the exact SDK lock")
        if rollback:
            scope.abort_unchanged()
        else:
            scope.verified_complete(sdk.startup_fingerprint())
            scope.request(plan)
    authority.require_current()


def test_announced_rollback_tracks_failed_snapshot_recovery_and_uses_sdk_lock(tmp_path, monkeypatch):
    sdk, _, _ = _sdk(tmp_path)
    state = tmp_path / "ordinary-profile"
    kernel = Kernel(Settings(state_dir=state))
    snapshot = ToolSnapshot.take(sdk.prefix, state / "auto-update")
    other_state = tmp_path / "different-profile"
    with MaintenanceLocks((host_maintenance_lock(sdk.prefix),)), pytest.raises(HeinzelError, match="in progress"):
        recover_announced(sdk, kernel)
    assert snapshot.copy.is_dir()

    def interrupted(_snapshot):
        raise InterruptedError

    with monkeypatch.context() as patcher:
        patcher.setattr(ToolSnapshot, "recover", interrupted)
        with pytest.raises(InterruptedError):
            recover_announced(sdk, kernel)
    assert installation_mutation_pending(sdk.prefix, other_state)
    assert recover_announced(sdk, kernel)
    assert not snapshot.copy.exists()
    assert not snapshot.aside.exists()
    assert not installation_mutation_pending(sdk.prefix, other_state)
    assert sdk.startup_state_matches()


def test_verified_legacy_update_enrolls_its_profile_without_certifying_completeness(tmp_path):
    sdk, _, _ = _sdk(tmp_path)
    state = tmp_path / "ordinary-legacy-profile"
    service = _ordinary_service(sdk, state)
    before = sdk.startup_fingerprint()
    with service.mutation_guard(sdk.startup_state_matches, sdk.rollback_identity) as scope:
        plan = scope.capture()
        scope.verified_complete(before)
        scope.request(plan)
    assert not installation_mutation_pending(sdk.prefix, state)
    assert not installation_inventory_complete(sdk.prefix)
    with pytest.raises(registration.UnverifiedMcpInventoryError):
        registration.InstalledMcpAuthority(sdk, before).require_current()


def test_fresh_sdk_cannot_restore_a_snapshot_from_an_unenrolled_legacy_profile(tmp_path):
    sdk, _, _ = _sdk(tmp_path, fresh=True)
    state = tmp_path / "ordinary-legacy-profile"
    snapshot = ToolSnapshot.take(sdk.prefix, state / "auto-update")
    kernel = Kernel(Settings(state_dir=state))
    service = _ordinary_service(sdk, state)
    with (
        pytest.raises(HeinzelError, match="cannot enroll a legacy profile"),
        service.mutation_guard(sdk.startup_state_matches, sdk.rollback_identity),
    ):
        pytest.fail("A new writer adopted the stale snapshot profile")
    with pytest.raises(HeinzelError, match="rollback profile does not belong"):
        recover_announced(sdk, kernel)
    assert snapshot.copy.is_dir()
    assert sdk.startup_state_matches()


def test_recovery_of_interrupted_fresh_install_does_not_infer_completeness(tmp_path):
    target, service = _fresh_setup(tmp_path)
    msg = "interrupted before verification"

    def interrupted():
        _write_sdk(tmp_path)
        raise RuntimeError(msg)

    with (
        pytest.raises(RuntimeError, match=msg),
        service.quiescence_guard(target.binary, fresh_store=target.fresh_store),
    ):
        interrupted()
    assert not installation_inventory_complete(target.prefix)
    service.recover_quiescent_install(target.binary)
    assert not installation_inventory_complete(target.prefix)
