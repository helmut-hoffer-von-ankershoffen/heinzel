"""Installed CLI foreground lifecycle and isolated native-manager preflight contracts."""

import json
import os
import pty
import re
import select
import shutil
import socket
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

from heinzel.heads import cli
from heinzel.heads.mcp import _ipc_lifecycle as lifecycle
from heinzel.heads.mcp import _ipc_runtime_root as roots
from heinzel.heads.mcp._ipc_admission import McpGeneration
from heinzel.heads.mcp._ipc_approval import PrivateMcpPlan
from heinzel.kernel import HostServiceManager, host_maintenance_directory
from heinzel.kernel._mcp_native import McpNativePlan
from heinzel.kernel._mcp_storage import InstalledMcpRuntime

pytestmark = pytest.mark.unit

_REGISTRATION = "fixture-foreground"
_START_REFUSED = {"decline", "changed", "manager-changed", "wrong-start-ack"}
_LAUNCHER = """
import fcntl, runpy, sys, termios
from pathlib import Path
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
runpy.run_path(sys.argv[1])['_exercise_installed_cli'](Path(sys.argv[2]), sys.argv[3])
"""


class _Manager(HostServiceManager):
    def __init__(self, stdout, code=0):
        self.stdout = stdout
        self.code = code
        self.commands = []

    def command(self, arguments):
        self.commands.append(arguments)
        return subprocess.CompletedProcess(arguments, self.code, self.stdout, "")


def _invoke(patcher, host, action, registration=_REGISTRATION):
    patcher.setattr(sys, "argv", ["hz", "system", "mcp", action, registration])
    with pytest.raises(SystemExit) as status:
        cli.main(host_mcp_lifecycle=host)
    return status.value.code


def _exercise_running(patcher, host, installation, native_root, phase):
    assert host._worker is not None
    pid = host._worker.child.pid
    (receipt,) = host_maintenance_directory(installation.prefix).glob("mcp-activation-*.json")
    locator = Path(json.loads(receipt.read_bytes())["locator"])
    assert locator.parent.parent == native_root
    assert locator.is_socket()
    assert _invoke(patcher, host, "check") == 1  # No MCP readiness proof yet.
    assert host._worker.child.pid == pid
    assert _invoke(patcher, host, "stop") == (1 if phase == "wrong-stop-ack" else 0)
    if phase == "wrong-stop-ack":
        assert host._worker.stopping
    else:
        with pytest.raises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)  # The exact direct child was reaped before the CLI returned.
        assert _invoke(patcher, host, "stop") == 0
    assert locator.is_socket()
    with pytest.raises(ConnectionRefusedError), socket.socket(socket.AF_UNIX) as client:
        client.connect(os.fspath(locator))


def _exercise_installed_cli(directory, phase):
    sdk, state = directory / "sdk", directory / "state"
    sdk.mkdir(mode=0o700)
    state.mkdir(mode=0o700)
    installation = InstalledMcpRuntime(sdk, state)
    plan = PrivateMcpPlan.capture(
        McpGeneration(_REGISTRATION, 4), "fixture-only", 3, time.monotonic() + 120, {"dispatch": {}}
    )
    registration = lifecycle.ForegroundMcpRegistration.capture(installation, plan)
    manager = _Manager("PID\tStatus\tLabel\n-\t0\tunrelated\n")
    host = lifecycle.ForegroundMcpLifecycle(registration, manager=manager)
    native_root = Path(os.confstr(65537)).resolve() / roots._locator_name()
    assert not native_root.exists()

    def forbidden_boot():
        pytest.fail("The foreground lifecycle booted the project kernel")

    try:
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(cli, "boot", forbidden_boot)
            patcher.setattr(roots, "_locator_name", lambda: native_root.name)
            _inject_lifecycle_race(patcher, phase, manager, registration.native.name)
            assert _invoke(patcher, host, "start") == 1
            if phase in _START_REFUSED:
                assert host._worker is None
                if phase == "wrong-start-ack":
                    (locator,) = native_root.glob("g-*/s")
                    with pytest.raises(ConnectionRefusedError), socket.socket(socket.AF_UNIX) as client:
                        client.connect(os.fspath(locator))
                else:
                    assert not native_root.exists()
            else:
                _exercise_running(patcher, host, installation, native_root, phase)
            assert manager.commands
            assert all(command == ["/bin/launchctl", "list"] for command in manager.commands)
    finally:
        host.close()
        if native_root.is_dir():
            shutil.rmtree(native_root)


def _inject_lifecycle_race(patcher, phase, manager, name):
    if phase == "manager-changed":
        command = manager.command

        def changed(arguments):
            if manager.commands:
                manager.stdout += f"99\t0\t{name}\n"
            return command(arguments)

        patcher.setattr(manager, "command", changed)
    if phase in {"wrong-start-ack", "wrong-stop-ack"}:
        reply = lifecycle._reply
        wrong = b"T" if phase == "wrong-start-ack" else b"S"

        def wrong_ack(channel, binding, status):
            reply(channel, binding, b"U" if status == wrong else status)

        patcher.setattr(lifecycle, "_reply", wrong_ack)


def _read_review(terminal):
    deadline = time.monotonic() + 2
    output = bytearray()
    while b"\r\n> " not in output:
        remaining = deadline - time.monotonic()
        assert remaining > 0
        assert select.select([terminal], [], [], remaining)[0]
        output.extend(os.read(terminal, 4096))
    return bytes(output)


def _answer_reviews(terminal, directory, phase):
    actions = ["start"]
    if phase in {"success", "wrong-stop-ack"}:
        actions += ["check", "stop"]
    if phase == "success":
        actions += ["stop"]
    for action in actions:
        prompt = _read_review(terminal)
        assert b"Review this exact MCP foreground lifecycle action" in prompt
        assert f'"action": "{action}"'.encode() in prompt
        assert b'"registration": "fixture-foreground"' in prompt
        assert b'"grant_revision": 3' in prompt
        match = re.search(rb"Type exactly: (approve MCP [0-9a-f]{8})\r\n", prompt)
        assert match is not None
        if phase == "changed":
            (directory / "state").rename(directory / "old-state")
            (directory / "state").mkdir(mode=0o700)
        os.write(terminal, (b"no" if phase == "decline" else match[1]) + b"\n")


@pytest.mark.skipif(sys.platform != "darwin", reason="Exercises a real Darwin locator and owned foreground child")
@pytest.mark.parametrize(
    "phase", ["success", "decline", "changed", "manager-changed", "wrong-start-ack", "wrong-stop-ack"]
)
def test_installed_foreground_cli_reviews_action_and_proves_owned_worker_exit(short_socket_root, phase):
    master, terminal = pty.openpty()
    settings = termios.tcgetattr(terminal)
    settings[3] &= ~termios.ECHO
    termios.tcsetattr(terminal, termios.TCSANOW, settings)
    try:
        with subprocess.Popen(
            [sys.executable, "-I", "-c", _LAUNCHER, __file__, os.fspath(short_socket_root), phase],
            stdin=terminal,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        ) as process:
            os.close(terminal)
            terminal = -1
            try:
                _answer_reviews(master, short_socket_root, phase)
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
    if phase in _START_REFUSED:
        assert stdout == b""
        assert stderr.endswith(b"The reviewed MCP lifecycle action was declined or unavailable.\n")
    else:
        states = [json.loads(line) for line in stdout.splitlines()]
        assert [(state["action"], state["worker"], state["transport"]) for state in states[:2]] == [
            ("start", "running", "reachable"),
            ("check", "running", "reachable"),
        ]
        assert all(state["mcp_readiness"] == "unverified" and state["manager"] == "absent" for state in states)
        assert [state["worker"] for state in states[2:]] == (
            ["stop-incomplete"] if phase == "wrong-stop-ack" else ["stopped", "stopped"]
        )


@pytest.mark.parametrize("action", ["start", "check", "stop"])
def test_standalone_cli_cannot_assert_installed_host_authority(tmp_path, action):
    result = subprocess.run(
        [sys.executable, "-I", "-m", "heinzel", "system", "mcp", action, _REGISTRATION],
        cwd=tmp_path,
        env={"HZ_MCP_HOST": "true", "HZ_STATE_DIR": os.fspath(tmp_path / "forged")},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=2,
        check=False,
    )
    assert (result.returncode, result.stdout, result.stderr) == (
        1,
        b"",
        b"MCP lifecycle requires a registration from the trusted installed host.\n",
    )
    assert not list(tmp_path.iterdir())


def test_native_manager_preflight_observes_only_exact_namespace():
    mac = McpNativePlan("darwin", "heinzel.test.mcp")
    header = "PID\tStatus\tLabel\n"
    assert mac.observe(_Manager(header + "-\t0\theinzel.test.mcp.other\n")) == "absent"
    assert mac.observe(_Manager(header + "55\t0\theinzel.test.mcp\n")) == "conflict"
    assert mac.observe(_Manager(header + "malformed output\n")) == "unavailable"
    linux = McpNativePlan("linux", "hz-mcp-fixture.service")
    absent = (
        "Id=hz-mcp-fixture.service\nLoadState=not-found\nFragmentPath=\nDropInPaths=\nMainPID=0\nControlPID=0\nJob=\n"
    )
    manager = _Manager(absent)
    assert linux.observe(manager) == "absent"
    assert manager.commands[0][:4] == ["/usr/bin/systemctl", "--user", "show", linux.name]
    assert linux.observe(_Manager(absent.replace("MainPID=0", "MainPID=55"))) == "conflict"
    assert linux.observe(_Manager(absent + "Id=foreign.service\n")) == "unavailable"
