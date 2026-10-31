"""README timeouts close a real owned child even after its command leader exits."""

import contextlib
import os
import select
import shlex
import signal
import socket
import subprocess
import sys
import time

import pytest

from tests.e2e import docs_test

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("foreground", [False, True])
@pytest.mark.parametrize("ignore_interrupt", [False, True])
def test_cleanup_closes_owned_children_before_reaping_the_exited_leader(
    tmp_path, monkeypatch, foreground, ignore_interrupt
):
    child = "import signal,socket,sys; "
    if ignore_interrupt:
        child += "signal.signal(signal.SIGINT,signal.SIG_IGN); "
    child += "s=socket.create_connection(('127.0.0.1',int(sys.argv[1]))); s.sendall(b'R'); s.recv(1)"
    parent = (
        "import os,subprocess,sys; subprocess.Popen([sys.executable,'-I','-c',sys.argv[1],sys.argv[2]]); os._exit(0)"
    )
    real_popen = docs_test.subprocess.Popen
    connection = None
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(3)

        def started(*args, **kwargs):
            nonlocal connection
            process = real_popen(*args, **kwargs)
            connection, _ = listener.accept()
            connection.settimeout(1)
            assert connection.recv(1) == b"R"
            return process

        monkeypatch.setattr(docs_test.subprocess, "Popen", started)
        monkeypatch.setattr(docs_test, "remaining", lambda _deadline: 0.1)
        try:
            port = str(listener.getsockname()[1])
            if foreground:
                script = tmp_path / "bootstrap.py"
                script.write_text(
                    f"import os,subprocess,sys\nsubprocess.Popen([sys.executable,'-I','-c',{child!r},{port!r}])\n"
                    "os._exit(7)\n"
                )
                executable = tmp_path / "uvx"
                executable.write_text(
                    f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(script))} "$@"\n'
                )
                executable.chmod(0o700)
                with pytest.raises(AssertionError):
                    docs_test.launch_documented_app(
                        shlex.quote(str(executable)), None, tmp_path, float("inf"), bootstrap_wheels=tmp_path
                    )
            else:
                result = docs_test.run_documented_command(
                    [sys.executable, "-I", "-c", parent, child, str(listener.getsockname()[1])],
                    env=None,
                    cwd=tmp_path,
                    deadline=1,
                )
                assert result.returncode == 0
            assert connection is not None
            assert connection.recv(1) == b""
        finally:
            if connection is not None:
                # The control channel releases a leaked fixture child without signalling a reaped or unknown PID.
                with contextlib.suppress(OSError):
                    connection.sendall(b"x")
                    connection.settimeout(3)
                    connection.recv(1)
                connection.close()


def test_command_output_is_drained_with_bounded_retained_tails(tmp_path):
    code = (
        "import os; [(os.write(1,b'x'*8192),os.write(2,b'y'*8192)) for _ in range(1024)]; "
        "os.write(1,b'OUT_END'); os.write(2,b'ERR_END')"
    )
    result = docs_test.run_documented_command(
        [sys.executable, "-I", "-c", code], env=None, cwd=tmp_path, deadline=time.monotonic() + 10
    )
    assert result.returncode == 0
    assert len(result.stdout.encode()) <= docs_test.MAX_OUTPUT
    assert len(result.stderr.encode()) <= docs_test.MAX_OUTPUT
    assert result.stdout.endswith("OUT_END")
    assert result.stderr.endswith("ERR_END")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("status", [0, 7])
def test_guardian_preserves_command_exit_status(tmp_path, status):
    command = docs_test.GuardedCommand(
        [sys.executable, "-I", "-c", f"print('fixture'); raise SystemExit({status})"], env=None, cwd=tmp_path
    )
    try:
        assert command.wait(3) == status
    finally:
        command.stop()
    assert "fixture" in command.capture.text("stdout")
    assert command.returncode == status


def test_guardian_child_receives_default_sigint(tmp_path):
    command = docs_test.GuardedCommand(
        [sys.executable, "-I", "-c", "import time; print('READY',flush=True); time.sleep(60)"],
        env=None,
        cwd=tmp_path,
    )
    try:
        deadline = time.monotonic() + 3
        while "READY" not in command.capture.text("stdout") and time.monotonic() < deadline:
            command.poll_command()
            command.capture.pump(0.05)
        assert "READY" in command.capture.text("stdout")
    finally:
        command.stop()
    assert command.returncode == -signal.SIGINT


def test_guardian_kills_interrupt_ignoring_command_after_timeout(tmp_path, monkeypatch):
    code = (
        "import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); "
        "time.sleep(0.8); print('READY',flush=True); time.sleep(60)"
    )
    command = docs_test.GuardedCommand([sys.executable, "-I", "-c", code], env=None, cwd=tmp_path)
    try:
        deadline = time.monotonic() + 3
        while "READY" not in command.capture.text("stdout") and time.monotonic() < deadline:
            command.poll_command()
            command.capture.pump(0.05)
        assert "READY" in command.capture.text("stdout")
        with pytest.raises(subprocess.TimeoutExpired):
            command.wait(0.4)
        assert "READY" in command.capture.text("stdout")
    finally:
        original = command.wait
        monkeypatch.setattr(command, "wait", lambda seconds: original(min(seconds, 0.2)))
        command.stop()
    assert command.process.returncode == -signal.SIGKILL


def test_unexpected_guardian_exit_is_uncertain_and_never_signals_again(tmp_path, monkeypatch):
    command = docs_test.GuardedCommand(
        [sys.executable, "-I", "-c", "import time; time.sleep(60)"], env=None, cwd=tmp_path
    )
    try:
        deadline = time.monotonic() + 3
        while not command.ready and time.monotonic() < deadline:
            command.poll_command()
            command.capture.pump(0.05)
        assert command.ready
        os.killpg(command.process.pid, signal.SIGKILL)
        deadline = time.monotonic() + 3
        while command.alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not command.alive()
        monkeypatch.setattr(os, "killpg", lambda *_args: pytest.fail("Must not signal after guardian exit"))
        with pytest.raises(RuntimeError, match="cleanup is uncertain"):
            command.stop()
    finally:
        command.process.wait(timeout=2)


def test_guardian_start_failure_is_reported_after_owned_group_cleanup(tmp_path):
    command = docs_test.GuardedCommand([str(tmp_path / "missing-executable")], env=None, cwd=tmp_path)
    try:
        ready, _, _ = select.select([command.status_fd], [], [], 3)
        assert ready
        with pytest.raises(RuntimeError, match="could not start"):
            command.stop()
        assert command.process.returncode == -signal.SIGKILL
    finally:
        if command.process.poll() is None:
            command.process.kill()
            command.process.wait(timeout=2)


def test_guardian_observation_works_without_waitid_or_reaping(tmp_path, monkeypatch):
    monkeypatch.delattr(os, "waitid", raising=False)
    command = docs_test.GuardedCommand([sys.executable, "-I", "-c", "raise SystemExit(0)"], env=None, cwd=tmp_path)
    try:
        assert command.wait(3) == 0
        assert command.alive()
        assert command.process.returncode is None
    finally:
        command.stop()
    assert command.process.returncode == -signal.SIGKILL


def test_dead_guardian_is_not_live_when_descendant_retains_status_writer(tmp_path, monkeypatch):
    # Model the fork/exec interval before the guardian can report ready: the child still owns the status writer.
    guardian = """import os, subprocess, sys, time
fd = int(sys.argv[1])
child = subprocess.Popen(sys.argv[2:], pass_fds=(fd,))
time.sleep(60)
"""
    monkeypatch.setattr(docs_test, "_GUARDIAN", guardian)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(3)
    child = (
        "import socket,sys; s=socket.create_connection(('127.0.0.1',int(sys.argv[1]))); "
        "s.sendall(b'R'); s.recv(1); s.close()"
    )
    command = docs_test.GuardedCommand(
        [sys.executable, "-I", "-c", child, str(listener.getsockname()[1])], env=None, cwd=tmp_path
    )
    connection = None
    original_killpg = os.killpg
    try:
        connection, _address = listener.accept()
        connection.settimeout(3)
        assert connection.recv(1) == b"R"
        assert not command.ready
        os.kill(command.process.pid, signal.SIGKILL)
        deadline = time.monotonic() + 3
        while command.alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not command.alive()
        assert command.process.returncode is None
        watcher = select.poll()
        watcher.register(command.status_fd, select.POLLIN | select.POLLHUP | select.POLLERR)
        assert not any(flags & (select.POLLHUP | select.POLLERR | select.POLLNVAL) for _, flags in watcher.poll(0))
        monkeypatch.setattr(os, "killpg", lambda *_args: pytest.fail("Must not signal after guardian exit"))
        with pytest.raises(RuntimeError, match="cleanup is uncertain"):
            command.stop()
        connection.sendall(b"X")
        assert connection.recv(1) == b""
    finally:
        if connection is not None:
            with contextlib.suppress(OSError):
                connection.sendall(b"X")
            connection.close()
        listener.close()
        if command.process.returncode is None:
            if command.alive():
                original_killpg(command.process.pid, signal.SIGKILL)
            command.process.wait(timeout=3)


@pytest.mark.parametrize("problem", ["denied", "unknown-status"])
def test_unknown_guardian_identity_fails_closed_without_group_signal(tmp_path, monkeypatch, problem):
    import psutil

    command = docs_test.GuardedCommand(
        [sys.executable, "-I", "-c", "import time; time.sleep(60)"], env=None, cwd=tmp_path
    )
    original_killpg = os.killpg
    try:
        assert command.guardian is not None

        def denied():
            raise psutil.AccessDenied(command.process.pid)

        if problem == "denied":
            monkeypatch.setattr(command.guardian, "is_running", denied)
        else:
            monkeypatch.setattr(command.guardian, "status", lambda: "unknown")
        monkeypatch.setattr(os, "killpg", lambda *_args: pytest.fail("Must not signal uncertain guardian"))
        with pytest.raises(RuntimeError, match="cleanup is uncertain"):
            command.stop()
        assert command.process.returncode is None
    finally:
        if command.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                original_killpg(command.process.pid, signal.SIGKILL)
            command.process.wait(timeout=3)
