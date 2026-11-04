"""Offline migration keeps port inspection and Unix IPC without gaining outbound IP access."""

import ast
import ctypes
import os
import platform
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit
SOURCE = Path(__file__).parents[1] / "e2e/offline_uninstall_test.py"
DENIAL = next(
    ast.literal_eval(node.value)
    for node in ast.parse(SOURCE.read_text()).body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == "DENY_LINUX" for target in node.targets)
)
ALLOW, DENY = 0x7FFF0000, 0x00050001


def filter_rows(machine):
    """Read the actual fixture program without installing a policy in pytest."""
    prefix = DENIAL.partition("filters =")[0].replace("platform.machine()", repr(machine))
    namespace = {}
    exec(compile(prefix, str(SOURCE), "exec"), namespace)  # noqa: S102 - fixed repository fixture
    return namespace["rows"]


def verdict(rows, architecture, number, arguments=(0, 0, 0, 0, 0, 0)):
    accumulator, index = 0, 0
    data = {0: number, 4: architecture} | {16 + 8 * i: value for i, value in enumerate(arguments)}
    while index < len(rows):
        code, yes, no, value = rows[index]
        if code == 0x20:
            accumulator = data[value] & 0xFFFFFFFF
        elif code == 0x15:
            index += yes if accumulator == value else no
        elif code == 0x45:
            index += yes if accumulator & value else no
        elif code == 0x54:
            accumulator &= value
        elif code == 0x06:
            return value
        else:
            msg = f"Unexpected filter instruction {code}"
            raise AssertionError(msg)
        index += 1
    msg = "Filter did not return"
    raise AssertionError(msg)


@pytest.mark.parametrize(
    ("machine", "architecture", "socket_call", "connect", "sends"),
    [
        ("x86_64", 0xC000003E, 41, 42, ((44, 3), (46, 2), (307, 3))),
        ("aarch64", 0xC00000B7, 198, 203, ((206, 3), (211, 2), (269, 3))),
    ],
)
def test_seccomp_keeps_unix_ipc_and_only_landlock_mediated_tcp(machine, architecture, socket_call, connect, sends):
    rows = filter_rows(machine)
    for family in (1, 2, 10):
        for flags in (0, 0x800, 0x80000, 0x80800):
            for protocol in (0, 6) if family != 1 else (0,):
                assert verdict(rows, architecture, socket_call, (family, 1 | flags, protocol)) == ALLOW
    for family in (2, 10):
        for kind in (2, 3, 5, 1 | 0x100):
            assert verdict(rows, architecture, socket_call, (family, kind, 0)) == DENY
        for protocol in (17, 132, 262):  # UDP, SCTP and MPTCP cannot bypass TCP mediation.
            assert verdict(rows, architecture, socket_call, (family, 1, protocol)) == DENY
    assert verdict(rows, architecture, socket_call, (17, 3, 0)) == DENY  # AF_PACKET
    assert verdict(rows, architecture, socket_call, (1, 2, 0)) == ALLOW
    local_calls = (49, 50) if machine == "x86_64" else (200, 201)
    assert all(verdict(rows, architecture, local) == ALLOW for local in local_calls)
    assert verdict(rows, architecture, connect) == ALLOW  # Landlock, not seccomp, distinguishes socket domains.
    for number, flags_argument in sends:
        args = [0] * 6
        assert verdict(rows, architecture, number, args) == ALLOW
        args[flags_argument] = 0x20000000 | 0x4000
        assert verdict(rows, architecture, number, args) == DENY
    assert verdict(rows, architecture, 425) == DENY
    assert verdict(rows, 0, socket_call) == 0x80000000
    if machine == "x86_64":
        for number in (socket_call, connect, 425, *(item[0] for item in sends)):
            assert verdict(rows, architecture, number | 0x40000000) == DENY


@pytest.mark.parametrize(
    "failure", ["abi", "unsupported", "privileges", "create", "restrict", "close", "seccomp", None]
)
def test_both_policies_must_be_installed_and_ruleset_closed_before_exec(monkeypatch, capsys, failure):
    calls = []

    class Syscall:
        def __call__(self, number, *args):
            if number == 444 and args[-1] == 1:
                calls.append("abi")
                return -1 if failure == "abi" else 3 if failure == "unsupported" else 4
            if number == 444:
                calls.append("create")
                assert args[0]._obj.handled_access_fs == 0
                assert args[0]._obj.handled_access_net == 2
                assert args[1:] == (16, 0)
                return -1 if failure == "create" else 81
            assert (number, args) == (446, (81, 0))
            calls.append("restrict")
            return -1 if failure == "restrict" else 0

    def prctl(operation, *args):
        stage = "privileges" if operation == 38 else "seccomp"
        assert operation in (38, 22)
        assert args[0] == (1 if operation == 38 else 2)
        calls.append(stage)
        return -1 if failure == stage else 0

    def close(fd):
        assert fd == 81
        calls.append("close")
        if failure == "close":
            msg = "close"
            raise OSError(msg)

    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(syscall=Syscall(), prctl=prctl))
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(os, "close", close)
    monkeypatch.setattr(os, "execvpe", lambda *args: calls.append("exec"))
    monkeypatch.setattr(sys, "argv", ["denial", "fixed-test-command"])
    if failure:
        with pytest.raises((SystemExit, OSError)):
            exec(compile(DENIAL, str(SOURCE), "exec"), {})  # noqa: S102 - fixed fixture, OS calls replaced
        assert "exec" not in calls
        if failure in {"restrict", "close", "seccomp"}:
            assert "close" in calls
    else:
        exec(compile(DENIAL, str(SOURCE), "exec"), {})  # noqa: S102 - fixed fixture, OS calls replaced
        assert calls == ["abi", "privileges", "create", "restrict", "close", "seccomp", "exec"]
        assert "Landlock ABI 4" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform != "linux", reason="Actual Linux policies require Linux")
def test_actual_linux_policies_preserve_bind_unix_and_exec_inheritance_while_denying_ip():
    probe = """import ctypes, errno, os, platform, socket, subprocess, sys, tempfile
for family, address in ((socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')):
    with socket.socket(family) as listener:
        listener.bind((address, 0)); listener.listen()
        with socket.socket(family) as client:
            assert client.connect_ex((address, listener.getsockname()[1])) in (errno.EPERM, errno.EACCES)
    forbidden = ((socket.SOCK_DGRAM, 0), (socket.SOCK_RAW, 6), (socket.SOCK_STREAM, 132), (socket.SOCK_STREAM, 262))
    for kind, protocol in forbidden:
        try:
            socket.socket(family, kind, protocol)
        except OSError as error:
            assert error.errno == errno.EPERM, error
        else:
            raise AssertionError('non-TCP socket allowed')
with tempfile.TemporaryDirectory(prefix='hz-ipc-', dir='/tmp') as directory:
    with socket.socket(socket.AF_UNIX) as server, socket.socket(socket.AF_UNIX) as client:
        address = os.path.join(directory, 'ipc'); server.bind(address); server.listen()
        client.connect(address); connection, _ = server.accept()
        with connection:
            client.sendall(b'local'); assert connection.recv(5) == b'local'
            connection.sendmsg([b'reply']); assert client.recv(5) == b'reply'
libc = ctypes.CDLL(None, use_errno=True)
sends = {'x86_64': ((44,3),(46,2),(307,3)), 'aarch64': ((206,3),(211,2),(269,3))}[platform.machine()]
for number, argument in (*sends, (425, 0)):
    args = [-1, 0, 0, 0, 0, 0]; args[argument] = 0x20000000
    ctypes.set_errno(0)
    assert libc.syscall(number, *args) == -1
    assert ctypes.get_errno() == errno.EPERM, (number, ctypes.get_errno())
child = "import errno,socket; s=socket.socket(); s.bind(('127.0.0.1',0)); "
child += "assert s.connect_ex(('127.0.0.1',9)) in (errno.EPERM,errno.EACCES)"
subprocess.run([sys.executable, '-I', '-c', child], check=True, timeout=3)
print('bind, Unix roundtrip and inherited IP denial verified')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", DENIAL, sys.executable, "-I", "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "Offline network denial active: Landlock ABI" in result.stderr
    assert result.stdout.strip() == "bind, Unix roundtrip and inherited IP denial verified"
