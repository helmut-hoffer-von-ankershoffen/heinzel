"""Exercise real named generation startup in a fresh isolated process."""

import os
import secrets
import socket
import stat
import subprocess
import sys
from pathlib import Path
from threading import Event, Thread

import pytest

from heinzel import HeinzelError
from heinzel.heads.mcp._ipc_named_generation import owned_named_generation

pytestmark = pytest.mark.unit

_FIXTURE = """
import runpy, sys
from pathlib import Path
runpy.run_path(sys.argv[1])[sys.argv[2]](Path(sys.argv[3]))
"""


def _start_check_stop(root):
    cwd = Path.cwd()
    mask = os.umask(0)
    os.umask(mask)

    def forbidden(*args, **kwargs):
        pytest.fail("Named activation attempted a pathname mutation")

    with pytest.MonkeyPatch.context() as patcher:
        for name in ("chmod", "unlink", "rename"):
            patcher.setattr(os, name, forbidden)
        with owned_named_generation(root) as first:
            locator = first.checked_locator()
            first.check()
            assert Path.cwd() == cwd
            assert stat.S_IMODE(locator.stat().st_mode) == 0o600
            assert stat.S_IMODE(locator.parent.stat().st_mode) == 0o700
        assert locator.is_socket()
        with pytest.raises(ConnectionRefusedError), socket.socket(socket.AF_UNIX) as stale:
            stale.connect(os.fspath(locator))
        with owned_named_generation(root) as second:
            assert second.checked_locator() != locator
            second.check()
        assert second.locator.is_socket()
        assert locator.is_socket()
    observed = os.umask(mask)
    assert observed == mask


def _ancestor_replaced(root):
    original = os.fchdir
    retained = root.with_name("retained")

    def replace_ancestor(descriptor):
        root.rename(retained)
        root.mkdir(mode=0o700)
        original(descriptor)

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(os, "fchdir", replace_ancestor)
        with (
            pytest.raises(HeinzelError, match="named generation could not be verified"),
            owned_named_generation(root),
        ):
            pytest.fail("A replaced ancestor was published as discovery")
    assert not list(root.iterdir())
    (stranded,) = retained.glob("g-*/s")
    assert stranded.is_socket()
    with pytest.raises(ConnectionRefusedError), socket.socket(socket.AF_UNIX) as client:
        client.connect(os.fspath(stranded))


def _foreign_socket_name(root):
    original_socket = socket.socket

    class CollidingSocket(original_socket):
        def bind(self, address):
            if address == "s":
                collision = Path(address)
                collision.write_text("foreign file")
                collision.chmod(0o640)
            return super().bind(address)

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(socket, "socket", CollidingSocket)
        with (
            pytest.raises(HeinzelError, match="named generation could not be verified"),
            owned_named_generation(root),
        ):
            pytest.fail("The helper replaced an existing name")
    (collision,) = root.glob("g-*/s")
    assert collision.read_text() == "foreign file"
    assert stat.S_IMODE(collision.stat().st_mode) == 0o640


def _stale_generation(root):
    name = "a" * 12
    old = root / ("g-" + name)
    old.mkdir(mode=0o700)
    locator = old / "s"
    with socket.socket(socket.AF_UNIX) as stale:
        stale.bind(os.fspath(locator))
    identity = locator.lstat().st_ino
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(secrets, "token_hex", lambda _: name)
        with (
            pytest.raises(HeinzelError, match="named generation could not be verified"),
            owned_named_generation(root),
        ):
            pytest.fail("The helper adopted a pre-existing generation")
    assert locator.lstat().st_ino == identity


def _unsolicited_peer(root):
    with owned_named_generation(root) as generation, socket.socket(socket.AF_UNIX) as foreign:
        foreign.connect(os.fspath(generation.locator))
        foreign.sendall(b"x" * 32)
        with pytest.raises(HeinzelError, match="descriptor transport is unavailable"):
            generation.check()
        foreign.settimeout(2)
        assert foreign.recv(1) == b""


def _threaded_start(root):
    finished = Event()
    worker = Thread(target=finished.wait)
    worker.start()
    try:
        with (
            pytest.raises(HeinzelError, match="named generation could not be verified"),
            owned_named_generation(root),
        ):
            pytest.fail("Named activation forked after worker startup")
        assert not list(root.iterdir())
    finally:
        finished.set()
        worker.join(timeout=2)


@pytest.mark.parametrize(
    "fixture",
    [
        "_start_check_stop",
        "_ancestor_replaced",
        "_foreign_socket_name",
        "_stale_generation",
        "_unsolicited_peer",
        "_threaded_start",
    ],
)
def test_named_generation_retains_socket_paths_and_refuses_substitution(tmp_path_factory, fixture):
    root = tmp_path_factory.mktemp("ng").resolve()
    result = subprocess.run(
        [sys.executable, "-I", "-c", _FIXTURE, __file__, fixture, os.fspath(root)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=4,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
