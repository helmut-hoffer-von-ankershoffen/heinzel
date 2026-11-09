"""Isolated real macOS activation and simulated Linux runtime-root contracts."""

import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from unittest import TestCase

import pytest

from heinzel import HeinzelError
from heinzel.heads.mcp import _ipc_runtime_root as roots
from heinzel.heads.mcp._ipc_admission import McpGeneration
from heinzel.heads.mcp._ipc_approval import PrivateMcpPlan
from heinzel.heads.mcp._ipc_supervisor import owned_installed_mcp_supervisor, owned_mcp_supervisor
from heinzel.kernel._host_maintenance import (
    MaintenanceLocks,
    host_maintenance_directory,
    host_maintenance_gate,
    host_maintenance_lock,
)

pytestmark = pytest.mark.unit

_FIXTURE = """
import runpy, sys
from pathlib import Path
runpy.run_path(sys.argv[1])[sys.argv[2]](Path(sys.argv[3]), Path(sys.argv[4]))
"""


def _installation(directory):
    prefix, state = directory / "sdk", directory / "state"
    prefix.mkdir(mode=0o700)
    state.mkdir(mode=0o700)
    return roots.InstalledMcpRuntime(prefix, state)


def _runtime_name(installation):
    key = hashlib.sha256(os.fsencode(installation.prefix) + b"\0" + os.fsencode(installation.state)).hexdigest()
    return "hz-m-" + key[:16]


def _plan():
    return PrivateMcpPlan.capture(McpGeneration("fixture", 2), "reviewed-grant", 1, 2000, {})


def _assert_activation_log(records, outcome):
    activation = [record for record in records if record.message == "mcp.ipc.activate_installed"]
    assert [record.phase for record in activation] == ["start", "end"]
    assert activation[-1].outcome == outcome


def _mac_activation(directory, _base):
    installation = _installation(directory)
    native = Path(os.confstr(65537)).resolve()
    expected = native / _runtime_name(installation)
    assert not expected.exists()
    os.environ["TMPDIR"] = os.fspath(directory / "forged")
    os.environ["XDG_RUNTIME_DIR"] = os.environ["TMPDIR"]
    inventory = host_maintenance_directory(installation.prefix)
    try:
        with owned_installed_mcp_supervisor(installation, _plan(), clock=lambda: 100.0) as host:
            locator = host.discovery_locator()
            assert locator is not None
            assert locator.parent.parent == expected
            assert host.check_transport() == McpGeneration("fixture", 2)
            records = {path.name: json.loads(path.read_bytes()) for path in inventory.glob("mcp-root-*.json")}
            (root_name,) = (name for name, record in records.items() if "runtime_root" in record)
            (generation,) = (record for record in records.values() if "root_record" in record)
            assert records[root_name]["runtime_root"] == os.fspath(expected)
            assert records[root_name]["runtime_root_identity"] == [expected.stat().st_dev, expected.stat().st_ino]
            assert generation["root_record"] == root_name
            assert generation["socket"] == [locator.stat().st_dev, locator.stat().st_ino]
            assert generation["generation"] == 2
            with (
                pytest.raises(HeinzelError, match="integration change is in progress"),
                MaintenanceLocks((host_maintenance_lock(installation.prefix),)),
            ):
                pytest.fail("SDK maintenance proceeded while the owned supervisor was active")
        with pytest.raises(ConnectionRefusedError), socket.socket(socket.AF_UNIX) as stale:
            stale.connect(os.fspath(locator))
        with (
            TestCase().assertLogs("heinzel.mcp") as logged,
            owned_installed_mcp_supervisor(installation, _plan(), clock=lambda: 100.0) as restarted,
        ):
            newer = restarted.discovery_locator()
            assert newer != locator
            assert newer.parent.parent == expected
        _assert_activation_log(logged.records, "ok")
        assert len(list(inventory.glob("mcp-root-*.json"))) == 3
    finally:
        # Retention is production behavior. Only the unique OS directory derived from this test installation is cleaned.
        if expected.is_dir():
            shutil.rmtree(expected)


def _linux_resolution(directory, base):
    installation = _installation(directory)
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(sys, "platform", "linux")
        patcher.setenv("XDG_RUNTIME_DIR", os.fspath(directory / "forged"))
        assert roots._os_runtime_base() == Path("/run/user") / str(os.getuid())
        patcher.setattr(roots, "_os_runtime_base", lambda: base)
        with roots.owned_mcp_runtime_root(installation) as runtime:
            assert runtime.path == base / _runtime_name(installation)
            identity = os.fstat(runtime.descriptor).st_ino
            saved = base / "old"
            runtime.path.rename(saved)
            runtime.path.mkdir(mode=0o700)
            with pytest.raises(HeinzelError, match="runtime root is unavailable or changed"):
                runtime.require_current()
        with (
            pytest.raises(HeinzelError, match="runtime root is unavailable or changed"),
            roots.owned_mcp_runtime_root(installation),
        ):
            pytest.fail("The installed root adopted a same-UID replacement")
        assert saved.stat().st_ino == identity
        assert not list(runtime.path.iterdir())


def _root_refusals(directory, physical):
    installation = _installation(directory)
    linked = directory / "link"
    linked.symlink_to(physical, target_is_directory=True)
    long = directory / ("x" * 87)
    long.mkdir(mode=0o700)
    with pytest.MonkeyPatch.context() as patcher:
        for base, error in [(linked, "unavailable or changed"), (long, "exceeds the Unix socket path budget")]:
            patcher.setattr(roots, "_os_runtime_base", lambda base=base: base)
            with pytest.raises(HeinzelError, match=error), roots.owned_mcp_runtime_root(installation):
                pytest.fail("An unsafe runtime root was provisioned")
        assert not list(physical.iterdir())
        assert not list(long.iterdir())
        patcher.setattr(roots, "_os_runtime_base", lambda: physical)
        physical.chmod(0o770)
        with (
            pytest.raises(HeinzelError, match="unavailable or changed"),
            roots.owned_mcp_runtime_root(installation),
        ):
            pytest.fail("A group-writable OS directory was accepted")
        physical.chmod(0o700)
        unrecorded = physical / _runtime_name(installation)
        unrecorded.mkdir(mode=0o700)
        with (
            pytest.raises(HeinzelError, match="unavailable or changed"),
            roots.owned_mcp_runtime_root(installation),
        ):
            pytest.fail("An unrecorded same-UID root was adopted")
        assert list(physical.iterdir()) == [unrecorded]
        gate = host_maintenance_gate(installation.prefix)
        gate.write_text("pending")
        unrecorded.rmdir()
        with (
            TestCase().assertLogs("heinzel.mcp") as logged,
            pytest.raises(HeinzelError, match="unavailable or changed"),
            owned_installed_mcp_supervisor(installation, _plan(), clock=lambda: 100.0),
        ):
            pytest.fail("Activation bypassed the SDK maintenance gate")
        _assert_activation_log(logged.records, "error")
        assert not list(physical.iterdir())
        assert gate.read_text() == "pending"
        assert not list(gate.parent.glob("mcp-root-*.json"))


def _anchored_generation(directory, base):
    installation = _installation(directory)
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(roots, "_os_runtime_base", lambda: base)
        with roots.owned_mcp_runtime_root(installation) as runtime:
            mkdir = os.mkdir
            saved = base / "old"

            def replace_after_check(path, *args, **kwargs):
                if isinstance(path, str) and path.startswith("g-"):
                    runtime.path.rename(saved)
                    mkdir(runtime.path, mode=0o700)
                return mkdir(path, *args, **kwargs)

            patcher.setattr(os, "mkdir", replace_after_check)
            with (
                pytest.raises(HeinzelError, match="named generation could not be verified"),
                owned_mcp_supervisor(installation.state / "fixture", _plan(), named_root=runtime, clock=lambda: 100.0),
            ):
                pytest.fail("The supervisor published a replaced runtime root")
            assert not list(runtime.path.iterdir())
            (retired,) = saved.glob("g-*/s")
            assert retired.is_socket()
            with pytest.raises(ConnectionRefusedError), socket.socket(socket.AF_UNIX) as client:
                client.connect(os.fspath(retired))


@pytest.mark.parametrize("fixture", ["_mac_activation", "_linux_resolution", "_root_refusals", "_anchored_generation"])
def test_installed_mcp_runtime_root_in_isolated_process(short_socket_root, fixture):
    if fixture == "_mac_activation" and sys.platform != "darwin":
        pytest.skip("The real Darwin resolver requires macOS")
    # The simulated OS base must leave room for the installation-specific suffix as well as the socket.
    simulated = short_socket_root.parent / secrets.token_hex(3)
    simulated.mkdir(mode=0o700)
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                _FIXTURE,
                __file__,
                fixture,
                os.fspath(short_socket_root),
                os.fspath(simulated),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=4,
            check=False,
        )
        assert result.returncode == 0, result.stderr.decode()
    finally:
        shutil.rmtree(simulated)
