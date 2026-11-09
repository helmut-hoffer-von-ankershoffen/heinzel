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

from heinzel import HeinzelError, Progress
from heinzel.heads import cli
from heinzel.heads.cli import _mcp_lifecycle
from heinzel.heads.mcp import _ipc_registration as registration
from heinzel.heads.mcp import _ipc_runtime_root as roots
from heinzel.heads.mcp._ipc_native_plan import MAC_LABEL_PREFIX, McpNativePlan
from heinzel.kernel import (
    HostIntegrationScope,
    HostServiceManager,
    Installation,
    Kernel,
    MaintenanceLocks,
    Settings,
    host_maintenance_directory,
    host_maintenance_lock,
)
from heinzel.modules.system.daemon import _maintenance
from heinzel.modules.system.daemon._models import DaemonSettings
from heinzel.modules.system.daemon._service import DaemonService

pytestmark = pytest.mark.unit
_ID = "fixture-installed"
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


def _sdk(directory):
    prefix = directory / "tools" / "heinzel"
    scripts = prefix / "bin"
    scripts.mkdir(parents=True)
    (scripts / "hz").write_text("scratch installed entrypoint")
    (scripts / "hz").chmod(0o700)
    interpreter = scripts / "python"
    interpreter.write_bytes(b"scratch interpreter identity; execution is already isolated")
    interpreter.chmod(0o700)
    bin_dir = directory / "entrypoints"
    bin_dir.mkdir()
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


def _invoke(patcher, action, expected):
    patcher.setattr(sys, "argv", ["hz", "system", "mcp", action, _ID])
    logging_scope = TestCase().assertLogs("heinzel.mcp") if action in {"register", "recover"} else nullcontext()
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
            registration.mcp_registration_inventory(sdk.prefix)
        _invoke(patcher, "recover", 0)
        assert intent.read_bytes() == data
    else:
        _invoke(patcher, "register", 0)
    (receipt,) = registration.mcp_registration_inventory(sdk.prefix)
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
    sdk, interpreter, module = _sdk(directory)
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
            patcher.setattr(_maintenance, "SystemHostServiceManager", lambda *_args, **_kwargs: _Manager())
            patcher.setattr(_mcp_lifecycle, "native_mcp_scope", lambda: scope)
            patcher.setattr(roots, "_locator_name", lambda: native_root.name)
            patcher.setenv("HZ_STATE_DIR", str(directory / "forged"))
            patcher.setattr(cli, "boot", lambda: pytest.fail("Project discovery ran"))
            if phase == "foreign-unit":
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
@pytest.mark.parametrize("phase", ["start", "recover", "sdk-changed", "lock-changed", "foreign-unit", "decline"])
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
                if phase != "foreign-unit":
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
    if phase in {"foreign-unit", "decline"}:
        assert not stdout
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
