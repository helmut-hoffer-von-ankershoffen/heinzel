"""Offline migration can inspect a local port without gaining outbound network operations."""

import ast
import platform
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
SOURCE = Path(__file__).parents[1] / "e2e/offline_uninstall_test.py"
DENIAL = next(
    ast.literal_eval(node.value)
    for node in ast.parse(SOURCE.read_text()).body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == "DENY_LINUX" for target in node.targets)
)


def filter_rows(machine):
    """Read the actual fixture program up to installation, never applying seccomp to pytest."""
    prefix = DENIAL.partition("filters =")[0].replace("platform.machine()", repr(machine))
    namespace = {}
    exec(compile(prefix, str(SOURCE), "exec"), namespace)  # noqa: S102 - fixed repository fixture
    return namespace["rows"]


def verdict(rows, architecture, number):
    accumulator, index = 0, 0
    while index < len(rows):
        code, yes, no, value = rows[index]
        if code == 0x20:
            accumulator = {0: number, 4: architecture, 16: 2}[value]
        elif code == 0x15:
            index += yes if accumulator == value else no
        elif code == 0x45:
            index += yes if accumulator & value else no
        elif code == 0x06:
            return value
        else:
            msg = f"Unexpected filter instruction {code}"
            raise AssertionError(msg)
        index += 1
    msg = "Filter did not return"
    raise AssertionError(msg)


@pytest.mark.parametrize(
    ("machine", "architecture", "local", "outbound"),
    [
        ("x86_64", 0xC000003E, (41, 49, 50), (42, 44, 46, 307, 425)),
        ("aarch64", 0xC00000B7, (198, 200, 201), (203, 206, 211, 269, 425)),
    ],
)
def test_network_filter_permits_port_inspection_but_denies_outbound_and_async_io(
    machine, architecture, local, outbound
):
    rows = filter_rows(machine)
    assert all(verdict(rows, architecture, number) == 0x7FFF0000 for number in local)
    assert all(verdict(rows, architecture, number) == 0x00050001 for number in outbound)
    assert verdict(rows, 0, local[0]) == 0x80000000
    if machine == "x86_64":
        assert all(verdict(rows, architecture, number | 0x40000000) == 0x00050001 for number in outbound)


@pytest.mark.skipif(sys.platform != "linux", reason="Actual seccomp execution needs Linux")
def test_actual_linux_sandbox_allows_local_bind_and_refuses_outbound_before_destination_lookup():
    denied = {"x86_64": (42, 44, 46, 307, 425), "aarch64": (203, 206, 211, 269, 425)}[platform.machine()]
    probe = f"""import ctypes, errno, socket
with socket.socket() as listener:
    listener.bind(('127.0.0.1', 0))
    assert listener.getsockname()[1] > 0
    try:
        listener.connect(('127.0.0.1', listener.getsockname()[1]))
    except OSError as error:
        assert error.errno == errno.EPERM, error
    else:
        raise AssertionError('connect was allowed')
libc = ctypes.CDLL(None, use_errno=True)
for number in {denied!r}:
    ctypes.set_errno(0)
    assert libc.syscall(number, -1, 0, 0, 0, 0, 0) == -1
    assert ctypes.get_errno() == errno.EPERM, (number, ctypes.get_errno())
print('local bind allowed; all outbound and async operations denied')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", DENIAL, sys.executable, "-I", "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "local bind allowed; all outbound and async operations denied"
