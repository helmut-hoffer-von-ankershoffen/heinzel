"""Exercise the isolated CLI reviewer with a private socket and a real PTY."""

import hashlib
import json
import os
import pty
import re
import select
import socket
import subprocess
import sys
import termios
import time
from contextlib import suppress
from dataclasses import replace

import pytest

from heinzel.heads.mcp._ipc_cli_review import _REVIEWER, CliReviewRuntime
from heinzel.kernel._approval import arguments_hash

pytestmark = pytest.mark.unit

_LAUNCHER = """
import fcntl, os, socket, sys, termios
from pathlib import Path
from heinzel import HeinzelError
from heinzel.heads.mcp import _ipc_cli_review as cli
if sys.argv[1] == 'tty':
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
if sys.argv[1] == 'replace':
    cli._REVIEWER = Path.cwd() / 'reviewer.py'
    verify = cli._verified_source
    def replace_after_verification(path, digest):
        source = verify(path, digest)
        (Path.cwd() / 'substitute.py').replace(path)
        return source
    cli._verified_source = replace_after_verification
with socket.socket(fileno=int(sys.argv[2])) as channel:
    try:
        accepted = cli.run_cli_host_reviewer(channel, cli.CliReviewRuntime(sys.argv[3]))
    except HeinzelError:
        raise SystemExit(2) from None
    assert channel.fileno() == -1
raise SystemExit(0 if accepted else 1)
"""


@pytest.fixture
def runtime():
    return CliReviewRuntime(hashlib.sha256(_REVIEWER.read_bytes()).hexdigest())


@pytest.fixture
def offer():
    review = {
        "instance": "pending-fixture",
        "challenge": "broker-challenge-hidden",
        "registration": "fixture-registration",
        "generation": 3,
        "terms": {"targets": ["personal\x1b[2J\u202e"], "result_read": True, "usd_limit": 0},
    }
    digest = arguments_hash(review)
    return json.dumps({"review": review, "digest": digest}, ensure_ascii=True).encode() + b"\n", digest


def launch_fixture(channel, runtime, cwd, *, terminal=None, mode=""):
    fixture_mode = mode or ("tty" if terminal is not None else "none")
    return subprocess.Popen(
        [
            sys.executable,
            "-E" if fixture_mode == "unisolated" else "-I",
            "-c",
            _LAUNCHER,
            fixture_mode,
            str(channel.fileno()),
            runtime.reviewer_sha256,
        ],
        cwd=cwd,
        env=os.environ | {"PYTHONPATH": os.fspath(cwd), "PYTHONSTARTUP": os.fspath(cwd / "sitecustomize.py")},
        pass_fds=(channel.fileno(),),
        stdin=terminal if terminal is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def read_prompt(terminal):
    deadline = time.monotonic() + 2
    output = bytearray()
    while b"\r\n> " not in output:
        remaining = deadline - time.monotonic()
        assert remaining > 0
        assert select.select([terminal], [], [], remaining)[0]
        output.extend(os.read(terminal, 4096))
    return bytes(output)


@pytest.mark.parametrize("approve", [False, True], ids=["decline", "exact-confirmation"])
def test_inherited_reviewer_requires_exact_foreground_terminal_answer(tmp_path, runtime, offer, approve):
    marker = tmp_path / "injected"
    injection = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    (tmp_path / "sitecustomize.py").write_text(injection)
    (tmp_path / "json.py").write_text(injection)
    master, terminal = pty.openpty()
    settings = termios.tcgetattr(terminal)
    settings[3] &= ~termios.ECHO
    termios.tcsetattr(terminal, termios.TCSANOW, settings)
    broker, channel = socket.socketpair()
    payload, digest = offer
    try:
        with broker, channel:
            broker.settimeout(2)
            broker.sendall(payload)
            with launch_fixture(channel, runtime, tmp_path, terminal=terminal) as process:
                os.close(terminal)
                terminal = -1
                channel.close()
                try:
                    prompt = read_prompt(master)
                    assert b"pending-fixture" in prompt
                    assert b"personal\\u001b[2J\\u202e" in prompt
                    assert b"broker-challenge-hidden" not in prompt
                    assert digest.encode() not in prompt
                    match = re.search(rb"Type exactly: (approve MCP [0-9a-f]{8})\r\n", prompt)
                    assert match is not None
                    os.write(master, (match[1] if approve else b"yes") + b"\n")
                    with broker.makefile("rb") as response:
                        assert response.readline() == (f"approve {digest}\n".encode() if approve else b"")
                        assert response.read(1) == b""
                    assert process.communicate(timeout=2) == (b"", b"")
                    assert process.returncode == (0 if approve else 1)
                    assert not marker.exists()
                finally:
                    if process.poll() is None:
                        os.close(master)  # Unblock Darwin's controlling-terminal drain before reaping the fixture.
                        master = -1
                        process.kill()
    finally:
        if master >= 0:
            os.close(master)
        if terminal >= 0:
            os.close(terminal)


def test_same_uid_without_a_controlling_terminal_cannot_send_a_reply(tmp_path, runtime, offer):
    broker, channel = socket.socketpair()
    with broker, channel:
        broker.sendall(offer[0])
        with launch_fixture(channel, runtime, tmp_path) as process:
            channel.close()
            try:
                assert process.communicate(timeout=2) == (b"", b"")
                assert process.returncode == 1
                # An unread offer may produce a reset when the other end closes. Neither form contains a reply.
                broker.settimeout(2)
                with suppress(ConnectionResetError):
                    assert broker.recv(1) == b""
            finally:
                if process.poll() is None:
                    process.kill()


def test_changed_reviewer_digest_refuses_before_use(tmp_path, runtime):
    broker, channel = socket.socketpair()
    with broker, channel, launch_fixture(channel, replace(runtime, reviewer_sha256="0" * 64), tmp_path) as process:
        channel.close()
        assert process.communicate(timeout=2) == (b"", b"mcp.ipc.load_reviewer\n")
        assert process.returncode == 2
        broker.settimeout(2)
        assert broker.recv(1) == b""


def test_unisolated_runtime_refuses_before_review(tmp_path, runtime):
    broker, channel = socket.socketpair()
    with broker, channel, launch_fixture(channel, runtime, tmp_path, mode="unisolated") as process:
        channel.close()
        assert process.communicate(timeout=2) == (b"", b"mcp.ipc.load_reviewer\n")
        assert process.returncode == 2
        broker.settimeout(2)
        assert broker.recv(1) == b""


def test_reviewer_replacement_after_digest_check_cannot_reply_without_terminal(tmp_path, runtime, offer):
    candidate = tmp_path / "reviewer.py"
    candidate.write_bytes(_REVIEWER.read_bytes())
    marker = tmp_path / "replacement-executed"
    payload, digest = offer
    replacement = (
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
        f"def review(channel):\n    channel.sendall({f'approve {digest}\n'.encode()!r})\n    return True\n"
    )
    (tmp_path / "substitute.py").write_text(replacement)
    broker, channel = socket.socketpair()
    with broker, channel:
        broker.sendall(payload)
        with launch_fixture(channel, runtime, tmp_path, mode="replace") as process:
            channel.close()
            assert process.communicate(timeout=2) == (b"", b"")
            assert process.returncode == 1
            broker.settimeout(2)
            with suppress(ConnectionResetError):
                assert broker.recv(1) == b""
    assert candidate.read_text() == replacement
    assert not marker.exists()
