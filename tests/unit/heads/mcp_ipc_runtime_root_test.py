"""Isolated real macOS activation and simulated Linux runtime-root contracts."""

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest import TestCase

import pytest

from heinzel import HeinzelError
from heinzel.heads.mcp import _ipc_bridge as bridge
from heinzel.heads.mcp import _ipc_runtime_root as roots
from heinzel.heads.mcp import _ipc_supervisor as supervisor_module
from heinzel.heads.mcp._ipc_admission import McpGeneration
from heinzel.heads.mcp._ipc_approval import PrivateMcpPlan
from heinzel.heads.mcp._ipc_supervisor import owned_installed_mcp_supervisor, owned_mcp_supervisor
from heinzel.kernel._host_maintenance import (
    MaintenanceLocks,
    host_maintenance_directory,
    host_maintenance_gate,
    host_maintenance_lock,
)
from heinzel.kernel._mcp_storage import InstalledMcpRuntime

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
    return InstalledMcpRuntime(prefix, state)


def _plan():
    return PrivateMcpPlan.capture(McpGeneration("fixture", 2), "reviewed-grant", 1, 2000, {})


def _assert_activation_log(records, outcome):
    activation = [record for record in records if record.message == "mcp.ipc.activate_installed"]
    assert [record.phase for record in activation] == ["start", "end"]
    assert activation[-1].outcome == outcome


def _mac_activation(directory, _base):
    installation = _installation(directory)
    native = Path(os.confstr(65537)).resolve()
    paths = [native / roots._locator_name() for _ in range(2)]
    assert not any(path.exists() for path in paths)
    os.environ["TMPDIR"] = os.fspath(directory / "forged")
    os.environ["XDG_RUNTIME_DIR"] = os.environ["TMPDIR"]
    inventory = host_maintenance_directory(installation.prefix)
    patcher = pytest.MonkeyPatch()
    names = iter(path.name for path in paths)
    patcher.setattr(roots, "_locator_name", lambda: next(names))
    try:
        with owned_installed_mcp_supervisor(installation, _plan(), clock=lambda: 100.0) as host:
            locator = host.discovery_locator()
            assert locator is not None
            assert locator.parent.parent == paths[0]
            assert host.check_transport() == McpGeneration("fixture", 2)
            (record,) = inventory.glob("mcp-activation-*.json")
            generation = json.loads(record.read_bytes())
            assert generation["kind"] == "mcp-locator-observation"
            assert generation["storage"]["locator_root"] == os.fspath(paths[0])
            assert generation["storage"]["observed_directory"] == [paths[0].stat().st_dev, paths[0].stat().st_ino]
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
            assert newer.parent.parent == paths[1]
        _assert_activation_log(logged.records, "ok")
        assert len(list(inventory.glob("mcp-activation-*.json"))) == 2
    finally:
        patcher.undo()
        # Retention is production behavior. Only uniquely selected test directories are cleaned.
        for path in paths:
            if path.is_dir():
                shutil.rmtree(path)


def _linux_resolution(directory, base):
    installation = _installation(directory)
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(sys, "platform", "linux")
        patcher.setenv("XDG_RUNTIME_DIR", os.fspath(directory / "forged"))
        assert roots._os_runtime_base() == Path("/run/user") / str(os.getuid())
        patcher.setattr(roots, "_os_runtime_base", lambda: base)
        with roots.mcp_runtime_locator(installation) as runtime:
            assert runtime.path.parent == base
            identity = os.fstat(runtime.descriptor).st_ino
            saved = base / "old"
            runtime.path.rename(saved)
            runtime.path.mkdir(mode=0o700)
            with pytest.raises(HeinzelError, match="runtime root is unavailable or changed"):
                runtime.require_current()
        with roots.mcp_runtime_locator(installation) as restarted:
            assert restarted.path not in {runtime.path, saved}
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
            with pytest.raises(HeinzelError, match=error), roots.mcp_runtime_locator(installation):
                pytest.fail("An unsafe runtime root was provisioned")
        assert not list(physical.iterdir())
        assert not list(long.iterdir())
        patcher.setattr(roots, "_os_runtime_base", lambda: physical)
        physical.chmod(0o770)
        with (
            pytest.raises(HeinzelError, match="unavailable or changed"),
            roots.mcp_runtime_locator(installation),
        ):
            pytest.fail("A group-writable OS directory was accepted")
        physical.chmod(0o700)
        unrecorded = physical / roots._locator_name()
        patcher.setattr(roots, "_locator_name", lambda: unrecorded.name)
        unrecorded.mkdir(mode=0o700)
        with (
            pytest.raises(HeinzelError, match="unavailable or changed"),
            roots.mcp_runtime_locator(installation),
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
        assert not list(gate.parent.glob("mcp-activation-*.json"))


def _anchored_generation(directory, base):
    installation = _installation(directory)
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(roots, "_os_runtime_base", lambda: base)
        with roots.mcp_runtime_locator(installation) as runtime:
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


def _preopen_substitution(directory, base):
    installation = _installation(directory)
    selected = base / roots._locator_name()
    saved = base / "original"
    marker = selected / "foreign"
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(roots, "_os_runtime_base", lambda: base)
        patcher.setattr(roots, "_locator_name", lambda: selected.name)
        open_descriptor = os.open

        def substitute(path, *args, **kwargs):
            if path == selected.name and kwargs.get("dir_fd") is not None and not saved.exists():
                selected.rename(saved)
                selected.mkdir(mode=0o700)
                marker.write_bytes(b"foreign data")
            return open_descriptor(path, *args, **kwargs)

        patcher.setattr(os, "open", substitute)
        with owned_installed_mcp_supervisor(installation, _plan(), clock=lambda: 100.0) as host:
            locator = host.discovery_locator()
            assert locator.parent.parent == selected
            observed = marker.stat()
            assert not list(saved.iterdir())
            with socket.socket(socket.AF_UNIX) as impostor:
                impostor.settimeout(0.05)
                impostor.connect(os.fspath(locator))
                impostor.sendall(b'{"registration":"fixture","generation":2}\n')
                with pytest.raises(TimeoutError):
                    impostor.recv(1)
                assert not host.pending_review_ids()
        with pytest.raises(ConnectionRefusedError), socket.socket(socket.AF_UNIX) as stale:
            stale.connect(os.fspath(locator))
        (receipt,) = host_maintenance_directory(installation.prefix).glob("mcp-activation-*.json")
        data = json.loads(receipt.read_bytes())
        assert data["kind"] == "mcp-locator-observation"
        assert data["storage"]["observed_directory"] == [selected.stat().st_dev, selected.stat().st_ino]
        with (
            pytest.raises(HeinzelError, match="unavailable or changed"),
            owned_installed_mcp_supervisor(installation, _plan(), clock=lambda: 100.0),
        ):
            pytest.fail("A stale receipt enabled activation over a substitute")
        assert marker.read_bytes() == b"foreign data"
        assert (marker.stat().st_ino, marker.stat().st_mtime_ns) == (observed.st_ino, observed.st_mtime_ns)
        assert locator.is_socket()


def _inventory_failure(directory, base):
    installation = _installation(directory)
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(roots, "_os_runtime_base", lambda: base)
        record = roots._record

        def write_uncertain(descriptor, name, data):
            record(descriptor, name, data[:8])
            raise OSError

        with pytest.MonkeyPatch.context() as writes:
            writes.setattr(roots, "_record", write_uncertain)
            with (
                pytest.raises(HeinzelError, match="unavailable or changed"),
                owned_installed_mcp_supervisor(installation, _plan(), clock=lambda: 100.0),
            ):
                pytest.fail("An uncertain inventory transaction published a supervisor")
        (retired,) = base.glob("hz-m-*/g-*/s")
        with pytest.raises(ConnectionRefusedError), socket.socket(socket.AF_UNIX) as stale:
            stale.connect(os.fspath(retired))
        inventory = host_maintenance_directory(installation.prefix)
        (uncertain,) = inventory.glob("mcp-activation-*.json")
        assert len(uncertain.read_bytes()) == 8
        with owned_installed_mcp_supervisor(installation, _plan(), clock=lambda: 100.0) as host:
            locator = host.discovery_locator()
            assert locator.parent.parent != retired.parent.parent
            (current,) = set(inventory.glob("mcp-activation-*.json")) - {uncertain}
            data = current.read_bytes()
            current.write_bytes(b"changed")
            with pytest.raises(HeinzelError, match="unavailable or changed"):
                host.check_transport()
            with pytest.raises(ConnectionRefusedError), socket.socket(socket.AF_UNIX) as stale:
                stale.connect(os.fspath(locator))
            current.write_bytes(data)
            with pytest.raises(HeinzelError, match="no longer owns the registration"), host.open_transport():
                pytest.fail("Restoring an inventory file revived a retired supervisor")
        assert len(uncertain.read_bytes()) == 8
        with pytest.raises(ConnectionRefusedError), socket.socket(socket.AF_UNIX) as stale:
            stale.connect(os.fspath(locator))


def _replayed_bridge_hello(directory, base):
    installation = _installation(directory)
    old_hello = []
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(roots, "_os_runtime_base", lambda: base)
        launch = bridge._bridge_child
        start = supervisor_module.launch_inherited_bridge

        def remember(channel, handshake):
            old_hello.append(handshake.hello)
            return start(channel, handshake)

        with (
            owned_installed_mcp_supervisor(installation, _plan(), clock=lambda: 100.0) as first,
            pytest.MonkeyPatch.context() as first_only,
        ):
            first_only.setattr(supervisor_module, "launch_inherited_bridge", remember)
            with first.launch_bridge():
                pass  # Close the first child without granting a lease.

        def replay(front, channel, handshake):
            launch(front, channel, replace(handshake, hello=old_hello[0]))

        patcher.setattr(bridge, "_bridge_child", replay)
        with (
            owned_installed_mcp_supervisor(installation, _plan(), clock=lambda: 100.0) as second,
            second.launch_bridge() as (front, transport),
        ):
            with pytest.raises(HeinzelError, match="could not read the bootstrap locator"), transport.admit():
                pytest.fail("A previous supervisor's private bridge greeting was accepted")
            front.settimeout(2)
            assert front.recv(1) == b""
            assert not second.pending_review_ids()


@pytest.mark.parametrize(
    "fixture",
    [
        "_mac_activation",
        "_linux_resolution",
        "_root_refusals",
        "_anchored_generation",
        "_preopen_substitution",
        "_inventory_failure",
        "_replayed_bridge_hello",
    ],
)
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
