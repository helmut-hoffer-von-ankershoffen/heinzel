"""Exercise the exact isolated reviewer launch with inherited sockets and a real PTY."""

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
from pathlib import Path

import pytest

from heinzel import HeinzelError
from heinzel.heads.mcp._ipc_cli_review import _REVIEWER, CliReviewRuntime, cli_host_reviewer
from heinzel.kernel._approval import arguments_hash

pytestmark = pytest.mark.unit

_LAUNCHER = """
import fcntl, os, socket, sys, termios
from pathlib import Path
from heinzel.heads.mcp._ipc_cli_review import CliReviewRuntime, cli_host_reviewer
if sys.argv[1] == 'tty':
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
os.environ['PYTHONPATH'] = os.getcwd()
os.environ['PYTHONSTARTUP'] = os.path.join(os.getcwd(), 'sitecustomize.py')
with socket.socket(fileno=int(sys.argv[2])) as channel:
    runtime = CliReviewRuntime(Path(sys.argv[3]), sys.argv[4], sys.argv[5])
    with cli_host_reviewer(channel, runtime) as process:
        assert channel.fileno() == -1
        status = process.wait(timeout=2)
raise SystemExit(status)
"""


@pytest.fixture
def runtime():
    interpreter = Path(sys.executable).resolve()
    return CliReviewRuntime(
        interpreter,
        hashlib.sha256(interpreter.read_bytes()).hexdigest(),
        hashlib.sha256(_REVIEWER.read_bytes()).hexdigest(),
    )


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


def launch_fixture(channel, runtime, cwd, *, terminal=None):
    return subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            _LAUNCHER,
            "tty" if terminal is not None else "none",
            str(channel.fileno()),
            str(runtime.interpreter),
            runtime.interpreter_sha256,
            runtime.reviewer_sha256,
        ],
        cwd=cwd,
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


def test_changed_reviewer_digest_refuses_before_descriptor_delivery(runtime):
    broker, channel = socket.socketpair()
    with (
        broker,
        channel,
        pytest.raises(HeinzelError, match="pinned local MCP reviewer is unavailable"),
        cli_host_reviewer(channel, replace(runtime, reviewer_sha256="0" * 64)),
    ):
        pytest.fail("A changed reviewer received the private descriptor")
